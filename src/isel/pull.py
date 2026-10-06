"""Read-only pulls from crawlerdb: the split table, a block sample and golden lookups.

Every statement is a single read through `ReadOnlySession`, paced by
`pull.min_interval_s`. The sample is one `COPY ... TABLESAMPLE SYSTEM` per
shard, so each statement reads about `sample_percent` of the shard's pages and
never sorts or aggregates on the server.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from isel.config import Config
from isel.io import AtomicParquetWriter, atomic_write_json, read_json, write_parquet_atomic
from isel.pg import ReadOnlySession
from isel.urlnorm import ShardRouter

BOOL, INT2, INT4, INT8, TEXT, FLOAT8, TSTZ = 16, 21, 23, 20, 25, 701, 1184
_ARROW = {BOOL: pa.bool_(), INT2: pa.int16(), INT4: pa.int32(), INT8: pa.int64(),
          TEXT: pa.string(), FLOAT8: pa.float64(), TSTZ: pa.timestamp("us", tz="UTC")}

# (column, SQL expression, type OID). Every expression casts, so the binary COPY
# types are fixed without a catalog lookup. `url_score` and `source` are pulled
# only for the forward-only baselines; `features.FORBIDDEN` keeps them out of models.
CRAWL_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("url", "url::text", TEXT),
    ("domain_id", "domain_id::int8", INT8),
    ("last_fetch_ok", "last_fetch_ok::timestamptz", TSTZ),
    ("title_len", "char_length(title)::int4", INT4),
    ("has_anchor", "(anchor_text IS NOT NULL)", BOOL),
    ("discovery_source_type", "discovery_source_type::int2", INT2),
    ("robots_bits", "robots_bits::int2", INT2),
    ("is_redirect", "is_redirect::bool", BOOL),
    ("redirect_hop_count", "redirect_hop_count::int4", INT4),
    ("has_json_ld", "has_json_ld::bool", BOOL),
    ("hreflang_count", "hreflang_count::int4", INT4),
    ("has_last_modified", "(last_modified IS NOT NULL)", BOOL),
    ("has_cache_control", "(cache_control IS NOT NULL)", BOOL),
    ("inlink_count_approx", "inlink_count_approx::int8", INT8),
    ("inlink_count_external", "inlink_count_external::int8", INT8),
    ("url_score", "url_score::float8", FLOAT8),
    ("source", "source::int2", INT2),
)
CRAWL_SCHEMA = pa.schema(
    [(name, _ARROW[oid]) for name, _, oid in CRAWL_COLUMNS] + [("shard", pa.int16())]
)
_SELECT_LIST = ", ".join(f"{expr} AS {name}" for name, expr, _ in CRAWL_COLUMNS)
_OIDS = [oid for _, _, oid in CRAWL_COLUMNS]
_BATCH_ROWS = 200_000


def _table(rows: Sequence[tuple], shard: int) -> pa.Table:
    columns = list(zip(*rows, strict=True)) if rows else [[] for _ in CRAWL_COLUMNS]
    arrays = [pa.array(col, type=CRAWL_SCHEMA.field(i).type) for i, col in enumerate(columns)]
    arrays.append(pa.array([shard] * len(rows), pa.int16()))
    return pa.Table.from_arrays(arrays, schema=CRAWL_SCHEMA)


def _shards(cfg: Config, shards: Iterable[int] | None) -> list[int]:
    chosen = list(shards) if shards else list(cfg.pull.shards)
    return chosen or list(range(cfg.crawlerdb.num_shards))


def _table_name(cfg: Config, shard: int) -> str:
    return cfg.crawlerdb.url_table_fmt.format(shard)


def pull_dims(cfg: Config, session: ReadOnlySession, out_dir: Path) -> dict[str, Any]:
    """Writes the split-subdomain whitelist (host, migrated_at)."""
    rows = session.fetchall(
        f"SELECT host::text, migrated_at::timestamptz FROM {cfg.crawlerdb.split_table}")
    table = pa.table({"host": [r[0] for r in rows], "migrated_at": [r[1] for r in rows]},
                     schema=pa.schema([("host", pa.string()),
                                       ("migrated_at", pa.timestamp("us", tz="UTC"))]))
    write_parquet_atomic(table, out_dir / "split_hosts.parquet")
    return {"split_hosts": table.num_rows,
            "pending": sum(r[1] is None for r in rows)}


def sample_sql(cfg: Config, shard: int) -> str:
    return (f"COPY (SELECT {_SELECT_LIST} FROM {_table_name(cfg, shard)} "
            f"TABLESAMPLE SYSTEM ({cfg.pull.sample_percent!r}) "
            f"REPEATABLE ({int(cfg.pull.sample_seed)}) "
            f"WHERE last_fetch_ok IS NOT NULL) TO STDOUT (FORMAT BINARY)")


def estimate_sample(cfg: Config, session: ReadOnlySession,
                    shards: Iterable[int] | None = None) -> dict[str, Any]:
    """Reads only `pg_class` to estimate rows and pages the sample will touch."""
    rows = pages = 0
    for shard in _shards(cfg, shards):
        r = session.fetchone("SELECT reltuples::int8, relpages::int8 FROM pg_class "
                             "WHERE oid = to_regclass(%s)", (_table_name(cfg, shard),))
        if r is not None:
            rows += max(int(r[0]), 0)
            pages += int(r[1])
    rate = cfg.sample_rate
    return {"table_rows": rows, "table_pages": pages, "sample_rows_upper": int(rows * rate),
            "sample_pages": int(pages * rate), "sample_heap_bytes": int(pages * rate * 8192)}


def pull_sample(cfg: Config, session: ReadOnlySession, out_dir: Path,
                shards: Iterable[int] | None = None) -> dict[str, Any]:
    """Writes `shard=NNN.parquet` per shard plus `_meta.json`; skips finished shards.

    `_meta.json` keeps the first start time as `snapshot_at` (S0). Pools are
    filtered by `last_fetch_ok < as_of <= S0`, so a shard read later in the pull
    cannot add rows that a forward evaluation must not see.
    """
    meta_path = out_dir / "_meta.json"
    meta = read_json(meta_path) or {}
    if meta and (meta.get("sample_percent") != cfg.pull.sample_percent
                 or meta.get("sample_seed") != cfg.pull.sample_seed):
        raise RuntimeError("existing sample used a different sample_percent or sample_seed")
    meta.setdefault("snapshot_at", datetime.now(UTC).isoformat())
    meta.update(sample_percent=cfg.pull.sample_percent, sample_seed=cfg.pull.sample_seed)
    done = set(meta.get("shards_done", []))
    for shard in _shards(cfg, shards):
        final = out_dir / f"shard={shard:03d}.parquet"
        if shard in done and final.exists():
            continue
        with AtomicParquetWriter(final, CRAWL_SCHEMA) as writer:
            buffer: list[tuple] = []
            for row in session.copy_rows(sample_sql(cfg, shard), _OIDS):
                buffer.append(row)
                if len(buffer) >= _BATCH_ROWS:
                    writer.write(_table(buffer, shard))
                    buffer = []
            writer.write(_table(buffer, shard))
        done.add(shard)
        meta["shards_done"] = sorted(done)
        meta.setdefault("rows", {})[str(shard)] = writer.rows
        atomic_write_json(meta_path, meta)
    meta["finished_at"] = datetime.now(UTC).isoformat()
    atomic_write_json(meta_path, meta)
    return {"shards_done": len(done), "rows": sum(meta.get("rows", {}).values())}


def probes(router: ShardRouter, urls: Iterable[str]) -> dict[int, list[str]]:
    """Groups each spelling under every shard that may hold it."""
    by_shard: dict[int, set[str]] = defaultdict(set)
    for url in urls:
        for shard in router.candidate_shards(url):
            by_shard[shard].add(url)
    return {s: sorted(u) for s, u in sorted(by_shard.items())}


def lookup_rows(cfg: Config, session: ReadOnlySession, router: ShardRouter,
                urls: Iterable[str]) -> pa.Table:
    """Primary-key lookups of `urls` in their candidate shards (crawled or not)."""
    tables = []
    chunk = cfg.pull.lookup_chunk
    for shard, items in probes(router, urls).items():
        text = f"SELECT {_SELECT_LIST} FROM {_table_name(cfg, shard)} WHERE url = ANY(%s)"
        for i in range(0, len(items), chunk):
            rows = session.fetchall(text, (items[i:i + chunk],))
            if rows:
                tables.append(_table(rows, shard))
    return pa.concat_tables(tables) if tables else CRAWL_SCHEMA.empty_table()


def pull_positives(cfg: Config, session: ReadOnlySession, router: ShardRouter,
                   golden: pa.Table, out_dir: Path, batches: Iterable[int] | None = None,
                   force: bool = False) -> dict[str, Any]:
    """Looks up raw and canonical spellings of each batch's golden URLs.

    Writes `batch=<id>.parquet` per batch; an existing file is kept unless `force`.
    """
    rows = golden.select(["batch_id", "raw_url", "canonical_url"]).to_pylist()
    wanted = set(batches) if batches else {r["batch_id"] for r in rows}
    report: dict[str, Any] = {}
    for batch in sorted(wanted):
        final = out_dir / f"batch={batch}.parquet"
        if final.exists() and not force:
            continue
        spellings = {u for r in rows if r["batch_id"] == batch
                     for u in (r["raw_url"], r["canonical_url"])}
        table = lookup_rows(cfg, session, router, spellings)
        write_parquet_atomic(table, final)
        found = set(table.column("url").to_pylist())
        report[str(batch)] = {
            "spellings": len(spellings), "found_rows": table.num_rows,
            "found_spellings": len(found & spellings),
            "crawled_rows": table.num_rows - table.column("last_fetch_ok").null_count,
        }
    if report:
        old = read_json(out_dir / "_report.json") or {}
        atomic_write_json(out_dir / "_report.json", {**old, **report})
    return report
