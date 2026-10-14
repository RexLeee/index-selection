"""Recrawl frontier: hands the live selection to the crawler and records what came back.

The frontier lives in selectdb's `recrawl` schema (`isel.schema`). The crawler
side is specified in `docs/CRAWLER_RECRAWL_PROPOSAL.md`.

- `sync` (after each swap): loads the live run's manifest. A doc is on the
  list while `rank` is set; sync writes only `rank` and `model_run_id`.
- `plan` (loop): one queue file per hostname, `crawler_NN/domain_<host_id>.json`,
  at most one on disk per host. The file is renamed into place only after the
  transaction that marks its docs `dispatched` commits. The crawler's per-host
  slot and host gate enforce the 0.2 qps rule; the planner only feeds it. Each
  file also carries `recrawl_share`, the share of the host's requests the crawler
  gives the recrawl lane when the normal lane wants the same host (`pace_share`).
- `archive` (loop): applies closed fetch logs idempotently and moves each
  WARC with its gzipped log into the permanent store.

Every frontier transaction takes one advisory lock, so plan, archive and sync
never interleave their updates and cannot deadlock.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import shutil
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import psycopg

from isel.config import Config
from isel.io import TMP_SUFFIX, commit_file, open_duckdb, sql_str
from isel.manifest import validate_run
from isel.publish import swap_allowed
from isel.schema import DUE_STATES
from isel.urlnorm import md5_number_lower

LOCK_CYCLE = 0x15E1_DB02      # xact lock held by every frontier transaction
LOCK_INSTANCE = {"sync": 0x15E1_DB03, "plan": 0x15E1_DB04, "archive": 0x15E1_DB05}
QUEUE_FILE_RE = re.compile(r"^domain_(\d+)\.json$")
LOG_NAME_RE = re.compile(r"^recrawl-(\d{2})-(\d{8})T\d{6}Z-\d+\.jsonl$")
HTML_TYPES = ("text/html", "application/xhtml+xml")
# The site said no; asked again after `robots_retry_days`, not retried with backoff.
ROBOTS_REFUSALS = ("robots_disallow", "robots_crawl_delay")
FETCH_LOG_KEYS = frozenset({
    "batch_id", "doc_id", "source_url", "final_url", "http_status", "content_type", "etag",
    "last_modified", "dispatched_at", "fetched_at", "fail_reason", "retry_after_s",
    "warc_file", "warc_offset", "warc_length", "payload_sha1"})
RESULT_COLUMNS = ("batch_id", "doc_id", "outcome", "http_status", "content_type", "final_url",
                  "fail_reason", "payload_sha1", "etag", "last_modified", "fetched_at",
                  "retry_after_s", "warc_file", "warc_offset", "warc_length")
_DUE = "state IN ({})".format(", ".join(f"'{s}'" for s in DUE_STATES))
_INT63 = (1 << 63) - 1


class RecrawlError(RuntimeError):
    """Raised when the frontier, a queue directory or a fetch log is inconsistent."""


def host_id(host: str) -> int:
    """Stable queue id of a hostname: the low 63 bits of `md5_number_lower`."""
    return md5_number_lower(host) & _INT63


def pg_doc_id(url: str) -> int:
    """`doc_id` of a canonical URL as selectdb stores it (two's-complement BIGINT)."""
    value = md5_number_lower(url)
    return value - (1 << 64) if value > _INT63 else value


def process_of(cfg: Config, crawl_shard: int) -> int:
    """The crawler process that owns `crawl_shard`; its queue dir is `crawler_NN`."""
    return crawl_shard // (cfg.crawlerdb.num_shards // cfg.recrawl.crawler_processes)


def pace_count_cap(cfg: Config) -> int:
    """Backlog at which `pace_share` reaches `share_max` for any `since` in the past."""
    r = cfg.recrawl
    return math.ceil(r.share_max * max(r.pace_days * 86400, r.plan_interval_s) / r.pace_cycle_s)


def pace_share(cfg: Config, backlog: int, since: datetime, now: datetime) -> float:
    """Recrawl share of a contended host that ends its backlog `pace_days` after `since`.

    `since` is the oldest due_at of the host's due docs. Past that deadline the
    share is `share_max`, so a host that fell behind catches up.
    """
    r = cfg.recrawl
    left = (since + timedelta(days=r.pace_days) - now).total_seconds()
    share = backlog * r.pace_cycle_s / max(left, r.plan_interval_s)
    return min(max(share, r.share_min), r.share_max)


def classify(rec: dict[str, Any]) -> tuple[str, str | None]:
    """Maps one fetch-log record to (outcome, fail_reason)."""
    status, reason = rec["http_status"], rec["fail_reason"]
    if reason is None and status is not None and 200 <= status < 300:
        ctype = (rec["content_type"] or "").split(";")[0].strip().lower()
        if ctype not in HTML_TYPES:
            return "gone", "non_html"
        if not rec["warc_file"]:
            raise RecrawlError(f"doc {rec['doc_id']}: an HTML 2xx without a WARC record")
        return "fetched", None
    if status in (404, 410):
        return "gone", reason or f"http_{status}"
    if reason in ROBOTS_REFUSALS:
        return "robots", reason
    if status in (429, 503) or reason == "host_backoff":
        return "throttled", reason or f"http_{status}"
    return "retry", reason or f"http_{status}"


def _instance_lock(conn: psycopg.Connection, name: str) -> None:
    (ok,) = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_INSTANCE[name],)).fetchone()
    if not ok:
        raise RecrawlError(f"another recrawl-{name} holds the instance lock")


