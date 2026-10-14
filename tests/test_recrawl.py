"""Recrawl pieces that need no database: ids, routing, outcomes and the fetch log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from conftest import BASE, replace

from isel.config import ConfigError, load_config
from isel.recrawl import (
    FETCH_LOG_KEYS,
    LOG_NAME_RE,
    RecrawlError,
    classify,
    host_id,
    pace_count_cap,
    pace_share,
    pg_doc_id,
    process_of,
    read_fetch_log,
    run_loop,
)
from isel.urlnorm import md5_number_lower

URL = "https://a.example/x"


def record(url=URL, **kw):
    rec = {k: None for k in FETCH_LOG_KEYS}
    rec.update(batch_id=7, doc_id=pg_doc_id(url), source_url=url, final_url=url,
               http_status=200, content_type="text/html; charset=utf-8",
               dispatched_at="2026-10-15T12:00:00+00:00", fetched_at="2026-10-15T12:00:01+00:00",
               warc_file="recrawl-00-20261015T120000Z-1.warc.gz", warc_offset=0,
               warc_length=10, payload_sha1="ab")
    rec.update(kw)
    return rec


def test_ids_match_duckdb_and_fit_postgres():
    urls = [f"https://h{i}.example/p" for i in range(64)]
    assert any(md5_number_lower(u) >= 1 << 63 for u in urls)   # both branches covered
    con = duckdb.connect()
    for u in urls:
        signed, hid = con.execute(
            "SELECT CASE WHEN md5_number_lower(?) > 9223372036854775807 THEN "
            "(md5_number_lower(?) - 9223372036854775808)::BIGINT - 9223372036854775807 - 1 "
            "ELSE md5_number_lower(?)::BIGINT END, "
            "(md5_number_lower(?) & 9223372036854775807)::BIGINT", [u] * 4).fetchone()
        assert signed == pg_doc_id(u) and -(1 << 63) <= signed < 1 << 63
        assert (signed + (1 << 64)) % (1 << 64) == md5_number_lower(u)
        assert hid == host_id(u) and 0 <= hid < 1 << 63


def test_process_of_follows_the_crawler_shard_split(base_cfg):
    assert [process_of(base_cfg, s) for s in (0, 15, 16, 255)] == [0, 0, 1, 15]


NOW = datetime(2026, 10, 20, tzinfo=UTC)


def test_pace_share_asks_for_what_ends_the_backlog_by_the_deadline(base_cfg):
    r = base_cfg.recrawl
    left = r.pace_days * 86400 / 2
    since = NOW - timedelta(days=r.pace_days / 2)
    half = round(0.5 * left / r.pace_cycle_s)               # needs half of the host
    assert pace_share(base_cfg, half, since, NOW) == pytest.approx(half * r.pace_cycle_s / left)
    assert pace_share(base_cfg, 1, since, NOW) == r.share_min
    assert pace_share(base_cfg, 10**9, since, NOW) == r.share_max
    overdue = NOW - timedelta(days=r.pace_days + 1)
    assert pace_share(base_cfg, 2 * r.plan_interval_s / r.pace_cycle_s, overdue, NOW) == (
        r.share_max)


def test_pace_count_cap_reaches_share_max_for_any_past_since(base_cfg):
    cap = pace_count_cap(base_cfg)
    assert pace_share(base_cfg, cap, NOW, NOW) == base_cfg.recrawl.share_max
    assert pace_share(base_cfg, cap - 2, NOW, NOW) < base_cfg.recrawl.share_max
    tiny = replace(base_cfg, "recrawl", pace_days=1e-4)       # deadline inside one plan cycle
    assert pace_share(tiny, pace_count_cap(tiny), NOW, NOW) == tiny.recrawl.share_max


@pytest.mark.parametrize(("kw", "expected"), [
    ({}, ("fetched", None)),
    ({"content_type": "application/pdf"}, ("gone", "non_html")),
    ({"http_status": 404, "warc_file": None}, ("gone", "http_404")),
    ({"http_status": None, "fail_reason": "robots_disallow"}, ("robots", "robots_disallow")),
    ({"http_status": None, "fail_reason": "robots_crawl_delay"},
     ("robots", "robots_crawl_delay")),
    ({"http_status": None, "fail_reason": "robots_unavailable"},
     ("retry", "robots_unavailable")),
    ({"http_status": 429, "retry_after_s": 30}, ("throttled", "http_429")),
    ({"http_status": None, "fail_reason": "host_backoff"}, ("throttled", "host_backoff")),
    ({"http_status": None, "fail_reason": "timeout"}, ("retry", "timeout")),
    ({"http_status": None, "fail_reason": "gate_refused"}, ("retry", "gate_refused")),
    ({"http_status": 500}, ("retry", "http_500")),
])
def test_classify(kw, expected):
    assert classify(record(**kw)) == expected


def test_classify_rejects_an_html_page_without_its_warc_record():
    with pytest.raises(RecrawlError, match="without a WARC"):
        classify(record(warc_file=None))


def _log(tmp_path, *records):
    path = tmp_path / "recrawl-00-20261015T120000Z-1.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def test_read_fetch_log_keeps_one_record_per_doc_preferring_a_2xx(tmp_path):
    other = "https://b.example/y"
    path = _log(tmp_path,
                record(http_status=None, fail_reason="timeout", warc_file=None),
                record(),
                record(fetched_at="2026-10-15T12:09:00+00:00", http_status=None,
                       fail_reason="timeout", warc_file=None),
                record(other, http_status=503, warc_file=None))
    got = {r["source_url"]: r["outcome"] for r in
           read_fetch_log(path, "recrawl-00-20261015T120000Z-1.warc.gz")}
    assert got == {URL: "fetched", other: "throttled"}


@pytest.mark.parametrize(("rec", "message"), [
    ({**record(), "extra": 1}, "keys differ"),
    (record(doc_id=1), "doc_id does not match"),
    (record(warc_file="recrawl-00-other.warc.gz"), "warc_file is not"),
])
def test_read_fetch_log_enforces_the_contract(tmp_path, rec, message):
    with pytest.raises(RecrawlError, match=message):
        read_fetch_log(_log(tmp_path, rec), "recrawl-00-20261015T120000Z-1.warc.gz")


def test_log_names():
    assert LOG_NAME_RE.match("recrawl-03-20261015T120000Z-12.jsonl").group(1, 2) == (
        "03", "20261015")
    assert LOG_NAME_RE.match("recrawl-03-20261015T120000Z-12.jsonl.open") is None


def test_run_loop_once_returns_the_first_cycle():
    calls = []
    assert run_loop(lambda: calls.append(1) or {"n": len(calls)}, 5.0, once=True,
                    sleep=pytest.fail) == {"n": 1}


def test_recrawl_config_is_strict(tmp_path, base_cfg):
    text = BASE.read_text().replace("sweep_rows = 100000", "")
    path = tmp_path / "isel.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="sweep_rows"):
        load_config([path])
    path.write_text(BASE.read_text().replace("crawler_processes = 16", "crawler_processes = 7"))
    with pytest.raises(ConfigError, match="crawler_processes"):
        load_config([path])
    path.write_text(BASE.read_text().replace("share_max = 0.75", "share_max = 1.0"))
    with pytest.raises(ConfigError, match="share_max"):
        load_config([path])
    path.write_text(BASE.read_text().replace("pace_cycle_s = 6.7", "pace_cycle_s = 1.0"))
    with pytest.raises(ConfigError, match="pace_cycle_s"):
        load_config([path])
    assert replace(base_cfg, "recrawl", batch_size=1).recrawl.batch_size == 1
