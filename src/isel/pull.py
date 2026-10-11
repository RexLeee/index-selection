"""Read-only pulls from crawlerdb: split table, sample, golden lookups, full pool.

Every statement is a single read through `ReadOnlySession`, paced by
`pull.min_interval_s` and held by the read guard. The sample is one
`COPY ... TABLESAMPLE SYSTEM` per shard, so each statement reads about
`sample_percent` of the shard's pages. The full export reads each shard in
ctid block ranges, one short `COPY ... (FORMAT CSV)` per range; pyarrow parses
the CSV in C++ and a worker thread writes it while the next range streams. No
statement sorts or aggregates on the server.
"""

from __future__ import annotations

import logging
import math
import shutil
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq
from psycopg import errors

from isel.config import Config
from isel.features import UNLOADED_COLUMNS
from isel.io import AtomicParquetWriter, atomic_write_json, read_json, write_parquet_atomic
from isel.pg import ReadOnlySession
from isel.urlnorm import ShardRouter

BOOL, INT2, INT4, INT8, TEXT, FLOAT8, TSTZ = 16, 21, 23, 20, 25, 701, 1184
_ARROW = {BOOL: pa.bool_(), INT2: pa.int16(), INT4: pa.int32(), INT8: pa.int64(),
          TEXT: pa.string(), FLOAT8: pa.float64(), TSTZ: pa.timestamp("us", tz="UTC")}

# (column, SQL expression, type OID). Every expression casts, so the binary COPY
# types are fixed without a catalog lookup. `url_score` and `source` are pulled
# only for the forward-only baselines and `num_fetch_ok_90d` only to check that a
# URL is fetched once; `features.FORBIDDEN` keeps all three out of models. `title`
# is kept for later text features; `{title_chars}` is filled from the config.
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
    ("num_fetch_ok_90d", "num_fetch_ok_90d::int4", INT4),
    ("title", "left(title, {title_chars})::text", TEXT),
)
CRAWL_SCHEMA = pa.schema(
    [(name, _ARROW[oid]) for name, _, oid in CRAWL_COLUMNS] + [("shard", pa.int16())]
)
_OIDS = [oid for _, _, oid in CRAWL_COLUMNS]
# The full export drops what FeatureStore never loads, so its rows score like sample rows.
# It reads CSV: pyarrow parses it about 15x faster than psycopg decodes binary rows, and
# the server writes CSV no slower. A naive UTC timestamp keeps the text zone-free.
_CSV_EXPRESSIONS = {"last_fetch_ok": "(last_fetch_ok AT TIME ZONE 'UTC')"}
FULL_COLUMNS = tuple((name, _CSV_EXPRESSIONS.get(name, expr), oid)
                     for name, expr, oid in CRAWL_COLUMNS if name not in UNLOADED_COLUMNS)
FULL_SCHEMA = pa.schema(
    [(name, _ARROW[oid]) for name, _, oid in FULL_COLUMNS] + [("shard", pa.int16())]
)
_CSV_NAMES = [name for name, _, _ in FULL_COLUMNS]
# PostgreSQL CSV: NULL is an unquoted empty field and an empty string is "".
_CSV_CONVERT = pcsv.ConvertOptions(
    column_types={name: (pa.timestamp("us") if oid == TSTZ else _ARROW[oid])
                  for name, _, oid in FULL_COLUMNS},
    true_values=["t"], false_values=["f"], null_values=[""],
    strings_can_be_null=True, quoted_strings_can_be_null=False)
_CSV_PARSE = pcsv.ParseOptions(newlines_in_values=True)
_BATCH_ROWS = 200_000
PAGE_BYTES = 8192
# A shard that hits its timeout or lock_timeout is retried on the next run.
_RETRYABLE = (errors.QueryCanceled, errors.LockNotAvailable)

log = logging.getLogger(__name__)


def _table(rows: Sequence[tuple], shard: int) -> pa.Table:
    width = len(CRAWL_SCHEMA) - 1
    columns = list(zip(*rows, strict=True)) if rows else [[] for _ in range(width)]
    arrays = [pa.array(col, type=CRAWL_SCHEMA.field(i).type) for i, col in enumerate(columns)]
    arrays.append(pa.array([shard] * len(rows), pa.int16()))
    return pa.Table.from_arrays(arrays, schema=CRAWL_SCHEMA)


