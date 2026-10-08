from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from conftest import BASE, FakeConn

from isel.cli import _ids
from isel.config import ConfigError, load_config
from isel.features import LeakageError, resolve_set
from isel.pg import Pacer, ReadOnlySession, ReadOnlyViolation, assert_read_only_sql
from isel.pull import probes, sample_sql
from isel.urlnorm import DomainKeyer, ShardRouter, canonicalize, md5_number_lower


def _overlay(tmp_path: Path, text: str) -> list[Path]:
    path = tmp_path / "overlay.toml"
    path.write_text(text)
    return [BASE, path]


def test_base_config_loads(base_cfg):
    assert base_cfg.eval.k_headline == 128_000_000
    assert len(base_cfg.sha256()) == 64


@pytest.mark.parametrize("text, message", [
    ("[run]\nunknown = 1\n", "unknown keys"),
    ("[pull]\nsample_percent = 'x'\n", "expected float"),
    ("[crawlerdb.session]\ndefault_transaction_read_only = 'off'\n", "read_only"),
    ("[eval]\nk_headline = 5\n", "k_headline"),
    ("[crawlerdb.session]\nstatement_timeout = '2min'\n", "statement_timeout"),
    ("[metricdb.session]\nstatement_timeout = '1000s'\n", "timeout_cap_s"),
    ("[metricdb.session]\nstandard_conforming_strings = 'off'\n", "standard_conforming"),
    ("[guard]\ntimeout_cap_s = 2700\n", "hourly open gap"),
    ("[guard]\ndaily_window = ['04:40', '02:50']\n", "wrap"),
    ("[guard]\nmonth_window = ['00:00', '25:00']\n", "HH:MM"),
])
def test_config_rejects_bad_values(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_overlay(tmp_path, text))


def test_config_rejects_missing_key(tmp_path):
    text = BASE.read_text().replace('app_name = "isel_r14725023"', "")
    path = tmp_path / "full.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="missing keys"):
        load_config([path])


def test_feature_set_with_forbidden_column_is_rejected(tmp_path):
    cfg = load_config(_overlay(tmp_path, "[features.sets]\nbad = ['log_ext', 'url_score']\n"))
    with pytest.raises(LeakageError, match="forbidden"):
        resolve_set(cfg, "bad")
    cfg = load_config(_overlay(tmp_path, "[features.sets]\nbad = ['nope']\n"))
    with pytest.raises(LeakageError, match="unregistered"):
        resolve_set(cfg, "bad")


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "WITH a AS (SELECT 1) SELECT * FROM a",
    "COPY (SELECT url FROM t TABLESAMPLE SYSTEM (1.0) REPEATABLE (7)) TO STDOUT (FORMAT BINARY)",
    "SELECT 'drop table x' AS s",
    "SELECT $$; set_config($$ AS s, $tag$ it's $$ $tag$ AS t",
    "SELECT E'it\\'s; delete' AS s, 'a''b' AS t",
    "SELECT 1 /* outer /* nested; update */ still comment */",
    'SELECT "update" FROM "my table" WHERE url = ANY(%s)',
    "SELECT a$b FROM t WHERE x = $1",
    "SELECT 'café' AS s, \"名稱\" FROM t WHERE x = %s",
    "COPY (SELECT a FROM t WHERE a SIMILAR TO stdout) TO STDOUT",
    "COPY (SELECT url FROM t WHERE f(url) IN (SELECT 1)) TO STDOUT WITH (FORMAT csv, HEADER true)",
])
def test_read_only_gate_allows_reads(sql):
    assert_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE t SET a = 1",
    "SELECT 1; DELETE FROM t",
    "COPY t FROM STDIN",
    "SELECT * FROM t FOR UPDATE",
    "EXPLAIN ANALYZE SELECT 1",
    "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
    "SELECT * INTO new_table FROM t",
    # COPY must be exactly COPY (query) TO STDOUT; a server-side file is a write.
    "COPY (SELECT url FROM t WHERE url SIMILAR TO stdout) TO '/tmp/x.csv'",
    "COPY (SELECT 1) TO STDOUT (FORMAT csv), pg_terminate_backend(1)",
    "COPY (SELECT 1) TO STDOUT (FORMAT csv) TO '/tmp/x'",
    "COPY t TO STDOUT",
    "COPY (DELETE FROM t RETURNING *) TO STDOUT",
    "COPY (SELECT 1) TO PROGRAM 'rm -rf /'",
])
def test_read_only_gate_rejects_writes(sql):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT set_config('default_transaction_read_only', 'off', false)",
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_cancel_backend(123)",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT pg_reload_conf()",
    "SELECT pg_switch_wal()",
    "SELECT pg_promote()",
    "SELECT SET_CONFIG ('statement_timeout', '0', false)",
    "SELECT pg_create_physical_replication_slot('s')",
    "SELECT pg_stat_reset()",
    "SELECT pg_advisory_lock(1)",
    "SELECT query_to_xml('select 1', true, false, '')",
    # Spellings that slipped past the regex gate before it lexed SQL.
    'SELECT "set_config"(\'default_transaction_read_only\', \'off\', false)',
    "SELECT '--', set_config('default_transaction_read_only', 'off', false)",
    "SELECT E'\\'', set_config('default_transaction_read_only', 'off', false), ''",
    'SELECT pg_catalog."pg_terminate_backend"(1)',
    "SELECT set_config/* gap */('statement_timeout', '0', false)",
    "SELECT pg_stat_statements_reset()",
    "SELECT brin_summarize_new_values('i')",
    "SELECT gin_clean_pending_list('i')",
    "SELECT pg_prewarm('url_state_current_000')",
    # Non-ASCII letters are identifier characters, so `$q$` here is not a quote.
    "SELECT 1 AS x\u00e9$q$, set_config('a', 'b', false), $q$ AS z",
    "SELECT x\u00e9e'\\', set_config('a', 'b', false), ''",
])
def test_read_only_gate_rejects_state_changing_functions(sql):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 'open",
    "SELECT 1 /* open",
    'SELECT "open',
    "SELECT $q$ open",
    'SELECT U&"d\\0061t" FROM t',
    "SELECT $q%s FROM t",   # psycopg would send `$q$1`
])
def test_read_only_gate_rejects_unlexable_sql(sql):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql(sql)