def _cycle_lock(cur: psycopg.Cursor) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_CYCLE,))


def _refresh_best_rank(cur: psycopg.Cursor, hosts: Iterable[int]) -> None:
    ids = sorted(set(hosts))
    if ids:
        cur.execute(
            "UPDATE recrawl.host h SET best_rank = (SELECT min(d.rank) FROM recrawl.doc d "
            f"WHERE d.host_id = h.host_id AND d.rank IS NOT NULL AND d.{_DUE}) "
            "WHERE h.host_id = ANY(%s::bigint[])", (ids,))


def run_loop(cycle: Callable[[], dict[str, Any]], interval_s: float, once: bool,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Runs `cycle` every `interval_s` seconds; prints each result as one JSON line."""
    while True:
        result = cycle()
        if once:
            return result
        print(json.dumps(result, sort_keys=True, default=str), flush=True)
        sleep(interval_s)


# ---------------------------------------------------------------- sync

def sync(cfg: Config, conn: psycopg.Connection,
         now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    """Makes the frontier match the live run: new docs, new ranks, docs off the list."""
    at = now()
    if not swap_allowed(at, cfg.schedule):
        raise RecrawlError("swap freeze window: the metric may be reading selectdb")
    _instance_lock(conn, "sync")
    run_id, label = _live_run(conn)
    run_dir = cfg.work_dir / "full" / "selection" / f"run={label}"
    meta = validate_run(run_dir)
    if meta.get("scope") != "full":
        raise RecrawlError(f"{run_dir} is not a select-full run")
    tmp = cfg.work_dir / "duckdb_tmp"
    con = open_duckdb(cfg, tmp)
    started = time.monotonic()
    try:
        docs = _stage_docs(con, run_dir)
        csv_path = tmp / "recrawl_sync.csv"
        for lo in range(0, docs, cfg.recrawl.sync_chunk_rows):
            con.execute(
                "COPY (SELECT doc_id, url, host_id, host, crawl_shard, rank FROM sync_rows "
                f"WHERE i > {lo} AND i <= {lo + cfg.recrawl.sync_chunk_rows}) "
                f"TO {sql_str(str(csv_path))} (FORMAT csv, HEADER false)")
            try:
                _load_chunk(cfg, conn, csv_path, run_id, at)
            finally:
                csv_path.unlink(missing_ok=True)
    finally:
        con.close()
    with conn.transaction(), conn.cursor() as cur:
        _cycle_lock(cur)
        cur.execute("UPDATE recrawl.doc SET rank = NULL WHERE rank IS NOT NULL "
                    "AND model_run_id IS DISTINCT FROM %s", (run_id,))
        dropped = cur.rowcount
        cur.execute(
            "UPDATE recrawl.host h SET best_rank = m.r FROM (SELECT host_id, min(rank) AS r "
            f"FROM recrawl.doc WHERE rank IS NOT NULL AND {_DUE} GROUP BY host_id) m "
            "WHERE h.host_id = m.host_id AND h.best_rank IS DISTINCT FROM m.r")
        cur.execute(
            "UPDATE recrawl.host h SET best_rank = NULL WHERE best_rank IS NOT NULL AND "
            "NOT EXISTS (SELECT 1 FROM recrawl.doc d WHERE d.host_id = h.host_id "
            f"AND d.rank IS NOT NULL AND d.{_DUE})")
    conn.execute("ANALYZE recrawl.host")
    conn.execute("ANALYZE recrawl.doc")
    (hosts,) = conn.execute("SELECT count(*) FROM recrawl.host WHERE best_rank IS NOT NULL"
                            ).fetchone()
    return {"model_run_id": run_id, "run_label": label, "docs": docs, "dropped": dropped,
            "hosts_due": hosts, "secs": round(time.monotonic() - started, 1)}


def _live_run(conn: psycopg.Connection) -> tuple[int, str]:
    rows = conn.execute("SELECT id, run_label FROM public.model_runs WHERE status = 'live'"
                        ).fetchall()
    if len(rows) != 1:
        raise RecrawlError(f"expected one live run, found {len(rows)}")
    return rows[0]


def _stage_docs(con: Any, run_dir: Path) -> int:
    """One row per doc_id (its best rank), routed by the shard of its host's best doc."""
    con.execute(f"""
CREATE OR REPLACE TEMP TABLE sync_rows AS
WITH best AS (
  SELECT * FROM read_parquet({sql_str(str(run_dir / 'manifest' / '*.parquet'))})
  QUALIFY row_number() OVER (PARTITION BY doc_id ORDER BY rank, url) = 1
), shard AS (
  SELECT host, arg_min(crawl_shard, rank) AS crawl_shard FROM best GROUP BY host
)
SELECT row_number() OVER (ORDER BY b.rank) AS i,
       CASE WHEN b.doc_id > {_INT63}
            THEN (b.doc_id - {_INT63 + 1})::BIGINT - {_INT63} - 1
            ELSE b.doc_id::BIGINT END AS doc_id,
       b.canonical_url AS url, (md5_number_lower(b.host) & {_INT63})::BIGINT AS host_id,
       b.host, s.crawl_shard, b.rank
FROM best b JOIN shard s USING (host)""")
    (docs,) = con.execute("SELECT count(*) FROM sync_rows").fetchone()
    return int(docs)


def _load_chunk(cfg: Config, conn: psycopg.Connection, csv_path: Path, run_id: int,
                at: datetime) -> None:
    with conn.transaction(), conn.cursor() as cur:
        _cycle_lock(cur)
        cur.execute("CREATE TEMP TABLE s (doc_id BIGINT, url TEXT, host_id BIGINT, host TEXT, "
                    "crawl_shard SMALLINT, rank BIGINT) ON COMMIT DROP")
        with cur.copy("COPY s FROM STDIN (FORMAT csv)") as copy, csv_path.open("rb") as fh:
            while block := fh.read(cfg.selectdb.copy_block_bytes):
                copy.write(block)
        cur.execute(
            "INSERT INTO recrawl.host (host_id, host, crawl_shard) "
            "SELECT DISTINCT ON (host_id) host_id, host, crawl_shard FROM s ORDER BY host_id "
            "ON CONFLICT (host_id) DO UPDATE SET crawl_shard = EXCLUDED.crawl_shard "
            "WHERE recrawl.host.crawl_shard <> EXCLUDED.crawl_shard")
        cur.execute("SELECT count(*) FROM s JOIN recrawl.host h USING (host_id) "
                    "WHERE h.host <> s.host")
        (clashes,) = cur.fetchone()
        if clashes:
            raise RecrawlError(f"{clashes} docs hit a host_id taken by another hostname")
        cur.execute(
            # A new doc is due from the sync; pace_share counts its deadline from here.
            "INSERT INTO recrawl.doc (doc_id, url, host_id, rank, model_run_id, due_at) "
            "SELECT doc_id, url, host_id, rank, %s, %s FROM s ON CONFLICT (doc_id) DO UPDATE "
            "SET rank = EXCLUDED.rank, model_run_id = EXCLUDED.model_run_id "
            "WHERE (recrawl.doc.rank, recrawl.doc.model_run_id) "
            "IS DISTINCT FROM (EXCLUDED.rank, EXCLUDED.model_run_id)", (run_id, at))


# ---------------------------------------------------------------- plan

def run_planner(cfg: Config, conn: psycopg.Connection, once: bool) -> dict[str, Any]:
    _instance_lock(conn, "plan")
    root = Path(cfg.recrawl.queue_root)
    for stale in root.glob(f"crawler_*/domain_*.json{TMP_SUFFIX}"):
        stale.unlink()         # a planner that died between write and rename
    return run_loop(lambda: plan_cycle(cfg, conn, datetime.now(UTC)),
                    cfg.recrawl.plan_interval_s, once)


def plan_cycle(cfg: Config, conn: psycopg.Connection, now: datetime) -> dict[str, Any]:
    """One planning pass; returns counts. Queue files appear only after the commit."""
    r = cfg.recrawl
    root = Path(r.queue_root)
    dirs = [root / f"crawler_{p:02d}" for p in range(r.crawler_processes)]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    on_disk = [{int(m.group(1)) for name in os.listdir(d) if (m := QUEUE_FILE_RE.match(name))}
               for d in dirs]
    written: list[tuple[Path, Path, int]] = []
    stats = {"taken": 0, "timed_out": 0, "swept": 0, "files": 0, "docs": 0, "disk_full": False}
    try:
        with conn.transaction(), conn.cursor() as cur:
            _cycle_lock(cur)
            stats["taken"] = _note_taken(cfg, cur, on_disk, now)
            cur.execute("UPDATE recrawl.doc SET state = 'pending' WHERE state = 'dispatched' "
                        "AND dispatched_at < %s RETURNING host_id",
                        (now - timedelta(seconds=r.dispatch_timeout_s),))
            timed_out = [h for (h,) in cur.fetchall()]
            cur.execute(
                "UPDATE recrawl.doc SET state = 'pending' WHERE doc_id IN (SELECT doc_id "
                "FROM recrawl.doc WHERE state = 'fetched' AND rank IS NOT NULL AND due_at <= %s "
                "LIMIT %s) RETURNING host_id", (now, r.sweep_rows))
            swept = [h for (h,) in cur.fetchall()]
            stats["timed_out"], stats["swept"] = len(timed_out), len(swept)
            _refresh_best_rank(cur, timed_out + swept)
            if shutil.disk_usage(root).free < r.min_free_gb * 1e9:
                stats["disk_full"] = True
            else:
                budget = r.plan_max_files_per_txn
                for p, d in enumerate(dirs):
                    room = min(r.max_files_per_dir - len(on_disk[p]), budget)
                    if room > 0:
                        files = _dispatch(cfg, cur, p, d, room, on_disk[p], now, written)
                        budget -= files
                        stats["files"] += files
                stats["docs"] = sum(n for _, _, n in written)
        for tmp, final, _ in written:
            commit_file(tmp, final)
    except BaseException:
        for tmp, _, _ in written:
            tmp.unlink(missing_ok=True)
        raise
    return stats


def _note_taken(cfg: Config, cur: psycopg.Cursor, on_disk: list[set[int]],
                now: datetime) -> int:
    """Hosts whose queue file the crawler took: their docs start the dispatch timeout."""
    cur.execute("SELECT host_id, crawl_shard, batch_id FROM recrawl.host "
                "WHERE batch_id IS NOT NULL")
    taken = [(h, b) for h, shard, b in cur.fetchall()
             if h not in on_disk[process_of(cfg, shard)]]
    if taken:
        hosts, batches = [h for h, _ in taken], [b for _, b in taken]
        cur.execute("UPDATE recrawl.doc SET dispatched_at = %s WHERE state = 'dispatched' "
                    "AND batch_id = ANY(%s::bigint[])", (now, batches))
        cur.execute("UPDATE recrawl.host SET batch_id = NULL WHERE host_id = ANY(%s::bigint[])",
                    (hosts,))
    return len(taken)


def _dispatch(cfg: Config, cur: psycopg.Cursor, process: int, directory: Path, room: int,
              present: set[int], now: datetime,
              written: list[tuple[Path, Path, int]]) -> int:
    """Writes up to `room` queue files for this process's best-ranked ready hosts."""
    r = cfg.recrawl
    per_process = cfg.crawlerdb.num_shards // r.crawler_processes
    cur.execute(
        "SELECT host_id FROM recrawl.host WHERE batch_id IS NULL AND best_rank IS NOT NULL "
        "AND (next_at IS NULL OR next_at <= %s) AND crawl_shard / %s = %s "
        "AND NOT host_id = ANY(%s::bigint[]) ORDER BY best_rank LIMIT %s",
        (now, per_process, process, sorted(present), room))
    hosts = [h for (h,) in cur.fetchall()]
    if not hosts:
        return 0
    cur.execute(
        "SELECT h.host_id, d.doc_id, d.url FROM unnest(%s::bigint[]) AS h(host_id) "
        "CROSS JOIN LATERAL (SELECT doc_id, url, rank FROM recrawl.doc "
        f"WHERE host_id = h.host_id AND rank IS NOT NULL AND {_DUE} AND due_at <= %s "
        "ORDER BY rank LIMIT %s) d ORDER BY h.host_id, d.rank", (hosts, now, r.batch_size))
    batches: dict[int, list[tuple[int, str]]] = {}
    for h, doc_id, url in cur.fetchall():
        batches.setdefault(h, []).append((doc_id, url))
    idle = [h for h in hosts if h not in batches]
    if idle:
        # Only docs in backoff are left: wait for the earliest, or drop out of the ready set.
        cur.execute(
            "UPDATE recrawl.host h SET next_at = m.n, best_rank = CASE WHEN m.n IS NULL "
            "THEN NULL ELSE h.best_rank END FROM (SELECT u.host_id, (SELECT min(d.due_at) "
            "FROM recrawl.doc d WHERE d.host_id = u.host_id AND d.rank IS NOT NULL "
            f"AND d.{_DUE}) AS n FROM unnest(%s::bigint[]) AS u(host_id)) m "
            "WHERE h.host_id = m.host_id", (idle,))
    if not batches:
        return 0
    # A capped count already gives share_max, whatever the min(due_at) of the sample.
    cur.execute(
        "SELECT h.host_id, p.n, p.since FROM unnest(%s::bigint[]) AS h(host_id) "
        "CROSS JOIN LATERAL (SELECT count(*) AS n, min(due_at) AS since FROM (SELECT due_at "
        f"FROM recrawl.doc WHERE host_id = h.host_id AND rank IS NOT NULL AND {_DUE} "
        "AND due_at <= %s LIMIT %s) d) p", (list(batches), now, pace_count_cap(cfg)))
    shares = {h: round(pace_share(cfg, n, since, now), 3) for h, n, since in cur.fetchall()}
    cur.execute("SELECT nextval('recrawl.batch_seq') FROM generate_series(1, %s)",
                (len(batches),))
    ids = [b for (b,) in cur.fetchall()]
    doc_ids, doc_batches, host_ids, host_batches = [], [], [], []
    for (h, docs), batch in zip(batches.items(), ids, strict=True):
        host_ids.append(h)
        host_batches.append(batch)
        doc_ids += [d for d, _ in docs]
        doc_batches += [batch] * len(docs)
        final = directory / f"domain_{h:06d}.json"
        tmp = final.with_name(final.name + TMP_SUFFIX)
        payload = {"generated_at": now.isoformat(), "domain_id": h, "batch_id": batch,
                   "urls": [u for _, u in docs], "doc_ids": [d for d, _ in docs],
                   "recrawl_share": shares[h]}
        tmp.write_text(json.dumps(payload, sort_keys=True))
        written.append((tmp, final, len(docs)))
    cur.execute(
        "UPDATE recrawl.doc d SET state = 'dispatched', batch_id = u.batch_id, "
        "dispatched_at = NULL FROM unnest(%s::bigint[], %s::bigint[]) AS u(doc_id, batch_id) "
        "WHERE d.doc_id = u.doc_id", (doc_ids, doc_batches))
    cur.execute(
        "UPDATE recrawl.host h SET batch_id = u.batch_id FROM unnest(%s::bigint[], "
        "%s::bigint[]) AS u(host_id, batch_id) WHERE h.host_id = u.host_id",
        (host_ids, host_batches))
    _refresh_best_rank(cur, host_ids)
    return len(batches)


def capacity_report(cfg: Config, conn: psycopg.Connection) -> dict[str, Any]:
    """Per-hostname capacity of the live list at 1 / `min_gap_s` requests per second."""
    r = cfg.recrawl
    rows = conn.execute("SELECT count(*) FROM recrawl.doc WHERE rank IS NOT NULL "
                        "AND state <> 'gone' GROUP BY host_id").fetchall()
    counts = np.array([n for (n,) in rows], dtype=np.int64)
    states = dict(conn.execute("SELECT state, count(*) FROM recrawl.doc "
                               "WHERE rank IS NOT NULL GROUP BY state").fetchall())
    per_day = 86400 / r.min_gap_s
    total = int(counts.sum())
    horizons = []
    for days in r.report_days:
        cap = per_day * days
        covered = int(np.minimum(counts, cap).sum())
        horizons.append({"days": days, "covered": covered,
                         "share": round(covered / total, 4) if total else 0.0,
                         "hosts_unfinished": int((counts > cap).sum()),
                         "mean_req_s": round(covered / (days * 86400), 1)})
    return {"docs": total, "hosts": int(counts.size), "states": states,
            "max_host_docs": int(counts.max()) if counts.size else 0,
            "days_for_largest_host": round(int(counts.max()) / per_day, 1) if counts.size
            else 0.0, "horizons": horizons}


# ---------------------------------------------------------------- archive

def run_archiver(cfg: Config, conn: psycopg.Connection, once: bool) -> dict[str, Any]:
    _instance_lock(conn, "archive")
    return run_loop(lambda: archive_cycle(cfg, conn, datetime.now(UTC)),
                    cfg.recrawl.archive_interval_s, once)


def archive_cycle(cfg: Config, conn: psycopg.Connection, now: datetime) -> dict[str, Any]:
    """Applies every closed fetch log and moves it with its WARC into `warc_root`."""
    totals = {"logs": 0, "records": 0, "applied": 0}
    root = Path(cfg.recrawl.result_root)
    for p in range(cfg.recrawl.crawler_processes):
        d = root / f"crawler_{p:02d}"
        if not d.is_dir():
            continue
        for log in sorted(d.glob("*.jsonl")):
            records, applied = _archive_log(cfg, conn, p, log, now)
            totals["logs"] += 1
            totals["records"] += records
            totals["applied"] += applied
    return totals


def read_fetch_log(path: Path, warc_name: str) -> list[dict[str, Any]]:
    """Parses a closed fetch log; one record per doc_id, a 2xx beating a failure."""
    best: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            rec = json.loads(line)
            if set(rec) != FETCH_LOG_KEYS:
                raise RecrawlError(f"{path.name}:{n}: keys differ from the fetch-log contract")
            if rec["doc_id"] != pg_doc_id(rec["source_url"]):
                raise RecrawlError(f"{path.name}:{n}: doc_id does not match source_url")
            if rec["warc_file"] not in (None, warc_name):
                raise RecrawlError(f"{path.name}:{n}: warc_file is not {warc_name}")
            rec["outcome"], rec["fail_reason"] = classify(rec)
            key = (rec["outcome"] == "fetched", rec["fetched_at"] or "")
            old = best.get(rec["doc_id"])
            if old is None or key >= (old["outcome"] == "fetched", old["fetched_at"] or ""):
                best[rec["doc_id"]] = rec
    return list(best.values())


def _archive_log(cfg: Config, conn: psycopg.Connection, process: int, log: Path,
                 now: datetime) -> tuple[int, int]:
    m = LOG_NAME_RE.match(log.name)
    if m is None or int(m.group(1)) != process:
        raise RecrawlError(f"{log} does not follow recrawl-{process:02d}-<ts>Z-<seq>.jsonl")
    base = log.name.removesuffix(".jsonl")
    warc_name = f"{base}.warc.gz"
    rel_dir = Path(m.group(2)) / f"crawler_{process:02d}"
    records = read_fetch_log(log, warc_name)
    applied = _apply(cfg, conn, records, str(rel_dir / warc_name), now)
    dest = Path(cfg.recrawl.warc_root) / rel_dir
    dest.mkdir(parents=True, exist_ok=True)
    gz_tmp = dest / f"{base}.jsonl.gz{TMP_SUFFIX}"
    with log.open("rb") as src, gzip.open(gz_tmp, "wb") as out:
        shutil.copyfileobj(src, out)
    commit_file(gz_tmp, dest / f"{base}.jsonl.gz")
    warc_src, warc_dst = log.with_name(warc_name), dest / warc_name
    if warc_src.exists():
        os.replace(warc_src, warc_dst)
    elif not warc_dst.exists():
        raise RecrawlError(f"{warc_src} is missing")
    log.unlink()
    return len(records), applied


def _apply(cfg: Config, conn: psycopg.Connection, records: list[dict[str, Any]],
           warc_file: str, now: datetime) -> int:
    """Applies results idempotently; returns the number of docs changed."""
    if not records:
        return 0
    r = cfg.recrawl
    args = {"now": now, "stale": r.stale_days, "robots": r.robots_retry_days,
            "base": r.retry_base_s, "max": r.retry_max_s, "attempts": r.max_attempts,
            "pause": r.host_pause_max_s}
    changed = 0
    with conn.transaction(), conn.cursor() as cur:
        _cycle_lock(cur)
        cur.execute("CREATE TEMP TABLE r (batch_id BIGINT, doc_id BIGINT, outcome TEXT, "
                    "http_status SMALLINT, content_type TEXT, final_url TEXT, fail_reason TEXT, "
                    "payload_sha1 TEXT, etag TEXT, last_modified TIMESTAMPTZ, "
                    "fetched_at TIMESTAMPTZ, retry_after_s DOUBLE PRECISION, warc_file TEXT, "
                    "warc_offset BIGINT, warc_length BIGINT) ON COMMIT DROP")
        with cur.copy(f"COPY r ({', '.join(RESULT_COLUMNS)}) FROM STDIN") as copy:
            for rec in records:
                row = {**rec, "warc_file": warc_file if rec["warc_file"] else None}
                copy.write_row([row[c] for c in RESULT_COLUMNS])
        # A 2xx is news whatever the doc's state; it only has to be newer than the last one.
        cur.execute(
            "UPDATE recrawl.doc d SET state = 'fetched', fetched_at = r.fetched_at, "
            "http_status = r.http_status, content_type = r.content_type, "
            "final_url = r.final_url, fail_reason = NULL, payload_sha1 = r.payload_sha1, "
            "etag = r.etag, last_modified = r.last_modified, warc_file = r.warc_file, "
            "warc_offset = r.warc_offset, warc_length = r.warc_length, attempts = 0, "
            "due_at = r.fetched_at + make_interval(days => %(stale)s) FROM r "
            "WHERE r.outcome = 'fetched' AND d.doc_id = r.doc_id "
            "AND (d.fetched_at IS NULL OR d.fetched_at < r.fetched_at)", args)
        changed += cur.rowcount
        # A failure counts only for the batch the doc is waiting on.
        current = ("FROM r WHERE r.outcome = {} AND d.doc_id = r.doc_id "
                   "AND d.state = 'dispatched' AND d.batch_id = r.batch_id")
        cur.execute(
            "UPDATE recrawl.doc d SET state = 'gone', http_status = r.http_status, "
            "content_type = r.content_type, final_url = r.final_url, "
            "fail_reason = r.fail_reason " + current.format("'gone'"), args)
        changed += cur.rowcount
        cur.execute(
            "UPDATE recrawl.doc d SET state = 'failed', http_status = r.http_status, "
            "final_url = r.final_url, fail_reason = r.fail_reason, "
            "due_at = %(now)s + make_interval(days => %(robots)s) "
            + current.format("'robots'"), args)
        changed += cur.rowcount
        cur.execute(
            "UPDATE recrawl.host h SET next_at = greatest(coalesce(h.next_at, %(now)s), "
            "%(now)s + make_interval(secs => least(coalesce(p.wait, %(base)s), %(pause)s))) "
            "FROM (SELECT d.host_id, max(r.retry_after_s) AS wait FROM r "
            "JOIN recrawl.doc d USING (doc_id) WHERE r.outcome = 'throttled' "
            "AND d.state = 'dispatched' AND d.batch_id = r.batch_id GROUP BY d.host_id) p "
            "WHERE h.host_id = p.host_id", args)
        cur.execute(
            "UPDATE recrawl.doc d SET state = 'failed', attempts = d.attempts + 1, "
            "http_status = r.http_status, final_url = r.final_url, "
            "fail_reason = r.fail_reason, due_at = %(now)s + CASE "
            "WHEN d.attempts + 1 >= %(attempts)s THEN make_interval(days => %(stale)s) "
            "ELSE make_interval(secs => least(%(base)s * power(2, d.attempts), %(max)s)) END "
            "FROM r WHERE r.outcome IN ('throttled', 'retry') AND d.doc_id = r.doc_id "
            "AND d.state = 'dispatched' AND d.batch_id = r.batch_id", args)
        changed += cur.rowcount
        cur.execute("SELECT DISTINCT d.host_id FROM r JOIN recrawl.doc d USING (doc_id)")
        _refresh_best_rank(cur, [h for (h,) in cur.fetchall()])
    return changed