def _shards(cfg: Config, shards: Iterable[int] | None) -> list[int]:
    chosen = list(shards) if shards else list(cfg.pull.shards)
    return chosen or list(range(cfg.crawlerdb.num_shards))


def _table_name(cfg: Config, shard: int) -> str:
    return cfg.crawlerdb.url_table_fmt.format(shard)


def _select_list(cfg: Config, columns: Sequence[tuple[str, str, int]] = CRAWL_COLUMNS) -> str:
    return ", ".join(f"{expr.format(title_chars=int(cfg.pull.title_chars))} AS {name}"
                     for name, expr, _ in columns)


def _table_size(cfg: Config, session: ReadOnlySession, shard: int) -> tuple[int, int]:
    """(reltuples, relpages) of a shard table from `pg_class`; (0, 0) if it is missing."""
    r = session.fetchone("SELECT reltuples::int8, relpages::int8 FROM pg_class "
                         "WHERE oid = to_regclass(%s)", (_table_name(cfg, shard),))
    return (max(int(r[0]), 0), int(r[1])) if r is not None else (0, 0)


def _timeout_for_bytes(cfg: Config, heap_bytes: float) -> int:
    g = cfg.guard
    seconds = math.ceil(heap_bytes / (g.min_mb_s * 1_000_000))
    return min(max(seconds, g.timeout_floor_s), g.timeout_cap_s)


def timeout_for(cfg: Config, relpages: int) -> int:
    """Statement timeout for sampling a table of `relpages` at the worst-case read speed."""
    return _timeout_for_bytes(cfg, relpages * cfg.sample_rate * PAGE_BYTES)


def probe_frozen(cfg: Config, session: ReadOnlySession, now: datetime) -> dict[str, Any]:
    """Server start time and cumulative row writes on the URL tables.

    Two equal probes mean no row of `url_state_current_*` was inserted, updated or
    deleted in between, so a sample taken earlier also describes the later time.
    """
    prefix = cfg.crawlerdb.url_table_fmt.split("{", 1)[0]
    started, changes = session.fetchone(
        "SELECT pg_postmaster_start_time(), "
        "coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0)::int8 "
        "FROM pg_stat_user_tables WHERE starts_with(relname, %s)", (prefix,))
    return {"postmaster_start": started.isoformat(), "tuple_changes": int(changes),
            "probed_at": now.isoformat()}


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
    return (f"COPY (SELECT {_select_list(cfg)} FROM {_table_name(cfg, shard)} "
            f"TABLESAMPLE SYSTEM ({cfg.pull.sample_percent!r}) "
            f"REPEATABLE ({int(cfg.pull.sample_seed)}) "
            f"WHERE last_fetch_ok IS NOT NULL) TO STDOUT (FORMAT BINARY)")


def estimate_sample(cfg: Config, session: ReadOnlySession,
                    shards: Iterable[int] | None = None) -> dict[str, Any]:
    """Reads only `pg_class` to estimate rows, pages and the timeout of every shard."""
    per_shard: dict[int, dict[str, int]] = {}
    for shard in _shards(cfg, shards):
        rows, pages = _table_size(cfg, session, shard)
        per_shard[shard] = {"rows": rows, "pages": pages, "timeout_s": timeout_for(cfg, pages)}
    rate = cfg.sample_rate
    pages = sum(v["pages"] for v in per_shard.values())
    timeouts = {s: v["timeout_s"] for s, v in per_shard.items()}
    slowest = max(timeouts, key=timeouts.get) if timeouts else None
    return {"table_rows": sum(v["rows"] for v in per_shard.values()), "table_pages": pages,
            "sample_rows_upper": int(sum(v["rows"] for v in per_shard.values()) * rate),
            "sample_pages": int(pages * rate),
            "sample_heap_bytes": int(pages * rate * PAGE_BYTES),
            "timeout_s_sum": sum(timeouts.values()),
            "timeout_s_max": timeouts.get(slowest), "slowest_shard": slowest,
            "shards": {str(s): v for s, v in per_shard.items()}}


def _pull_shard(cfg: Config, session: ReadOnlySession, out_dir: Path, shard: int) -> int:
    """Streams one shard's sample into `shard=NNN.parquet`; returns its row count."""
    session.set_statement_timeout(timeout_for(cfg, _table_size(cfg, session, shard)[1]))
    with AtomicParquetWriter(out_dir / f"shard={shard:03d}.parquet", CRAWL_SCHEMA) as writer:
        buffer: list[tuple] = []
        for row in session.copy_rows(sample_sql(cfg, shard), _OIDS):
            buffer.append(row)
            if len(buffer) >= _BATCH_ROWS:
                writer.write(_table(buffer, shard))
                buffer = []
        writer.write(_table(buffer, shard))
    return writer.rows


