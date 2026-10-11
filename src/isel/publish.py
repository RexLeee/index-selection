"""Load, swap and roll back the selection in OUR selectdb; read it as the metric does.

This is the only module that writes to a database. It refuses a target whose
port is in `selectdb.forbidden_ports` (crawlerdb, metricdb, V1 selectdb) or
that holds crawler or metric tables.

The schema matches V1 (WebSearchEngine `IndexSelection/selectdb_common.py`)
and the official metric's query
`SELECT url FROM public.selected_urls_current WHERE url = ANY(:urls)`.

- Load (transaction 1): create `selected_urls_r<id>`, COPY FREEZE, add the
  primary key, ANALYZE and check the row count in one transaction. With
  `wal_level=minimal`, a table created and filled in one transaction skips WAL.
- Swap (transaction 2): rename tables under a short `lock_timeout`, so readers
  never see a half-loaded list and wait milliseconds at most.
- A new load or a rollback drops every loaded-but-unswapped run, so a pending
  swap cannot undo a rollback.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from isel.config import Config, ScheduleCfg, hhmm_minutes
from isel.evaluate import bootstrap, golden_by_tag, rates
from isel.golden import read_golden
from isel.io import assert_no_secrets, open_duckdb, read_json, sql_str
from isel.pg import require_env

CURRENT = "selected_urls_current"
PREV = "selected_urls_prev"
RUN_TABLE_RE = r"^selected_urls_r[0-9]+$"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public.model_runs (
    id BIGSERIAL PRIMARY KEY,
    run_label TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cutoff_datetime TIMESTAMPTZ,
    model_path TEXT,
    train_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    eval_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    selection_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_paths JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'loading',
    activated_at TIMESTAMPTZ,
    config_sha256 TEXT,
    code_version TEXT
);
CREATE TABLE IF NOT EXISTS public.selected_urls_current (
    url TEXT PRIMARY KEY,
    score DOUBLE PRECISION NOT NULL,
    first_seen TIMESTAMPTZ,
    run_id BIGINT NOT NULL REFERENCES public.model_runs(id) ON DELETE CASCADE,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
# A database with any of these is crawlerdb or metricdb, whatever its port.
_FOREIGN_TABLES = ("public.url_state_current_000", "public.metric_url")


class ForbiddenTarget(RuntimeError):
    """Raised when a write connection would reach a database other than our selectdb."""


def swap_allowed(now: datetime, cfg: ScheduleCfg) -> bool:
    """False while the metric may read the list (measurement days, freeze window, UTC)."""
    now = now.astimezone(UTC)
    if now.day not in cfg.swap_freeze_days:
        return True
    start, end = (hhmm_minutes(v) for v in cfg.swap_freeze_window)
    return not start <= now.hour * 60 + now.minute < end


def connect_selectdb(cfg: Config, dsn_env: str, read_only: bool) -> psycopg.Connection:
    """Opens our selectdb after checking the target is not a forbidden database.

    Raises:
        ForbiddenTarget: If the port is forbidden or the database holds
            crawler or metric tables.
    """
    s = cfg.selectdb
    dsn = require_env(dsn_env)
    if int(conninfo_to_dict(dsn).get("port") or 5432) in s.forbidden_ports:
        raise ForbiddenTarget(f"{dsn_env} points at a forbidden port")
    conn = psycopg.connect(dsn, autocommit=True, application_name=f"{cfg.run.app_name}_select",
                           connect_timeout=s.connect_timeout_s,
                           options="-c default_transaction_read_only=on" if read_only else "")
    try:
        if conn.info.port in s.forbidden_ports:
            raise ForbiddenTarget(f"{dsn_env} reached a forbidden port")
        with conn.cursor() as cur:
            cur.execute("SELECT " + " OR ".join(
                f"to_regclass('{t}') IS NOT NULL" for t in _FOREIGN_TABLES))
            if cur.fetchone()[0]:
                raise ForbiddenTarget(f"{dsn_env} reaches a database with crawler or metric tables")
    except BaseException:
        conn.close()
        raise
    return conn


def init_selectdb(cfg: Config, admin: psycopg.Connection) -> None:
    """One-time setup: tables plus the read-only role the metric connects as. Idempotent."""
    role = cfg.selectdb.reader_role
    password = require_env(cfg.selectdb.reader_password_env)
    with admin.transaction(), admin.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(role), sql.Literal(password)))
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(admin.info.dbname), sql.Identifier(role)))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
            sql.Identifier(role)))
        cur.execute(sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
            sql.Identifier(role)))


def _ensure_schema(cfg: Config, admin: psycopg.Connection) -> None:
    with admin.transaction(), admin.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (cfg.selectdb.reader_role,))
        if cur.fetchone() is None:
            raise RuntimeError(f"role {cfg.selectdb.reader_role!r} is missing; "
                               "run `isel init-selectdb` once")


def _table_exists(cur: psycopg.Cursor, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    return bool(cur.fetchone()[0])


def _drop_unswapped(admin: psycopg.Connection) -> list[int]:
    """Drops tables of runs that were loaded but never swapped in; returns their ids."""
    dropped = []
    with admin.transaction(), admin.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename ~ %s", (RUN_TABLE_RE,))
        for (name,) in cur.fetchall():
            run_id = int(name.removeprefix("selected_urls_r"))
            cur.execute(sql.SQL("DROP TABLE public.{}").format(sql.Identifier(name)))
            cur.execute("UPDATE public.model_runs SET status = 'abandoned' WHERE id = %s",
                        (run_id,))
            dropped.append(run_id)
    return dropped


def _ensure_space(cfg: Config, admin: psycopg.Connection, k: int) -> None:
    s = cfg.selectdb
    need = k * s.est_bytes_per_row
    with admin.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        (size,) = cur.fetchone()
        if size + need > s.max_db_bytes and _table_exists(cur, PREV):
            with admin.transaction():
                cur.execute(sql.SQL("DROP TABLE public.{}").format(sql.Identifier(PREV)))
            cur.execute("SELECT pg_database_size(current_database())")
            (size,) = cur.fetchone()
    if size + need > s.max_db_bytes:
        raise RuntimeError(f"selectdb would exceed selectdb.max_db_bytes ({size} + {need})")


def _model_runs_row(run: dict[str, Any]) -> dict[str, Any]:
    row = {
        "run_label": run["run_id"], "cutoff_datetime": run["snapshot_at"],
        "model_path": run["model_dir"], "train_metrics": run["model_meta"],
        "selection_metrics": {k: run[k] for k in
                              ("k", "rows", "candidates", "boundary_score", "tau",
                               "target_batch")},
        "source_paths": {"selection_run": run["run_id"]},
        "config_sha256": run["config_sha256"], "code_version": run["code_version"],
    }
    assert_no_secrets(row)
    return row


def _load_validated(cfg: Config, admin: psycopg.Connection, csv_path: Path,
                    row: dict[str, Any], k: int) -> int:
    """Transaction 1: model_runs row, new table, COPY FREEZE, keys, ANALYZE, row check."""
    s = cfg.selectdb
    with admin.transaction(), admin.cursor() as cur:
        cur.execute(
            "INSERT INTO public.model_runs (run_label, cutoff_datetime, model_path, "
            "train_metrics, selection_metrics, source_paths, status, config_sha256, "
            "code_version) VALUES (%s, %s, %s, %s, %s, %s, 'loading', %s, %s) RETURNING id",
            (row["run_label"], row["cutoff_datetime"], row["model_path"],
             json.dumps(row["train_metrics"], default=str),
             json.dumps(row["selection_metrics"], default=str),
             json.dumps(row["source_paths"]), row["config_sha256"], row["code_version"]))
        (run_id,) = cur.fetchone()
        table = sql.Identifier(f"selected_urls_r{run_id}")
        cur.execute(sql.SQL(
            "CREATE TABLE public.{} (url TEXT NOT NULL, score DOUBLE PRECISION NOT NULL, "
            "first_seen TIMESTAMPTZ, run_id BIGINT NOT NULL DEFAULT {}, "
            "selected_at TIMESTAMPTZ NOT NULL DEFAULT now())").format(table, sql.Literal(run_id)))
        # FREEZE is allowed because the table was created in this transaction. Rows are
        # written frozen and all-visible, so readers never dirty pages with hint bits.
        with cur.copy(sql.SQL("COPY public.{} (url, score) FROM STDIN (FORMAT csv, FREEZE)")
                      .format(table)) as copy, csv_path.open("rb") as fh:
            while block := fh.read(s.copy_block_bytes):
                copy.write(block)
        cur.execute(sql.SQL("SET LOCAL maintenance_work_mem = {}").format(
            sql.Literal(s.maintenance_work_mem)))
        cur.execute(sql.SQL("ALTER TABLE public.{} ADD CONSTRAINT {} PRIMARY KEY (url)").format(
            table, sql.Identifier(f"selected_urls_r{run_id}_pkey")))
        cur.execute(sql.SQL(
            "ALTER TABLE public.{} ADD CONSTRAINT {} FOREIGN KEY (run_id) "
            "REFERENCES public.model_runs(id) ON DELETE CASCADE").format(
            table, sql.Identifier(f"selected_urls_r{run_id}_run_fk")))
        cur.execute(sql.SQL("ANALYZE public.{}").format(table))
        cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(table))
        (rows,) = cur.fetchone()
        if rows != k:
            raise RuntimeError(f"loaded {rows} rows, expected {k}")
        cur.execute("UPDATE public.model_runs SET status = 'validated' WHERE id = %s", (run_id,))
    return run_id


def load_selection(cfg: Config, admin: psycopg.Connection, run_dir: Path,
                   work_dir: Path) -> dict[str, Any]:
    """Loads a `select-full` run into a new, not yet visible table.

    The table is validated (row count == K, unique URLs through the primary key)
    before commit. Nothing becomes visible to readers until `swap`.
    """
    run = read_json(run_dir / "_meta.json")
    if run is None:
        raise FileNotFoundError(f"{run_dir}/_meta.json is missing; run select-full")
    if run["rows"] != run["k"]:
        raise RuntimeError(f"the run holds {run['rows']} rows, not K = {run['k']}")
    row = _model_runs_row(run)
    _ensure_schema(cfg, admin)
    dropped = _drop_unswapped(admin)
    _ensure_space(cfg, admin, run["k"])
    csv_path = work_dir / "duckdb_tmp" / "load.csv"
    con = open_duckdb(cfg, work_dir / "duckdb_tmp")
    started = time.monotonic()
    try:
        con.execute(f"COPY (SELECT url, score FROM read_parquet("
                    f"{sql_str(str(run_dir / 'selected.parquet'))})) "
                    f"TO {sql_str(str(csv_path))} (FORMAT csv, HEADER false)")
        db_run_id = _load_validated(cfg, admin, csv_path, row, run["k"])
    finally:
        csv_path.unlink(missing_ok=True)
    return {"db_run_id": db_run_id, "rows": run["k"], "dropped_unswapped": dropped,
            "secs": round(time.monotonic() - started, 1)}


def swap(cfg: Config, admin: psycopg.Connection, run_id: int,
         now: Callable[[], datetime] = lambda: datetime.now(UTC),
         sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Makes run `run_id` live. Retries on lock timeouts; refuses during the freeze.

    Idempotent: a run that is already live reports success.
    """
    s = cfg.selectdb
    with admin.cursor() as cur:
        cur.execute("SELECT status FROM public.model_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if row and row[0] == "live":
            return {"swapped": True, "already_live": True}
        if not _table_exists(cur, f"selected_urls_r{run_id}"):
            return {"swapped": False, "reason": "run has no loaded table", "retry": False}
    for attempt in range(1, s.swap_retry_attempts + 1):
        if not swap_allowed(now(), cfg.schedule):
            return {"swapped": False, "reason": "swap freeze window", "retry": True}
        try:
            with admin.transaction(), admin.cursor() as cur:
                cur.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(
                    sql.Literal(s.swap_lock_timeout)))
                started = time.monotonic()
                if _table_exists(cur, CURRENT):
                    cur.execute(sql.SQL("LOCK TABLE public.{} IN ACCESS EXCLUSIVE MODE").format(
                        sql.Identifier(CURRENT)))
                lock_ms = round((time.monotonic() - started) * 1000, 1)
                cur.execute(sql.SQL("DROP TABLE IF EXISTS public.{}").format(sql.Identifier(PREV)))
                cur.execute(sql.SQL("ALTER TABLE IF EXISTS public.{} RENAME TO {}").format(
                    sql.Identifier(CURRENT), sql.Identifier(PREV)))
                cur.execute(sql.SQL("ALTER TABLE public.{} RENAME TO {}").format(
                    sql.Identifier(f"selected_urls_r{run_id}"), sql.Identifier(CURRENT)))
                cur.execute("UPDATE public.model_runs SET status = 'retired' WHERE status = 'live'")
                cur.execute("UPDATE public.model_runs SET status = 'live', activated_at = now() "
                            "WHERE id = %s", (run_id,))
                if not s.keep_prev:
                    cur.execute(sql.SQL("DROP TABLE IF EXISTS public.{}").format(
                        sql.Identifier(PREV)))
        except psycopg.errors.LockNotAvailable:
            if attempt < s.swap_retry_attempts:
                sleep(s.swap_retry_backoff_s)
            continue
        return {"swapped": True, "lock_wait_ms": lock_ms, "attempts": attempt}
    return {"swapped": False, "reason": "lock timeout", "retry": True}


