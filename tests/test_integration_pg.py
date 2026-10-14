"""The read and selectdb paths against a throwaway local `postgres:16` container.

Run with `uv run pytest -m integration`. Nothing here touches a course server:
the container listens on 127.0.0.1 only and is removed when the module ends.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa
from alembic import command
from conftest import replace

from isel.golden import golden_table, pull_golden
from isel.io import read_json, write_parquet_atomic
from isel.manifest import write_run
from isel.pg import ReadGuard, ReadOnlyViolation, open_crawlerdb, open_metricdb
from isel.publish import load_selection, rollback, swap, verify_live
from isel.pull import CRAWL_COLUMNS, FULL_SCHEMA, estimate_full, pull_dims, pull_full
from isel.recrawl import (
    archive_cycle,
    capacity_report,
    host_id,
    pace_count_cap,
    pg_doc_id,
    plan_cycle,
    sync,
)
from isel.selectdb import (
    ForbiddenTarget,
    NeedsBaselineStamp,
    _alembic_config,
    connect,
    init_role,
    migrate,
)
from isel.urlnorm import DomainKeyer, ShardRouter

pytestmark = pytest.mark.integration

SHARDS = 4
ALLOWED = datetime(2026, 10, 8, 5, 20, tzinfo=UTC)   # inside a read window
FETCHED = datetime(2026, 9, 1, tzinfo=UTC)
URL_TABLE = """CREATE TABLE url_state_current_{:03d} (
    url text PRIMARY KEY, domain_id bigint, last_fetch_ok timestamptz, title text,
    anchor_text text, discovery_source_type smallint, robots_bits smallint,
    is_redirect boolean, redirect_hop_count integer, has_json_ld boolean,
    hreflang_count integer, last_modified text, cache_control text,
    inlink_count_approx bigint, inlink_count_external bigint, url_score double precision,
    source smallint, num_fetch_ok_90d integer)"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _router() -> ShardRouter:
    return ShardRouter(DomainKeyer([], offline=True), SHARDS, {})


def _urls() -> list[str]:
    plain = [f"https://site{d}.com/p{i}" for d in range(40) for i in range(25)]
    # Quotes, commas, newlines, backslashes and CJK, so the CSV full export is checked
    # against psycopg's own decoding; ten of them keep len(_urls()) a multiple of 10.
    tricky = [f'https://tricky{i}.example/a?q="x,{i}"\n\\p/中文/NULL' for i in range(10)]
    return plain + tricky


def _load(dsn: str) -> None:
    router = _router()
    with psycopg.connect(dsn, autocommit=True) as conn:
        for shard in range(SHARDS):
            conn.execute(URL_TABLE.format(shard))
        for n, url in enumerate(_urls()):
            shard = router.shard_for_key(router.keyer.key(url))
            fetched = None if n % 10 == 0 else FETCHED + timedelta(minutes=n, microseconds=7 * n)
            conn.execute(
                f"INSERT INTO url_state_current_{shard:03d} VALUES "
                "(%s, %s, %s, %s, %s, %s, 1, %s, 0, true, 0, NULL, 'max-age=60', %s, %s, %s, "
                "0, 1)", (url, n // 25, fetched, "t" * 500, "a" if n % 2 else None,
                          None if n % 4 == 0 else n % 5, None if n % 5 == 0 else n % 3 == 0,
                          n % 7, n % 3, n / 3))
        conn.execute("CREATE TABLE shard_split_subdomain (host text, migrated_at timestamptz)")
        conn.execute("INSERT INTO shard_split_subdomain VALUES ('news.site1.com', NULL)")
        conn.execute("CREATE TABLE metric_batches (id bigint, created_at timestamptz)")
        conn.execute("CREATE TABLE metric_queries (id bigint, batch_id bigint, keyword text, "
                     "frequency bigint, tags text[])")
        conn.execute("CREATE TABLE metric_url (query_id bigint, rank integer, url text)")
        conn.execute("INSERT INTO metric_batches VALUES (1, '2026-09-16 12:00+00')")
        conn.execute("INSERT INTO metric_queries VALUES (1, 1, 'q1', 900, '{head}'), "
                     "(2, 1, 'q2', 50, '{}')")
        conn.execute("INSERT INTO metric_url VALUES (1, 1, 'https://site1.com/p1'), "
                     "(1, 2, 'https://nowhere.example/')")
        conn.execute("ANALYZE")