def pull_sample(cfg: Config, session: ReadOnlySession, out_dir: Path,
                shards: Iterable[int] | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    """Writes `shard=NNN.parquet` per shard plus `_meta.json`; skips finished shards.

    `_meta.json` keeps the first start time as `snapshot_at` (S0) and a
    `frozen_probe` taken at that time. Pools are filtered by
    `last_fetch_ok < as_of <= S0`, so a shard read later in the pull cannot add
    rows that a forward evaluation must not see. Each shard's statement gets a
    timeout sized to the pages it reads; a shard that times out is recorded in
    `shards_failed` and retried by the next run. `finished_at` is set only once
    every configured shard is done, whatever `shards` selects.
    """
    now = now or datetime.now(UTC)
    meta_path = out_dir / "_meta.json"
    meta = read_json(meta_path) or {}
    if meta and (meta.get("sample_percent") != cfg.pull.sample_percent
                 or meta.get("sample_seed") != cfg.pull.sample_seed):
        raise RuntimeError("existing sample used a different sample_percent or sample_seed")
    if "snapshot_at" not in meta:
        meta["snapshot_at"] = now.isoformat()
        meta["frozen_probe"] = probe_frozen(cfg, session, now)
    meta.update(sample_percent=cfg.pull.sample_percent, sample_seed=cfg.pull.sample_seed)
    done = set(meta.get("shards_done", []))
    failed: dict[str, str] = meta.get("shards_failed", {})
    todo = [s for s in _shards(cfg, shards)
            if not (s in done and (out_dir / f"shard={s:03d}.parquet").exists())]
    for i, shard in enumerate(todo, start=1):
        done.discard(shard)   # a done shard whose file is gone is pulled again
        try:
            rows = _pull_shard(cfg, session, out_dir, shard)
        except _RETRYABLE as exc:
            failed[str(shard)] = type(exc).__name__
            log.warning("shard %03d failed (%s); it is retried on the next run",
                        shard, type(exc).__name__)
        else:
            done.add(shard)
            failed.pop(str(shard), None)
            meta.setdefault("rows", {})[str(shard)] = rows
            log.info("shard %03d: %d rows (%d/%d)", shard, rows, i, len(todo))
        meta["shards_done"] = sorted(done)
        meta["shards_failed"] = failed
        atomic_write_json(meta_path, meta)
    missing = sorted(set(_shards(cfg, None)) - done)
    if missing:
        meta.pop("finished_at", None)
    else:
        meta.setdefault("finished_at", datetime.now(UTC).isoformat())
    atomic_write_json(meta_path, meta)
    return {"shards_done": len(done), "shards_missing": len(missing),
            "shards_failed": sorted(int(s) for s in failed),
            "rows": sum(meta.get("rows", {}).values())}


def advance_snapshot(cfg: Config, session: ReadOnlySession, out_dir: Path,
                     now: datetime | None = None) -> dict[str, Any]:
    """Moves S0 to now if crawlerdb has not changed since the sample started.

    Raises:
        RuntimeError: If the sample is unfinished or the frozen probe differs.
    """
    now = now or datetime.now(UTC)
    meta_path = out_dir / "_meta.json"
    meta = read_json(meta_path) or {}
    if "finished_at" not in meta or "frozen_probe" not in meta:
        raise RuntimeError("the sample is not finished; run pull-sample until no shard is missing")
    probe = probe_frozen(cfg, session, now)
    first = meta["frozen_probe"]
    if any(probe[k] != first[k] for k in ("postmaster_start", "tuple_changes")):
        raise RuntimeError(f"crawlerdb changed since {meta['snapshot_at']}; S0 is not moved "
                           f"(was {first}, now {probe})")
    previous = meta["snapshot_at"]
    meta.setdefault("snapshot_initial", previous)
    meta["snapshot_at"] = probe["probed_at"]
    meta["snapshot_probe"] = probe
    atomic_write_json(meta_path, meta)
    return {"snapshot_at": meta["snapshot_at"], "previous": previous}


def probes(router: ShardRouter, urls: Iterable[str]) -> dict[int, list[str]]:
    """Groups each spelling under every shard that may hold it."""
    by_shard: dict[int, set[str]] = defaultdict(set)
    for url in urls:
        for shard in router.candidate_shards(url):
            by_shard[shard].add(url)
    return {s: sorted(u) for s, u in sorted(by_shard.items())}


def lookup_sql(cfg: Config, shard: int) -> str:
    return f"SELECT {_select_list(cfg)} FROM {_table_name(cfg, shard)} WHERE url = ANY(%s)"


def lookup_rows(cfg: Config, session: ReadOnlySession, router: ShardRouter,
                urls: Iterable[str]) -> pa.Table:
    """Primary-key lookups of `urls` in their candidate shards (crawled or not)."""
    tables = []
    chunk = cfg.pull.lookup_chunk
    for shard, items in probes(router, urls).items():
        text = lookup_sql(cfg, shard)
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


def _relation_blocks(cfg: Config, session: ReadOnlySession, shard: int) -> int:
    """Current heap size of a shard table in blocks (not the stale `relpages`); 0 if missing."""
    (blocks,) = session.fetchone(
        "SELECT coalesce(pg_relation_size(to_regclass(%s)), 0) "
        "/ current_setting('block_size')::int8", (_table_name(cfg, shard),))
    return int(blocks)


def full_sql(cfg: Config, shard: int, lo: int, hi: int) -> str:
    """One COPY over heap blocks [lo, hi): a TID range scan that reads only those blocks."""
    return (f"COPY (SELECT {_select_list(cfg, FULL_COLUMNS)} FROM {_table_name(cfg, shard)} "
            f"WHERE ctid >= '({int(lo)},0)'::tid AND ctid < '({int(hi)},0)'::tid "
            "AND last_fetch_ok IS NOT NULL) TO STDOUT (FORMAT CSV)")


def _csv_table(data: bytes | bytearray, shard: int) -> pa.Table:
    """Parses one chunk of `full_sql` output into `FULL_SCHEMA` rows of `shard`."""
    if not data:
        return FULL_SCHEMA.empty_table()
    table = pcsv.read_csv(pa.BufferReader(data), pcsv.ReadOptions(column_names=_CSV_NAMES),
                          _CSV_PARSE, _CSV_CONVERT)
    table = table.append_column("shard", pa.repeat(pa.scalar(shard, pa.int16()),
                                                   table.num_rows))
    return table.cast(FULL_SCHEMA)


def estimate_full(cfg: Config, session: ReadOnlySession,
                  shards: Iterable[int] | None = None) -> dict[str, Any]:
    """Reads only catalog sizes: heap bytes, COPY statements and worst-case hours."""
    blocks = {s: _relation_blocks(cfg, session, s) for s in _shards(cfg, shards)}
    heap = sum(blocks.values()) * PAGE_BYTES
    return {
        "shards": len(blocks), "heap_bytes": heap,
        "chunks": sum(math.ceil(b / cfg.full.chunk_blocks) for b in blocks.values()),
        "chunk_timeout_s": _timeout_for_bytes(cfg, cfg.full.chunk_blocks * PAGE_BYTES),
        "hours_at_min_mb_s": round(heap / (cfg.guard.min_mb_s * 1_000_000) / 3600, 1),
        "largest_shard": max(blocks, key=blocks.get) if blocks else None,
        "blocks": {str(s): b for s, b in blocks.items()},
    }


def _check_disk(cfg: Config, path: Path) -> None:
    free = shutil.disk_usage(path).free
    if free < cfg.full.min_free_gb * 1e9:
        raise RuntimeError(f"only {free / 1e9:.0f} GB free under {path}; "
                           f"full.min_free_gb is {cfg.full.min_free_gb:.0f}")


def _export_file(cfg: Config, session: ReadOnlySession, final: Path, shard: int,
                 lo: int, hi: int) -> int:
    """Streams blocks [lo, hi) of one shard into `final`, one COPY per chunk.

    A worker thread parses and writes each chunk while the next COPY streams;
    pyarrow releases the GIL, so the server rarely waits for this client.
    """
    span = cfg.full.chunk_blocks
    with AtomicParquetWriter(final, FULL_SCHEMA) as writer, ThreadPoolExecutor(1) as pool:
        pending = None
        for c_lo in range(lo, hi, span):
            c_hi = min(c_lo + span, hi)
            session.set_statement_timeout(_timeout_for_bytes(cfg, (c_hi - c_lo) * PAGE_BYTES))
            data = session.copy_bytes(full_sql(cfg, shard, c_lo, c_hi))
            if pending is not None:
                pending.result()
            pending = pool.submit(lambda d=data: writer.write(_csv_table(d, shard)))
        if pending is not None:
            pending.result()
    return writer.rows


def _export_shard(cfg: Config, session: ReadOnlySession, out_dir: Path, shard: int,
                  extents: list[list[int]], save: Any) -> int:
    """Exports every extent of a shard as files of `chunks_per_file` chunks; skips done files.

    Pages appended while the export runs become one more extent, so rows that
    moved to the end of the heap are still read.
    """
    span = cfg.full.chunk_blocks * cfg.full.chunks_per_file
    shard_dir = out_dir / f"shard={shard:03d}"
    rows, i = 0, 0
    while i < len(extents):
        lo, hi = extents[i]
        for f_lo in range(lo, hi, span):
            f_hi = min(f_lo + span, hi)
            final = shard_dir / f"part-{f_lo:010d}-{f_hi:010d}.parquet"
            if final.exists():
                rows += pq.ParquetFile(final).metadata.num_rows
                continue
            _check_disk(cfg, out_dir)
            rows += _export_file(cfg, session, final, shard, f_lo, f_hi)
        i += 1
        if i == len(extents):
            grown = _relation_blocks(cfg, session, shard)
            if grown > extents[-1][1]:
                extents.append([extents[-1][1], grown])
                save()
    return rows


def pull_full(cfg: Config, session: ReadOnlySession, out_dir: Path,
              shards: Iterable[int] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Exports every crawled row into `shard=NNN/part-<lo>-<hi>.parquet`, resumably.

    The first visit of a shard records its heap size in `_meta.json` as a block
    extent; a resumed run reuses it, so file ranges never move. A shard whose
    COPY hits its timeout or lock_timeout is recorded in `shards_failed` and
    resumed by the next run at file granularity. `finished_at` is set once every
    configured shard is done. The pool filter `last_fetch_ok < as_of` drops rows
    fetched after the sample's S0, so a later export still scores consistently.
    """
    now = now or datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "_meta.json"
    meta = read_json(meta_path) or {}
    layout = {"chunk_blocks": cfg.full.chunk_blocks, "chunks_per_file": cfg.full.chunks_per_file}
    if meta and any(meta.get(k) != v for k, v in layout.items()):
        raise RuntimeError("the existing export used a different full.chunk_blocks or "
                           "full.chunks_per_file; finish it with the same config")
    if "started_at" not in meta:
        meta.update(layout, started_at=now.isoformat(),
                    frozen_probe=probe_frozen(cfg, session, now))
    extents: dict[str, list[list[int]]] = meta.setdefault("extents", {})
    done = set(meta.get("shards_done", []))
    failed: dict[str, str] = meta.get("shards_failed", {})
    todo = [s for s in _shards(cfg, shards) if s not in done]

    def save() -> None:
        atomic_write_json(meta_path, meta)

    pa.set_cpu_count(cfg.full.parse_threads)
    for i, shard in enumerate(todo, start=1):
        key = str(shard)
        if key not in extents:
            extents[key] = [[0, _relation_blocks(cfg, session, shard)]]
            save()
        try:
            rows = _export_shard(cfg, session, out_dir, shard, extents[key], save)
        except _RETRYABLE as exc:
            failed[key] = type(exc).__name__
            log.warning("shard %03d failed (%s); it resumes on the next run",
                        shard, type(exc).__name__)
        else:
            done.add(shard)
            failed.pop(key, None)
            meta.setdefault("rows", {})[key] = rows
            log.info("shard %03d: %d crawled rows (%d/%d)", shard, rows, i, len(todo))
        meta["shards_done"] = sorted(done)
        meta["shards_failed"] = failed
        save()
    missing = sorted(set(_shards(cfg, None)) - done)
    if missing:
        meta.pop("finished_at", None)
    elif "finished_at" not in meta:
        finished = datetime.now(UTC)
        meta.update(finished_at=finished.isoformat(),
                    end_probe=probe_frozen(cfg, session, finished))
    save()
    return {"shards_done": len(done), "shards_missing": len(missing),
            "shards_failed": sorted(int(s) for s in failed),
            "rows": sum(meta.get("rows", {}).values())}