def rollback(cfg: Config, admin: psycopg.Connection) -> dict[str, Any]:
    """Swaps `selected_urls_current` and `selected_urls_prev` back."""
    tmp = "selected_urls_swap_tmp"
    with admin.transaction(), admin.cursor() as cur:
        if not (_table_exists(cur, CURRENT) and _table_exists(cur, PREV)):
            raise RuntimeError("rollback needs both current and prev tables")
        cur.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(
            sql.Literal(cfg.selectdb.swap_lock_timeout)))
        cur.execute(sql.SQL("LOCK TABLE public.{}, public.{} IN ACCESS EXCLUSIVE MODE").format(
            sql.Identifier(CURRENT), sql.Identifier(PREV)))
        cur.execute(sql.SQL("SELECT run_id FROM public.{} LIMIT 1").format(sql.Identifier(PREV)))
        prev_row = cur.fetchone()
        for a, b in ((CURRENT, tmp), (PREV, CURRENT), (tmp, PREV)):
            cur.execute(sql.SQL("ALTER TABLE public.{} RENAME TO {}").format(
                sql.Identifier(a), sql.Identifier(b)))
        cur.execute("UPDATE public.model_runs SET status = 'rolled_back' WHERE status = 'live'")
        if prev_row:
            cur.execute("UPDATE public.model_runs SET status = 'live', activated_at = now() "
                        "WHERE id = %s", (prev_row[0],))
    return {"live_run": prev_row[0] if prev_row else None,
            "dropped_unswapped": _drop_unswapped(admin)}


