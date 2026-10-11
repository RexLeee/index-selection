"""The read and selectdb paths against a throwaway local `postgres:16` container.

Run with `uv run pytest -m integration`. Nothing here touches a course server:
the container listens on 127.0.0.1 only and is removed when the module ends.
"""

from __future__ import annotations

import socket
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import replace

from isel.golden import golden_table, pull_golden
from isel.io import atomic_write_json, read_json, write_parquet_atomic
from isel.pg import ReadGuard, ReadOnlyViolation, open_crawlerdb, open_metricdb
from isel.publish import (
    ForbiddenTarget,
    connect_selectdb,
    init_selectdb,
    load_selection,
    rollback,
    swap,
    verify_live,
)
from isel.pull import (
    CRAWL_SCHEMA,
    FULL_SCHEMA,
    advance_snapshot,
    estimate_full,
    estimate_sample,
    pull_dims,
    pull_full,
    pull_positives,
    pull_sample,
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
    # against the binary sample; ten of them keep len(_urls()) a multiple of 10.
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
    return replace(cfg, "pull", sample_percent=100.0, min_interval_s=0.0)


def _session(cfg, opener=open_crawlerdb):
    session = opener(cfg)
    session.guard = ReadGuard(cfg.guard, session.io_waiters, now=lambda: ALLOWED)
    return session


def test_sample_and_lookups_read_real_rows(cfg, tmp_path):
    session = _session(cfg)
    try:
        assert session.io_waiters() >= 0
        estimate = estimate_sample(cfg, session)
        assert estimate["table_rows"] == len(_urls())
        assert pull_dims(cfg, session, tmp_path / "dims")["split_hosts"] == 1
        out = pull_sample(cfg, session, tmp_path / "sample", now=ALLOWED)
        assert out["shards_missing"] == 0 and out["shards_failed"] == []
        sample = pq.read_table(tmp_path / "sample")
        assert sample.num_rows == len(_urls()) - len(_urls()) // 10   # last_fetch_ok IS NULL
        assert sample.schema.equals(CRAWL_SCHEMA, check_metadata=False)
        assert {len(t) for t in sample.column("title").to_pylist()} == {cfg.pull.title_chars}
        golden = golden_table([(1, ALLOWED, 1, "q", 1, ["head"], 1, "https://site3.com/p4"),
                               (1, ALLOWED, 1, "q", 1, ["head"], 2, "https://none.example/")])
        report = pull_positives(cfg, session, _router(), golden, tmp_path / "positives")
        assert report["1"]["found_rows"] == 1 and report["1"]["crawled_rows"] == 1
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


def test_advance_snapshot_follows_real_write_counters(cfg, tmp_path, dsn):
    session = _session(cfg)
    try:
        pull_sample(cfg, session, tmp_path / "sample", now=ALLOWED)
        later = ALLOWED + timedelta(hours=1)
        assert advance_snapshot(cfg, session, tmp_path / "sample", now=later)["snapshot_at"] \
            == later.isoformat()
        with psycopg.connect(dsn, autocommit=True) as writer:
            writer.execute("UPDATE url_state_current_000 SET title = 'x' "
                           "WHERE url = (SELECT min(url) FROM url_state_current_000)")
        time.sleep(1.5)   # backends flush table stats at most once per second
        with pytest.raises(RuntimeError, match="changed"):
            advance_snapshot(cfg, session, tmp_path / "sample", now=later + timedelta(hours=1))
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


def test_the_csv_full_export_equals_the_binary_sample(cfg, tmp_path):
    cfg = replace(cfg, "full", chunk_blocks=1, chunks_per_file=3, min_free_gb=0.0)
    session = _session(cfg)
    try:
        pull_sample(cfg, session, tmp_path / "sample", now=ALLOWED)
        pull_full(cfg, [session], tmp_path / "export", now=ALLOWED)
    finally:
        session.close()
    binary = pq.read_table(tmp_path / "sample").select(FULL_SCHEMA.names).cast(FULL_SCHEMA)
    csv = _files(tmp_path / "export").cast(FULL_SCHEMA)
    assert csv.sort_by("url").equals(binary.sort_by("url"))
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


def _selection(work, run_id, urls):
    run = work / "full" / "selection" / f"run={run_id}"
    write_parquet_atomic(pa.table({"url": urls, "score": [float(len(urls) - i)
                                                          for i in range(len(urls))]}),
                         run / "selected.parquet")
    atomic_write_json(run / "_meta.json", {
        "run_id": run_id, "k": len(urls), "rows": len(urls), "candidates": len(urls),
        "boundary_score": 1.0, "tau": 0.0, "model_dir": "/work/models/m0",
        "snapshot_at": "2026-10-09T05:39:22+00:00", "target_batch": 20,
        "model_meta": {"best_iteration": 7}, "config_sha256": "c", "code_version": "test"})
    return run


def _count(conn, table="selected_urls_current"):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def test_publish_swap_freeze_rollback_and_the_metric_query(selectdb, tmp_path):
    cfg = selectdb
    first_urls = ["https://site1.com/p1", "https://a.example/x?q=1,2", 'https://b.example/"q"']
    admin = connect_selectdb(cfg, cfg.selectdb.admin_dsn_env, read_only=False)
    try:
        init_selectdb(cfg, admin)
        first = load_selection(cfg, admin, _selection(tmp_path, "r1", first_urls), tmp_path)
        assert _count(admin) == 0                       # loaded, not visible yet
        assert swap(cfg, admin, first["db_run_id"], now=lambda: OPEN)["swapped"]
        assert _count(admin) == 3
        second = load_selection(cfg, admin, _selection(
            tmp_path, "r2", ["https://site2.com/p2", "https://site1.com/p1"]), tmp_path)
        frozen = swap(cfg, admin, second["db_run_id"], now=lambda: FROZEN)
        assert frozen == {"swapped": False, "reason": "swap freeze window", "retry": True}
        assert swap(cfg, admin, second["db_run_id"], now=lambda: OPEN)["swapped"]
        assert _count(admin) == 2
        assert rollback(cfg, admin)["live_run"] == first["db_run_id"]
        assert _count(admin) == 3
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
    reader = connect_selectdb(cfg, cfg.selectdb.reader_dsn_env, read_only=True)
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            reader.execute("DELETE FROM selected_urls_current")
    finally:
        reader.close()


def test_writes_refuse_a_database_with_crawler_tables(selectdb, dsn, monkeypatch):
    monkeypatch.setenv(selectdb.selectdb.admin_dsn_env, dsn)   # holds url_state_current_000
    with pytest.raises(ForbiddenTarget, match="crawler or metric tables"):
        connect_selectdb(selectdb, selectdb.selectdb.admin_dsn_env, read_only=False)
