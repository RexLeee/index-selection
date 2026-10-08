from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import replace
from psycopg import errors

from isel.io import read_json
from isel.pg import assert_read_only_sql
from isel.pull import (
    CRAWL_SCHEMA,
    advance_snapshot,
    estimate_sample,
    lookup_sql,
    pull_sample,
    sample_sql,
    timeout_for,
)

T0 = datetime(2026, 10, 8, 5, 20, tzinfo=UTC)
T1 = datetime(2026, 10, 14, 20, 10, tzinfo=UTC)


class FakeSession:
    """Answers the catalog and probe queries `pull` sends; COPY returns no rows."""

    def __init__(self, relpages: dict[str, int], probe: tuple, failing: set[str] = frozenset()):
        self.relpages = relpages
        self.probe = probe
        self.failing = set(failing)
        self.timeout_s = 120
        self.copies: list[tuple[str, int]] = []

    def fetchone(self, query, params=None):
        assert_read_only_sql(query)
        if "pg_class" in query:
            return (self.relpages[params[0]] * 50, self.relpages[params[0]])
        if "pg_stat_user_tables" in query:
            return self.probe
        raise AssertionError(query)

    def set_statement_timeout(self, seconds):
        self.timeout_s = seconds

    def copy_rows(self, query, type_oids):
        assert_read_only_sql(query)
        self.copies.append((query, self.timeout_s))
        if any(f"FROM {table} " in query for table in self.failing):
            raise errors.QueryCanceled("canceling statement due to statement timeout")
        yield from ()


PROBE = (datetime(2026, 10, 6, 8, 2, 16, tzinfo=UTC), 123_456)


@pytest.mark.parametrize("relpages, expected", [
    (10_339_355, 170),        # shard 224: 847 MB at 5 MB/s
    (1_000, 120),             # floor
    (1_000_000_000, 900),     # cap
])
def test_timeout_scales_with_pages_read(base_cfg, relpages, expected):
    assert timeout_for(base_cfg, relpages) == expected


def test_sample_reads_diagnostic_and_title_columns(base_cfg):
    text = sample_sql(base_cfg, 3)
    assert "num_fetch_ok_90d" in text
    assert f"left(title, {base_cfg.pull.title_chars})" in text
    assert {"num_fetch_ok_90d", "title"} <= set(CRAWL_SCHEMA.names)
    assert_read_only_sql(lookup_sql(base_cfg, 3))


def test_pull_sample_sets_a_timeout_per_shard_and_records_the_probe(tmp_path, base_cfg):
    cfg = replace(base_cfg, "pull", shards=[0, 224])
    session = FakeSession({"url_state_current_000": 1_000,
                           "url_state_current_224": 10_339_355}, PROBE)
    pull_sample(cfg, session, tmp_path, now=T0)
    assert [(q.split("FROM ")[1].split()[0], s) for q, s in session.copies] == [
        ("url_state_current_000", 120), ("url_state_current_224", 170)]
    meta = read_json(tmp_path / "_meta.json")
    assert meta["snapshot_at"] == T0.isoformat()
    assert meta["frozen_probe"] == {"postmaster_start": PROBE[0].isoformat(),
                                    "tuple_changes": PROBE[1], "probed_at": T0.isoformat()}


def _finished_sample(tmp_path, cfg):
    session = FakeSession({"url_state_current_000": 1_000}, PROBE)
    pull_sample(replace(cfg, "pull", shards=[0]), session, tmp_path, now=T0)


def test_advance_snapshot_moves_s0_when_crawlerdb_is_unchanged(tmp_path, base_cfg):
    _finished_sample(tmp_path, base_cfg)
    out = advance_snapshot(base_cfg, FakeSession({}, PROBE), tmp_path, now=T1)
    meta = read_json(tmp_path / "_meta.json")
    assert meta["snapshot_at"] == T1.isoformat() == out["snapshot_at"]
    assert meta["snapshot_initial"] == T0.isoformat()