def test_statement_timeout_is_sent_as_a_parameter():
    conn = FakeConn()
    session = ReadOnlySession(conn)
    session.set_statement_timeout(300)
    assert session.timeout_s == 300
    (text, params), = conn.executed
    assert "statement_timeout" in text and params == ("300s",)


@pytest.mark.parametrize("seconds", [0, -5, 1.5, "60"])
def test_statement_timeout_rejects_bad_values(seconds):
    with pytest.raises(ValueError):
        ReadOnlySession(FakeConn()).set_statement_timeout(seconds)


def test_pull_statements_pass_the_gate(base_cfg):
    assert_read_only_sql(sample_sql(base_cfg, 12))
    assert "url_state_current_012" in sample_sql(base_cfg, 12)


def test_pacer_spaces_statements():
    now = [0.0]
    slept = []
    pacer = Pacer(1.0, clock=lambda: now[0], sleep=lambda s: (slept.append(s),
                                                               now.__setitem__(0, now[0] + s)))
    pacer.wait()
    now[0] += 0.25
    pacer.wait()
    assert slept == [0.75]


def test_shard_router_matches_crawler_rule(base_cfg):
    keyer = DomainKeyer(["news.example.co.uk"], offline=True)
    router = ShardRouter(keyer, 256, base_cfg.routing.overrides)
    expected = int(hashlib.md5(b"example.org").hexdigest(), 16) % 256
    assert router.shard_for_key(keyer.key("https://a.b.example.org/x")) == expected
    assert router.shard_for_key(keyer.key("https://en.wikipedia.org/wiki/X")) == 0
    assert router.shard_for_key(keyer.key("https://www.youtube.com/watch?v=1")) == 64
    split = router.candidate_shards("https://news.example.co.uk/a")
    assert len(split) == 2
    assert router.shard_for_key("example.co.uk") in split


def test_probes_group_by_shard(base_cfg):
    router = ShardRouter(DomainKeyer([], offline=True), 256, base_cfg.routing.overrides)
    grouped = probes(router, ["https://en.wikipedia.org/a", "https://fr.wikipedia.org/b"])
    assert grouped == {0: ["https://en.wikipedia.org/a", "https://fr.wikipedia.org/b"]}


def test_canonicalize_and_hash():
    assert canonicalize("https://x.com/a?b=2&a=1#f") == "https://x.com/a?a=1&b=2"
    assert md5_number_lower("x") == int.from_bytes(hashlib.md5(b"x").digest()[8:], "little")


def test_id_ranges():
    assert _ids("0-2,7,5-5") == [0, 1, 2, 5, 7]
    assert _ids(None) is None