def verify_live(cfg: Config, work_dir: Path, batch: int) -> dict[str, Any]:
    """The indexed rate of `batch` under the live list, read as the official metric reads it.

    Connects as the metric's reader role and sends the metric's own query in
    chunks of `selectdb.verify_chunk` URLs. The exact rate equals the metric's
    `indexed_rate` (distinct raw URLs found / distinct raw URLs); the canonical
    rate also accepts the canonical spelling.
    """
    golden = golden_by_tag(read_golden(work_dir).to_pylist(), batch, cfg.golden.tags)
    if not golden:
        raise RuntimeError(f"batch {batch} has no golden rows; run pull-golden first")
    e, chunk = cfg.eval, cfg.selectdb.verify_chunk
    conn = connect_selectdb(cfg, cfg.selectdb.reader_dsn_env, read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, run_label, activated_at FROM public.model_runs "
                        "WHERE status = 'live'")
            live = cur.fetchall()
            tags = {}
            for tag, g in golden.items():
                spellings = sorted(set(g.urls) | set(g.canonical.values()))
                listed: set[str] = set()
                for i in range(0, len(spellings), chunk):
                    cur.execute("SELECT url FROM public.selected_urls_current "
                                "WHERE url = ANY(%s)", (spellings[i:i + chunk],))
                    listed.update(u for (u,) in cur.fetchall())
                exact, r_exact, r_canonical = rates(g, listed)
                tags[tag] = {"golden_urls": len(g.urls), "indexed": len(exact),
                             "indexed_exact_rate": r_exact,
                             "indexed_canonical_rate": r_canonical,
                             "ci95": list(bootstrap(g.queries, exact, e.bootstrap_samples,
                                                    e.bootstrap_seed))}
    finally:
        conn.close()
    return {"batch": batch,
            "live_runs": [{"id": r[0], "run_label": r[1], "activated_at": r[2]} for r in live],
            "tags": tags}