@pytest.mark.parametrize("probe", [
    (PROBE[0], PROBE[1] + 1),                                   # rows written
    (datetime(2026, 10, 9, 0, 0, tzinfo=UTC), PROBE[1]),        # server restarted
])
def test_advance_snapshot_refuses_when_crawlerdb_changed(tmp_path, base_cfg, probe):
    _finished_sample(tmp_path, base_cfg)
    with pytest.raises(RuntimeError, match="changed"):
        advance_snapshot(base_cfg, FakeSession({}, probe), tmp_path, now=T1)
    assert read_json(tmp_path / "_meta.json")["snapshot_at"] == T0.isoformat()


def test_advance_snapshot_needs_a_finished_sample(tmp_path, base_cfg):
    with pytest.raises(RuntimeError, match="finished"):
        advance_snapshot(base_cfg, FakeSession({}, PROBE), tmp_path, now=T1)


def _tables(*shards):
    return {f"url_state_current_{s:03d}": 1_000 for s in shards}


def test_a_partial_pull_is_not_a_finished_sample(tmp_path, base_cfg):
    cfg = replace(base_cfg, "pull", shards=[0, 1])
    out = pull_sample(cfg, FakeSession(_tables(0, 1), PROBE), tmp_path, shards=[0], now=T0)
    assert out["shards_missing"] == 1
    assert "finished_at" not in read_json(tmp_path / "_meta.json")
    with pytest.raises(RuntimeError, match="finished"):
        advance_snapshot(cfg, FakeSession({}, PROBE), tmp_path, now=T1)
    pull_sample(cfg, FakeSession(_tables(0, 1), PROBE), tmp_path, now=T0)
    assert "finished_at" in read_json(tmp_path / "_meta.json")


def test_a_timed_out_shard_is_recorded_and_retried(tmp_path, base_cfg):
    cfg = replace(base_cfg, "pull", shards=[0, 1, 2])
    session = FakeSession(_tables(0, 1, 2), PROBE, failing={"url_state_current_001"})
    out = pull_sample(cfg, session, tmp_path, now=T0)
    assert out["shards_failed"] == [1] and out["shards_done"] == 2
    assert len(session.copies) == 3
    meta = read_json(tmp_path / "_meta.json")
    assert meta["shards_failed"] == {"1": "QueryCanceled"} and "finished_at" not in meta
    retry = FakeSession(_tables(0, 1, 2), PROBE)
    out = pull_sample(cfg, retry, tmp_path, now=T1)
    assert [q.split("FROM ")[1].split()[0] for q, _ in retry.copies] == ["url_state_current_001"]
    meta = read_json(tmp_path / "_meta.json")
    assert meta["shards_failed"] == {} and "finished_at" in meta
    assert meta["snapshot_at"] == T0.isoformat()


def test_estimate_lists_every_shard(base_cfg):
    session = FakeSession({"url_state_current_000": 1_000,
                           "url_state_current_224": 10_339_355}, PROBE)
    est = estimate_sample(base_cfg, session, [0, 224])
    assert est["shards"] == {"0": {"rows": 50_000, "pages": 1_000, "timeout_s": 120},
                             "224": {"rows": 516_967_750, "pages": 10_339_355,
                                     "timeout_s": 170}}
    assert est["slowest_shard"] == 224 and est["timeout_s_sum"] == 290


def test_a_done_shard_whose_file_is_gone_is_pulled_again(tmp_path, base_cfg):
    cfg = replace(base_cfg, "pull", shards=[0, 1])
    pull_sample(cfg, FakeSession(_tables(0, 1), PROBE), tmp_path, now=T0)
    (tmp_path / "shard=001.parquet").unlink()
    failing = FakeSession(_tables(0, 1), PROBE, failing={"url_state_current_001"})
    out = pull_sample(cfg, failing, tmp_path, now=T1)
    meta = read_json(tmp_path / "_meta.json")
    assert out["shards_missing"] == 1 and meta["shards_done"] == [0]
    assert "finished_at" not in meta