@pytest.fixture(scope="module")
def dsn():
    name = f"isel-it-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    password = uuid.uuid4().hex
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e",
                    f"POSTGRES_PASSWORD={password}", "-p", f"127.0.0.1:{port}:5432",
                    "postgres:16"], check=True, capture_output=True)
    url = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(url, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        _load(url)
        yield url
    finally:
        subprocess.run(["docker", "stop", name], check=False, capture_output=True)


@pytest.fixture
def cfg(base_cfg, tmp_path, dsn, monkeypatch):
    monkeypatch.setenv(base_cfg.crawlerdb.dsn_env, dsn)
    monkeypatch.setenv(base_cfg.metricdb.dsn_env, dsn)
    cfg = replace(base_cfg, "run", work_dir=str(tmp_path))
    cfg = replace(cfg, "crawlerdb", num_shards=SHARDS)
    cfg = replace(cfg, "routing", overrides={})
    return replace(cfg, "pull", min_interval_s=0.0)


def _session(cfg, opener=open_crawlerdb):
    session = opener(cfg)
    session.guard = ReadGuard(cfg.guard, session.io_waiters, now=lambda: ALLOWED)
    return session


def test_dims_read_real_rows(cfg, tmp_path):
    session = _session(cfg)
    try:
        assert session.io_waiters() >= 0
        assert pull_dims(cfg, session, tmp_path / "dims")["split_hosts"] == 1
    finally:
        session.close()


def test_the_server_refuses_writes_and_the_gate_stops_bypasses(cfg):
    session = _session(cfg)
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            session.conn.execute("CREATE TABLE should_not_exist (a int)")
        with pytest.raises(ReadOnlyViolation):
            session.fetchall("SELECT \"set_config\"('default_transaction_read_only', 'off', false)")
        session.set_statement_timeout(300)
        assert session.fetchone("SHOW statement_timeout") == ("5min",)
        assert session.fetchone("SHOW default_transaction_read_only") == ("on",)
    finally:
        session.close()


def test_golden_pull_reads_metricdb_tables(cfg, tmp_path):
    session = _session(cfg, open_metricdb)
    try:
        summary = pull_golden(session, tmp_path / "golden", cfg.golden.tags)
    finally:
        session.close()
    assert summary["1"]["head"]["distinct_raw_urls"] == 2
    pool = pq.read_table(tmp_path / "golden" / "pool.parquet").to_pylist()
    assert sorted((r["keyword"], r["tags"]) for r in pool) == [("q1", ["head"]), ("q2", [])]


def _files(root):
    return pa.concat_tables([pq.read_table(p) for p in sorted(root.glob("shard=*/part-*.parquet"))])


def test_full_export_reads_every_crawled_row_once(cfg, tmp_path, dsn):
    cfg = replace(cfg, "full", chunk_blocks=1, chunks_per_file=3, min_free_gb=0.0)
    sessions = [_session(cfg), _session(cfg)]
    try:
        assert estimate_full(cfg, sessions[0])["chunks"] > SHARDS
        out = pull_full(cfg, sessions, tmp_path / "export", now=ALLOWED)
    finally:
        for session in sessions:
            session.close()
    exported = _files(tmp_path / "export")
    crawled = [u for n, u in enumerate(_urls()) if n % 10 != 0]
    assert out["shards_missing"] == 0 and out["rows"] == exported.num_rows == len(crawled)
    assert sorted(exported.column("url").to_pylist()) == sorted(crawled)
    assert exported.schema.equals(FULL_SCHEMA, check_metadata=False)
    meta = read_json(tmp_path / "export" / "_meta.json")
    assert "finished_at" in meta and "end_probe" in meta
    assert set(meta["io"]) == {str(s) for s in range(SHARDS)}


def test_the_csv_full_export_equals_psycopg_decoding(cfg, tmp_path, dsn):
    cfg = replace(cfg, "full", chunk_blocks=1, chunks_per_file=3, min_free_gb=0.0)
    session = _session(cfg)
    try:
        pull_full(cfg, [session], tmp_path / "export", now=ALLOWED)
    finally:
        session.close()
    columns = ", ".join(f"{expr} AS {name}" for name, expr, _ in CRAWL_COLUMNS)
    rows = []
    with psycopg.connect(dsn) as conn:
        for shard in range(SHARDS):
            cur = conn.execute(f"SELECT {columns} FROM url_state_current_{shard:03d} "
                               "WHERE last_fetch_ok IS NOT NULL")
            names = [d.name for d in cur.description]
            rows += [{**dict(zip(names, r, strict=True)), "shard": shard} for r in cur]
    decoded = pa.Table.from_pylist(rows, schema=FULL_SCHEMA)
    csv = _files(tmp_path / "export").cast(FULL_SCHEMA)
    assert csv.sort_by("url").equals(decoded.sort_by("url"))
    assert sum("\n" in u for u in csv.column("url").to_pylist()) == 9


OPEN = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
FROZEN = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def selectdb(cfg, dsn, monkeypatch):
    """A fresh database in the throwaway container standing in for our selectdb."""
    name = f"selectdb_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    server = dsn.rsplit("/", 1)[0]
    password = uuid.uuid4().hex
    monkeypatch.setenv(cfg.selectdb.admin_dsn_env, f"{server}/{name}")
    monkeypatch.setenv(cfg.selectdb.reader_password_env, password)
    host = server.split("@")[1]
    monkeypatch.setenv(cfg.selectdb.reader_dsn_env, f"postgresql://select:{password}@{host}/{name}")
    return cfg


# The tables `init-selectdb` made before Alembic, and the state a load and a swap left
# on the live selectdb (2026-10-12): current carries run 1's names and default.
LEGACY = """
CREATE TABLE model_runs (id BIGSERIAL PRIMARY KEY, run_label TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), cutoff_datetime TIMESTAMPTZ, model_path TEXT,
    train_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    eval_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    selection_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_paths JSONB NOT NULL DEFAULT '{}'::jsonb, status TEXT NOT NULL DEFAULT 'loading',
    activated_at TIMESTAMPTZ, config_sha256 TEXT, code_version TEXT);
CREATE TABLE selected_urls_prev (url TEXT PRIMARY KEY, score DOUBLE PRECISION NOT NULL,
    first_seen TIMESTAMPTZ,
    run_id BIGINT NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
ALTER TABLE selected_urls_prev RENAME CONSTRAINT selected_urls_prev_pkey
    TO selected_urls_current_pkey;
ALTER TABLE selected_urls_prev RENAME CONSTRAINT selected_urls_prev_run_id_fkey
    TO selected_urls_current_run_id_fkey;
INSERT INTO model_runs (run_label, status, activated_at) VALUES ('v2', 'live', now());
CREATE TABLE selected_urls_current (url TEXT NOT NULL, score DOUBLE PRECISION NOT NULL,
    first_seen TIMESTAMPTZ, run_id BIGINT NOT NULL DEFAULT 1,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO selected_urls_current (url, score) VALUES ('https://v2.example/', 1.0);
ALTER TABLE selected_urls_current ADD CONSTRAINT selected_urls_r1_pkey PRIMARY KEY (url);
ALTER TABLE selected_urls_current ADD CONSTRAINT selected_urls_r1_run_fk FOREIGN KEY (run_id)
    REFERENCES model_runs(id) ON DELETE CASCADE;
"""


def _admin(cfg):
    return connect(cfg, cfg.selectdb.admin_dsn_env, read_only=False)


def _setup(cfg):
    """migrate-selectdb as the CLI runs it on an empty selectdb."""
    result = migrate(_admin(cfg))
    admin = _admin(cfg)
    try:
        init_role(cfg, admin)
    finally:
        admin.close()
    return result


def _constraints(conn, table):
    cur = conn.execute("SELECT conname, contype, confdeltype FROM pg_constraint "
                       "WHERE conrelid = %s::regclass ORDER BY conname", (f"public.{table}",))
    return [(n, t, d if t == "f" else None) for n, t, d in cur.fetchall()]


def _list_constraints(table):
    return [(f"{table}_pkey", "p", None), (f"{table}_run_id_fkey", "f", "r")]


def test_migrate_adopts_the_pre_alembic_selectdb(selectdb):
    cfg = selectdb
    admin = _admin(cfg)
    admin.execute(LEGACY)
    admin.close()
    with pytest.raises(NeedsBaselineStamp):
        migrate(_admin(cfg))
    first = migrate(_admin(cfg), stamp_baseline=True)
    assert first == {"revision_before": None, "revision": "0003", "stamped_baseline": True}
    assert migrate(_admin(cfg))["revision_before"] == "0003"     # a rerun changes nothing
    with pytest.raises(RuntimeError, match="only fits a pre-Alembic"):
        migrate(_admin(cfg), stamp_baseline=True)
    admin = _admin(cfg)
    try:
        for table in ("selected_urls_current", "selected_urls_prev"):
            assert _constraints(admin, table) == _list_constraints(table)
            (default,) = admin.execute(
                "SELECT column_default FROM information_schema.columns WHERE table_name = %s "
                "AND column_name = 'run_id'", (table,)).fetchone()
            assert default is None
        assert admin.execute("SELECT count(*) FROM selected_urls_current").fetchone() == (1,)
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute("UPDATE model_runs SET status = 'liv'")
        with pytest.raises(psycopg.errors.UniqueViolation):
            admin.execute("INSERT INTO model_runs (run_label, status) VALUES ('x', 'live')")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute("DELETE FROM model_runs")              # RESTRICT keeps the list
    finally:
        admin.close()


def test_migrate_builds_an_empty_selectdb_and_downgrades(selectdb):
    cfg = selectdb
    assert _setup(cfg)["revision"] == "0003"
    dsn = os.environ[cfg.selectdb.admin_dsn_env]
    engine = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    acfg = _alembic_config()
    with engine.begin() as conn:
        acfg.attributes["connection"] = conn
        command.downgrade(acfg, "base")
        assert sa.inspect(conn).get_table_names() == ["alembic_version"]
        assert "recrawl" not in sa.inspect(conn).get_schema_names()
        command.upgrade(acfg, "head")
        command.check(acfg)
    engine.dispose()


def test_the_reader_role_cannot_see_the_recrawl_schema(selectdb):
    cfg = selectdb
    _setup(cfg)
    reader = connect(cfg, cfg.selectdb.reader_dsn_env, read_only=True)
    try:
        assert reader.execute("SELECT count(*) FROM selected_urls_current").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.execute("SELECT count(*) FROM recrawl.doc")
    finally:
        reader.close()


def test_publish_refuses_a_selectdb_behind_the_newest_revision(selectdb, tmp_path):
    cfg = selectdb
    admin = _admin(cfg)
    admin.execute(LEGACY)
    try:
        with pytest.raises(RuntimeError, match="run `isel migrate-selectdb`"):
            load_selection(cfg, admin, _selection(cfg, tmp_path, "r1", ["https://a.example/"]),
                           tmp_path)
    finally:
        admin.close()


def _selection(cfg, work, run_id, urls, shards=None):
    con = duckdb.connect()
    con.register("rows", pa.table({
        "url": urls, "crawl_shard": pa.array(shards or [0] * len(urls), pa.int16()),
        "domain_id": pa.array([1] * len(urls), pa.int64()),
        "score": [float(len(urls) - i) for i in range(len(urls))],
        "reason": pa.nulls(len(urls), pa.list_(pa.string()))}))
    sel = work / "full" / "selection"
    write_run(cfg, con, "SELECT * FROM rows", sel, run_id, {
        "scope": "full", "k": len(urls), "candidates": len(urls), "tau": 0.0,
        "model_dir": "/work/models/m0", "snapshot_at": "2026-10-09T05:39:22+00:00",
        "target_batch": 20, "model_meta": {"best_iteration": 7}, "code_version": "test"})
    return sel / f"run={run_id}"


def _count(conn, table="selected_urls_current"):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def test_publish_swap_freeze_rollback_and_the_metric_query(selectdb, tmp_path):
    cfg = selectdb
    first_urls = ["https://site1.com/p1", "https://a.example/x?q=1,2", 'https://b.example/"q"']
    _setup(cfg)
    admin = _admin(cfg)
    try:
        first = load_selection(cfg, admin, _selection(cfg, tmp_path, "r1", first_urls), tmp_path)
        assert _count(admin) == 0                       # loaded, not visible yet
        assert swap(cfg, admin, first["db_run_id"], now=lambda: OPEN)["swapped"]
        assert _count(admin) == 3
        second = load_selection(cfg, admin, _selection(
            cfg, tmp_path, "r2", ["https://site2.com/p2", "https://site1.com/p1"]), tmp_path)
        frozen = swap(cfg, admin, second["db_run_id"], now=lambda: FROZEN)
        assert frozen == {"swapped": False, "reason": "swap freeze window", "retry": True}
        assert swap(cfg, admin, second["db_run_id"], now=lambda: OPEN)["swapped"]
        assert _count(admin) == 2
        assert admin.execute("SELECT DISTINCT run_id FROM selected_urls_current").fetchall() == [
            (second["db_run_id"],)]
        for table in ("selected_urls_current", "selected_urls_prev"):
            assert _constraints(admin, table) == _list_constraints(table)
        assert rollback(cfg, admin)["live_run"] == first["db_run_id"]
        assert _count(admin) == 3
        for table in ("selected_urls_current", "selected_urls_prev"):
            assert _constraints(admin, table) == _list_constraints(table)
        (default,) = admin.execute(
            "SELECT column_default FROM information_schema.columns WHERE table_name = "
            "'selected_urls_current' AND column_name = 'run_id'").fetchone()
        assert default is None
    finally:
        admin.close()
    write_parquet_atomic(golden_table([
        (20, OPEN, 1, "q", 1, ["head"], 1, "https://site1.com/p1"),
        (20, OPEN, 1, "q", 1, ["head"], 2, "https://a.example/x?q=1,2"),
        (20, OPEN, 2, "r", 1, ["head"], 1, "https://nowhere.example/")]),
        tmp_path / "golden" / "golden.parquet")
    result = verify_live(cfg, tmp_path, 20)
    head = result["tags"]["head"]
    assert head["golden_urls"] == 3 and head["indexed"] == 2
    assert head["indexed_exact_rate"] == pytest.approx(2 / 3)
    assert [r["run_label"] for r in result["live_runs"]] == ["r1"]
    reader = connect(cfg, cfg.selectdb.reader_dsn_env, read_only=True)
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            reader.execute("DELETE FROM selected_urls_current")
    finally:
        reader.close()


def test_writes_refuse_a_database_with_crawler_tables(selectdb, dsn, monkeypatch):
    monkeypatch.setenv(selectdb.selectdb.admin_dsn_env, dsn)   # holds url_state_current_000
    with pytest.raises(ForbiddenTarget, match="crawler or metric tables"):
        connect(selectdb, selectdb.selectdb.admin_dsn_env, read_only=False)


# ---------------------------------------------------------------- recrawl

A1, A2, A3, B1 = ("https://a.example/1", "https://a.example/2", "https://a.example/3",
                  "https://b.example/1")
T0 = OPEN
LOG = "recrawl-00-20261015T120000Z-1"


def _recrawl_cfg(cfg, tmp_path):
    root = tmp_path / "recrawl"
    return replace(cfg, "recrawl", queue_root=str(root / "queue"),
                   result_root=str(root / "result"), warc_root=str(root / "warc"),
                   crawler_processes=2, batch_size=2, min_free_gb=0)


def _go_live(cfg, admin, work, run_id, urls, shards):
    loaded = load_selection(cfg, admin, _selection(cfg, work, run_id, urls, shards), work)
    assert swap(cfg, admin, loaded["db_run_id"], now=lambda: OPEN)["swapped"]


def _queue_file(cfg, process, url):
    host = url.split("/")[2]
    return Path(cfg.recrawl.queue_root) / f"crawler_{process:02d}" / f"domain_{host_id(host):06d}.json"


def _doc(admin, url, *cols):
    return admin.execute(f"SELECT {', '.join(cols)} FROM recrawl.doc WHERE doc_id = %s",
                         (pg_doc_id(url),)).fetchone()


def _fetch_log(cfg, process, records, warc=b"WARC/1.1"):
    d = Path(cfg.recrawl.result_root) / f"crawler_{process:02d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{LOG}.warc.gz").write_bytes(warc)
    (d / f"{LOG}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return d


def _result(url, batch, at, **kw):
    rec = {"batch_id": batch, "doc_id": pg_doc_id(url), "source_url": url, "final_url": url,
           "http_status": 200, "content_type": "text/html", "etag": '"e1"',
           "last_modified": None, "dispatched_at": at.isoformat(), "fetched_at": at.isoformat(),
           "fail_reason": None, "retry_after_s": None, "warc_file": f"{LOG}.warc.gz",
           "warc_offset": 0, "warc_length": 8, "payload_sha1": "ab"}
    rec.update(kw)
    return rec


def test_recrawl_sync_plan_archive_and_revisit(selectdb, tmp_path):
    cfg = _recrawl_cfg(selectdb, tmp_path)
    _setup(cfg)
    admin = _admin(cfg)
    try:
        # A2 twice: the second spelling canonicalizes onto the same doc.
        urls = [A1, A2, "https://a.example/x/../2", A3, B1]
        _go_live(cfg, admin, tmp_path, "r1", urls, [0, 0, 0, 0, 3])
        synced = sync(cfg, admin, now=lambda: OPEN)
        assert (synced["docs"], synced["dropped"], synced["hosts_due"]) == (4, 0, 2)
        assert capacity_report(cfg, admin)["docs"] == 4

        first = plan_cycle(cfg, admin, T0)
        assert (first["files"], first["docs"]) == (2, 3)
        a_file, b_file = _queue_file(cfg, 0, A1), _queue_file(cfg, 1, B1)
        a = json.loads(a_file.read_text())
        assert a["urls"] == [A1, A2] and a["doc_ids"] == [pg_doc_id(A1), pg_doc_id(A2)]
        assert a["domain_id"] == host_id("a.example") and b_file.exists()
        assert a["recrawl_share"] == cfg.recrawl.share_min    # 3 docs need little of 14 days
        assert _doc(admin, A3, "due_at") == (OPEN,)           # the deadline counts from sync
        assert not list(Path(cfg.recrawl.queue_root).rglob("*.tmp"))
        assert plan_cycle(cfg, admin, T0)["files"] == 0      # one file per host on disk

        a_file.unlink()                                       # the crawler takes it
        second = plan_cycle(cfg, admin, T0 + timedelta(seconds=30))
        assert (second["taken"], second["files"]) == (1, 1)
        assert json.loads(a_file.read_text())["urls"] == [A3]
        assert _doc(admin, A1, "state", "dispatched_at") == ("dispatched", T0 + timedelta(
            seconds=30))

        done = T0 + timedelta(minutes=1)
        records = [_result(A1, a["batch_id"], done),
                   _result(A2, a["batch_id"], done, http_status=503, content_type=None,
                           warc_file=None, retry_after_s=600.0)]
        source = _fetch_log(cfg, 0, records)
        assert archive_cycle(cfg, admin, done) == {"logs": 1, "records": 2, "applied": 2}
        stored = f"20261015/crawler_00/{LOG}.warc.gz"
        assert _doc(admin, A1, "state", "warc_file", "due_at") == (
            "fetched", stored, done + timedelta(days=cfg.recrawl.stale_days))
        assert _doc(admin, A2, "state", "attempts", "fail_reason") == ("failed", 1, "http_503")
        (paused,) = admin.execute("SELECT next_at FROM recrawl.host WHERE host_id = %s",
                                  (host_id("a.example"),)).fetchone()
        assert paused == done + timedelta(seconds=600)
        warc = Path(cfg.recrawl.warc_root) / "20261015" / "crawler_00"
        assert (warc / f"{LOG}.warc.gz").read_bytes() == b"WARC/1.1"
        assert (warc / f"{LOG}.jsonl.gz").exists() and not list(source.iterdir())

        # The same log again changes nothing: the 2xx is not newer, the 503 is stale.
        _fetch_log(cfg, 0, records)
        assert archive_cycle(cfg, admin, done)["applied"] == 0

        # B's file was taken but never answered: the doc goes out again without an attempt.
        b_file.unlink()
        taken = T0 + timedelta(hours=1)
        plan_cycle(cfg, admin, taken)
        late = taken + timedelta(seconds=cfg.recrawl.dispatch_timeout_s + 1)
        assert plan_cycle(cfg, admin, late)["timed_out"] == 1
        assert _doc(admin, B1, "state", "attempts", "dispatched_at") == ("dispatched", 0, None)
        assert json.loads(b_file.read_text())["urls"] == [B1]

        # A fetched doc comes due again after stale_days and is planned first by rank.
        a_file.unlink()
        stale = done + timedelta(days=cfg.recrawl.stale_days, seconds=1)
        revisit = plan_cycle(cfg, admin, stale)
        assert revisit["swept"] == 1
        assert json.loads(a_file.read_text())["urls"] == [A1, A2]
    finally:
        admin.close()


def test_recrawl_sync_takes_docs_off_the_list_and_plan_skips_them(selectdb, tmp_path):
    cfg = _recrawl_cfg(selectdb, tmp_path)
    _setup(cfg)
    admin = _admin(cfg)
    try:
        _go_live(cfg, admin, tmp_path, "r1", [A1, B1], [0, 3])
        sync(cfg, admin, now=lambda: OPEN)
        _go_live(cfg, admin, tmp_path, "r2", [A1], [0])
        assert sync(cfg, admin, now=lambda: OPEN)["dropped"] == 1
        assert _doc(admin, B1, "rank", "state") == (None, "pending")
        assert plan_cycle(cfg, admin, T0)["files"] == 1
        assert not _queue_file(cfg, 1, B1).exists()
        with pytest.raises(RuntimeError, match="freeze window"):
            sync(cfg, admin, now=lambda: FROZEN)
    finally:
        admin.close()


def test_recrawl_plan_asks_a_host_behind_its_deadline_for_share_max(selectdb, tmp_path):
    # The deadline passed 9 s after sync; the backlog count stops at the cap of 2.
    cfg = replace(_recrawl_cfg(selectdb, tmp_path), "recrawl", pace_days=1e-4,
                  plan_interval_s=10.0)
    assert pace_count_cap(cfg) == 2
    _setup(cfg)
    admin = _admin(cfg)
    try:
        _go_live(cfg, admin, tmp_path, "r1", [A1, A2, A3], [0, 0, 0])
        sync(cfg, admin, now=lambda: OPEN)
        plan_cycle(cfg, admin, T0 + timedelta(hours=1))
        a = json.loads(_queue_file(cfg, 0, A1).read_text())
        assert (a["urls"], a["recrawl_share"]) == ([A1, A2], cfg.recrawl.share_max)
    finally:
        admin.close()


def test_recrawl_plan_writes_nothing_when_the_disk_is_short(selectdb, tmp_path):
    cfg = _recrawl_cfg(selectdb, tmp_path)
    cfg = replace(cfg, "recrawl", min_free_gb=10**9)
    _setup(cfg)
    admin = _admin(cfg)
    try:
        _go_live(cfg, admin, tmp_path, "r1", [A1], [0])
        sync(cfg, admin, now=lambda: OPEN)
        assert plan_cycle(cfg, admin, T0)["disk_full"]
        assert _doc(admin, A1, "state") == ("pending",)
    finally:
        admin.close()
