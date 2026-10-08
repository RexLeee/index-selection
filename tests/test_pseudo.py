from __future__ import annotations

import dataclasses
import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pyarrow.parquet as pq
import pytest
from conftest import BASE, replace

from isel.cli import main
from isel.config import SERP_PROVIDERS, Config
from isel.golden import PSEUDO, golden_table, pool_table, read_golden
from isel.io import read_json
from isel.pseudo import (
    CLIENT_TYPES,
    BudgetExceeded,
    SerpApiClient,
    SerpCache,
    SerperClient,
    SerpLedger,
    TrendItem,
    backfill_selection,
    build_pseudo_batch,
    calibration_report,
    compare_snapshots,
    fetch_serp,
    load_key,
    merge_trending,
    overlap_at_k,
    parse_organic,
    positive_gate,
    read_snapshot_pool,
    run_calibration,
    run_pseudo_batch,
    select_head,
    select_random,
    serp_cache,
    snapshot_selection,
    spearman,
    trends_snapshot,
)

T0 = datetime(2026, 10, 9, 13, 0, tzinfo=UTC)


def _budgets(cfg: Config, **budgets: int) -> Config:
    providers = {name: dataclasses.replace(p, budget=budgets.get(name, p.budget))
                 for name, p in cfg.serp.providers.items()}
    return replace(cfg, "serp", providers=providers)


def test_merge_follows_the_official_rules():
    pool = merge_trending([
        ("US", [TrendItem("Taylor Swift", 500), TrendItem("nfl", 200)]),
        ("GB", [TrendItem("taylor swift", 100), TrendItem("nfl", 2000)]),
        ("JP", [TrendItem("Taylor Swift", None), TrendItem("台風", 200)]),
    ])
    # Exact-string dedup, volume summed across geos (None counts as 0),
    # sorted by frequency with ties kept in first-seen order.
    assert [(q.keyword, q.frequency) for q in pool] == [
        ("nfl", 2200), ("Taylor Swift", 500), ("台風", 200), ("taylor swift", 100)]


def test_head_and_random_selection():
    pool = merge_trending([("US", [TrendItem(f"q{i}", 100 - i) for i in range(50)])])
    assert select_head(pool, 3) == ["q0", "q1", "q2"]
    picked = select_random(pool, 10, seed=7, exclude={"q0", "q1"})
    assert len(set(picked)) == 10 and not {"q0", "q1"} & set(picked)
    assert picked == select_random(pool, 10, seed=7, exclude={"q0", "q1"})
    assert len(select_random(pool, 100, seed=7, exclude=set())) == 50


@pytest.mark.parametrize("client, payload", [
    (SerpApiClient, {"organic_results": [{"link": "https://a/"}, {"title": "no link"},
                                         {"link": "https://b/"}]}),
    (SerperClient, {"organic": [{"link": "https://a/"}, {"link": "https://b/"}],
                    "topStories": [{"link": "https://news/"}]}),
])
def test_parse_organic_keeps_links_in_order(client, payload):
    assert parse_organic(payload, client.organic_key) == ["https://a/", "https://b/"]


def test_every_configured_provider_has_a_client():
    assert set(CLIENT_TYPES) == set(SERP_PROVIDERS)


def test_overlap_at_k_uses_the_reference_as_denominator():
    assert overlap_at_k(["a", "b", "c"], ["b", "c", "d", "e"], 10) == 0.5
    assert overlap_at_k(["a"], [], 10) is None


def test_ledger_resets_a_monthly_budget_but_not_signup_credits(tmp_path, base_cfg):
    providers = _budgets(base_cfg, serpapi=2, serper=2).serp.providers
    ledger = SerpLedger(tmp_path / "ledger.json", providers)
    for provider in ("serpapi", "serper"):
        ledger.charge(provider, T0)
        ledger.charge(provider, T0)
        with pytest.raises(BudgetExceeded):
            ledger.charge(provider, T0)
    reopened = SerpLedger(tmp_path / "ledger.json", providers)
    november = datetime(2026, 11, 1, tzinfo=UTC)
    reopened.charge("serpapi", november)
    with pytest.raises(BudgetExceeded):
        reopened.charge("serper", november)


def test_ledgers_sharing_a_file_count_together(tmp_path, base_cfg):
    providers = _budgets(base_cfg, serper=2).serp.providers
    a = SerpLedger(tmp_path / "ledger.json", providers)
    b = SerpLedger(tmp_path / "ledger.json", providers)
    a.charge("serper", T0)
    b.charge("serper", T0)
    with pytest.raises(BudgetExceeded):
        a.charge("serper", T0)


def test_cache_survives_a_torn_last_line(tmp_path):
    cache = SerpCache(tmp_path / "serper.jsonl")
    cache.put("q1", ["https://a/"], T0)
    with (tmp_path / "serper.jsonl").open("a") as fh:
        fh.write('{"query": "q2", "links": ["htt')
    reopened = SerpCache(tmp_path / "serper.jsonl")
    assert reopened.get("q1") == (["https://a/"], T0)
    assert reopened.get("q2") is None


class FakeClient:
    provider = "serper"

    def __init__(self):
        self.calls: list[str] = []

    def search(self, query):
        self.calls.append(query)
        return [f"https://{query}.example/1", f"https://{query}.example/2"]


def test_fetch_never_asks_twice_for_a_cached_query(tmp_path):
    client = FakeClient()
    first = fetch_serp(client, SerpCache(tmp_path / "serper.jsonl"), ["a", "b"], now=lambda: T0)
    again = fetch_serp(client, SerpCache(tmp_path / "serper.jsonl"), ["a", "b", "c"],
                       now=lambda: T0)
    assert client.calls == ["a", "b", "c"]
    assert first["a"] == again["a"]


def test_pseudo_batch_rows_carry_tags_ranks_and_fetch_time():
    t_created = datetime(2026, 10, 1, 12, tzinfo=UTC)
    serp = {"q1": (["https://a/", "https://b/"], T0), "q2": ([], T0)}
    table = build_pseudo_batch(10019, t_created, {"q1": ["random"], "q2": ["random"]},
                               {"q1": 300, "q2": 5}, serp)
    rows = table.to_pylist()
    assert [(r["raw_url"], r["rank"], r["tags"]) for r in rows] == [
        ("https://a/", 1, ["random"]), ("https://b/", 2, ["random"])]
    assert {r["label_source"] for r in rows} == {PSEUDO}
    assert {r["available_at"] for r in rows} == {T0}
    assert {r["batch_created_at"] for r in rows} == {t_created}
    assert len({r["query_id"] for r in rows}) == 1


def test_load_key_refuses_the_course_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "course-secret")
    monkeypatch.setenv("ISEL_SERPAPI_KEY", "course-secret")
    with pytest.raises(RuntimeError, match="course"):
        load_key("ISEL_SERPAPI_KEY")
    monkeypatch.setenv("ISEL_SERPAPI_KEY", "mine")
    assert load_key("ISEL_SERPAPI_KEY") == "mine"
    monkeypatch.delenv("ISEL_SERPAPI_KEY")
    with pytest.raises(RuntimeError, match="not set"):
        load_key("ISEL_SERPAPI_KEY")


def test_cache_appends_cleanly_after_a_torn_line(tmp_path):
    path = tmp_path / "serper.jsonl"
    path.write_text('{"query": "q1", "links": [], "fetched_at": "2026-10-09T13:00:00+00:00"}\n'
                    '{"query": "q2", "li')
    SerpCache(path).put("q3", ["https://c/"], T0)
    reopened = SerpCache(path)
    assert reopened.get("q3") == (["https://c/"], T0)
    assert reopened.get("q1") == ([], T0)


class Transport:
    """Fake HTTP: replays responses (dicts or exceptions) and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout_s):
        self.requests.append(request)
        out = self.responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def _ledger(tmp_path, cfg, **budgets):
    return SerpLedger(tmp_path / "ledger.json", _budgets(cfg, **budgets).serp.providers)


def _client(kind, tmp_path, cfg, transport, sleep=lambda s: None, ledger=None):
    return kind(cfg.serp, ledger or _ledger(tmp_path, cfg), "k123", transport=transport,
                sleep=sleep, now=lambda: T0)


def test_serpapi_client_matches_the_official_call(tmp_path, base_cfg):
    transport = Transport({"organic_results": [{"link": "https://a/"}]})
    client = _client(SerpApiClient, tmp_path, base_cfg, transport)
    assert client.search("taylor swift") == ["https://a/"]
    params = parse_qs(urlsplit(transport.requests[0].full_url).query)
    assert params == {"engine": ["google"], "q": ["taylor swift"], "num": ["10"],
                      "api_key": ["k123"]}


def test_serpapi_no_results_is_empty_but_other_errors_raise(tmp_path, base_cfg):
    transport = Transport({"error": "Google hasn't returned any results for this query."},
                          {"error": "Your account has run out of searches."})
    client = _client(SerpApiClient, tmp_path, base_cfg, transport)
    assert client.search("x") == []
    with pytest.raises(RuntimeError, match="run out"):
        client.search("y")


def test_serper_client_posts_json_with_the_key_header(tmp_path, base_cfg):
    transport = Transport({"organic": [{"link": "https://a/"}]})
    client = _client(SerperClient, tmp_path, base_cfg, transport)
    assert client.search("nfl") == ["https://a/"]
    request = transport.requests[0]
    assert request.get_method() == "POST" and request.full_url == "https://google.serper.dev/search"
    assert request.get_header("X-api-key") == "k123"
    assert json.loads(request.data) == {"q": "nfl", "num": 10}


def test_client_retries_transient_errors_only(tmp_path, base_cfg):
    slept = []
    ok = _client(SerperClient, tmp_path, base_cfg, sleep=slept.append,
                 transport=Transport(_http_error(503), _http_error(429), {"organic": []}))
    assert ok.search("q") == []
    assert len(slept) >= 2
    denied = Transport(_http_error(401))
    client = _client(SerperClient, tmp_path, base_cfg, denied)
    with pytest.raises(RuntimeError) as info:
        client.search("q")
    assert len(denied.requests) == 1 and "k123" not in str(info.value)


def test_every_http_attempt_is_charged(tmp_path, base_cfg):
    ledger = _ledger(tmp_path, base_cfg, serper=2)
    flaky = _client(SerperClient, tmp_path, base_cfg, ledger=ledger,
                    transport=Transport(TimeoutError(), {"organic": []}))
    assert flaky.search("q") == []
    assert ledger.used("serper", T0) == 2
    broke = _client(SerperClient, tmp_path, base_cfg, ledger=ledger,
                    transport=Transport({"organic": []}))
    with pytest.raises(BudgetExceeded):
        broke.search("q")


class FakeTrends:
    provider = "fake"

    def trending(self, geo, hours):
        assert hours == 168
        return {"US": [TrendItem("nfl", 900), TrendItem("weather", 50)],
                "TW": [TrendItem("颱風", 300), TrendItem("nfl", 100)]}.get(geo, [])


def test_trends_snapshot_writes_the_merged_pool(tmp_path, base_cfg):
    out = trends_snapshot(base_cfg, FakeTrends(), tmp_path, now=T0)
    pool = read_snapshot_pool(Path(out["dir"]))
    assert [(q.keyword, q.frequency) for q in pool] == [
        ("nfl", 1000), ("颱風", 300), ("weather", 50)]
    assert out["queries"] == 3 and out["taken_at"] == T0.isoformat()


class OtherTrends(FakeTrends):
    provider = "other"

    def trending(self, geo, hours):
        return {"US": [TrendItem("nfl", 800), TrendItem("weather", 60), TrendItem("secret", 1)],
                "TW": [TrendItem("颱風", 300), TrendItem("nfl", 100)]}.get(geo, [])


def test_snapshot_comparison_reports_overlap_without_query_text(tmp_path, base_cfg):
    a = Path(trends_snapshot(base_cfg, FakeTrends(), tmp_path, now=T0)["dir"])
    b = Path(trends_snapshot(base_cfg, OtherTrends(), tmp_path,
                             now=datetime(2026, 10, 9, 13, 5, tzinfo=UTC))["dir"])
    a_id, b_id = (d.name.split("=", 1)[1] for d in (a, b))
    out = compare_snapshots(base_cfg, tmp_path, a_id, b_id)
    assert out["geo_jaccard"] == {"US": pytest.approx(2 / 3), "TW": 1.0}
    assert out["mean_geo_jaccard"] == pytest.approx(5 / 6) and out["passed"] is True
    assert out["pool_jaccard"] == pytest.approx(3 / 4) and out["common_queries"] == 3
    assert out["frequency_spearman"] == pytest.approx(1.0)
    saved = (tmp_path / "compare" / f"{a_id}_vs_{b_id}.json").read_text()
    assert "weather" not in saved and "nfl" not in saved


def test_spearman_shares_ranks_between_ties():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2, 3], [5, 5, 6, 7]) == pytest.approx(1.0)
    assert spearman([1, 2], [1, 2]) is None and spearman([1, 1, 1], [1, 2, 3]) is None


def test_snapshot_selection_tags_queries_like_the_official_job():
    pool = merge_trending([("US", [TrendItem(f"q{i}", 100 - i) for i in range(20)])])
    chosen = snapshot_selection(pool, head_n=3, random_n=5, seed=1)
    assert [k for k, tags in chosen.items() if "head" in tags] == ["q0", "q1", "q2"]
    assert sum("random" in tags for tags in chosen.values()) == 5
    assert all(tags for tags in chosen.values())


def test_backfill_selection_ignores_the_pool_row_order():
    t1 = datetime(2026, 10, 1, 12, tzinfo=UTC)
    rows = [(19, t1, i, f"q{i}", 10, []) for i in range(40)]
    forward = backfill_selection(pool_table(rows), 19, n=10, seed=3)[1]
    backward = backfill_selection(pool_table(rows[::-1]), 19, n=10, seed=3)[1]
    assert forward == backward


def test_backfill_selection_skips_queries_the_official_job_used():
    t1 = datetime(2026, 10, 1, 12, tzinfo=UTC)
    pool = pool_table([(19, t1, 1, "used", 900, ["head"]), (19, t1, 2, "a", 50, []),
                       (19, t1, 3, "b", 40, []), (18, t1, 4, "other", 10, [])])
    created, chosen, frequency = backfill_selection(pool, 19, n=5, seed=1)
    assert created == t1
    assert sorted(chosen) == ["a", "b"] and all(t == ["random"] for t in chosen.values())
    assert frequency == {"used": 900, "a": 50, "b": 40}


def test_calibration_separates_provider_and_drift_effects(base_cfg):
    official = {"q1": ["https://a/", "https://b/"], "q2": ["https://c/", "https://d/"]}
    serpapi = {"q1": (["https://a/", "https://b/"], T0), "q2": (["https://c/", "https://x/"], T0)}
    serper = {"q1": (["https://a/", "https://y/"], T0), "q2": (["https://c/", "https://x/"], T0)}
    report = calibration_report(base_cfg, official, serpapi, serper)
    assert report["provider_overlap"] == pytest.approx((0.5 + 1.0) / 2)
    assert report["drift_overlap"] == pytest.approx((1.0 + 0.5) / 2)
    assert report["gates"] == {"provider": True, "drift": True}


def _official_work(tmp_path):
    t1 = datetime(2026, 10, 1, 12, tzinfo=UTC)
    work = tmp_path / "work"
    (work / "golden").mkdir(parents=True)
    pq.write_table(pool_table([(19, t1, 1, "used", 900, ["random"]),
                               (19, t1, 2, "secret invasion", 50, []), (19, t1, 3, "b", 40, [])]),
                   work / "golden" / "pool.parquet")
    pq.write_table(golden_table([(19, t1, 1, "used", 900, ["random"], 1, "https://u/1"),
                                 (19, t1, 1, "used", 900, ["random"], 2, "https://u/2")]),
                   work / "golden" / "golden.parquet")
    return work


def test_run_pseudo_batch_backfills_once(tmp_path, base_cfg):
    work = _official_work(tmp_path)
    ledger = _ledger(tmp_path, base_cfg)
    out = run_pseudo_batch(base_cfg, work, "official:19", 10019, head_n=0, random_n=5,
                           client=FakeClient(), ledger=ledger, now=lambda: T0)
    table = read_golden(work, include_pseudo=True)
    pseudo = [r for r in table.to_pylist() if r["label_source"] == PSEUDO]
    assert {r["batch_id"] for r in pseudo} == {10019} and out["queries"] == 2
    assert {r["keyword"] for r in pseudo} == {"secret invasion", "b"}
    with pytest.raises(FileExistsError):
        run_pseudo_batch(base_cfg, work, "official:19", 10019, head_n=0, random_n=5,
                         client=FakeClient(), ledger=ledger, now=lambda: T0)
    with pytest.raises(ValueError, match="id_offset"):
        run_pseudo_batch(base_cfg, work, "official:19", 19, head_n=0, random_n=5,
                         client=FakeClient(), ledger=ledger, now=lambda: T0)


def test_a_run_the_budget_cannot_cover_spends_nothing(tmp_path, base_cfg):
    work = _official_work(tmp_path)
    client = FakeClient()
    with pytest.raises(BudgetExceeded, match="nothing was spent"):
        run_pseudo_batch(base_cfg, work, "official:19", 10019, head_n=0, random_n=5,
                         client=client, ledger=_ledger(tmp_path, base_cfg, serper=1),
                         now=lambda: T0)
    assert client.calls == []
    # Cached queries need no budget, so a resumed run fits a smaller remainder.
    serp_cache(work, "batch=10019", "serper").put("b", ["https://b/"], T0)
    run_pseudo_batch(base_cfg, work, "official:19", 10019, head_n=0, random_n=5,
                     client=client, ledger=_ledger(tmp_path, base_cfg, serper=1),
                     now=lambda: T0)
    assert client.calls == ["secret invasion"]


class EchoClient(FakeClient):
    def __init__(self, provider, links, log=None):
        super().__init__()
        self.provider = provider
        self.links = links
        self.log = log if log is not None else []

    def search(self, query):
        self.calls.append(query)
        self.log.append((self.provider, query))
        return list(self.links)


def test_run_calibration_writes_numbers_only_to_json(tmp_path, base_cfg):
    work = _official_work(tmp_path)
    summary = run_calibration(base_cfg, work, batch=19, tag="random", n=5,
                              serpapi=EchoClient("serpapi", ["https://u/1", "https://u/2"]),
                              serper=EchoClient("serper", ["https://u/1", "https://z/"]),
                              ledger=_ledger(tmp_path, base_cfg), now=lambda: T0)
    assert summary["queries"] == 1
    assert summary["drift_overlap"] == 1.0 and summary["provider_overlap"] == 0.5
    saved = read_json(work / "pseudo" / "calibration" / "batch=19_random.json")
    assert "per_query" not in saved and saved["gates"] == {"provider": False, "drift": True}
    detail = pq.read_table(work / "pseudo" / "calibration" / "batch=19_random.parquet")
    assert detail.column("query").to_pylist() == ["used"]


def test_calibration_fetches_both_providers_back_to_back(tmp_path, base_cfg):
    work = _official_work(tmp_path)
    t1 = datetime(2026, 10, 1, 12, tzinfo=UTC)
    rows = [(19, t1, q, f"k{q}", 1, ["random"], 1, f"https://u/{q}") for q in range(4)]
    pq.write_table(golden_table(rows), work / "golden" / "golden.parquet")
    serpapi = EchoClient("serpapi", [])
    with pytest.raises(BudgetExceeded):
        run_calibration(base_cfg, work, batch=19, tag="random", n=4, serpapi=serpapi,
                        serper=EchoClient("serper", []),
                        ledger=_ledger(tmp_path / "small", base_cfg, serpapi=3))
    assert serpapi.calls == []
    log: list[tuple[str, str]] = []
    run_calibration(base_cfg, work, batch=19, tag="random", n=4,
                    serpapi=EchoClient("serpapi", [], log), serper=EchoClient("serper", [], log),
                    ledger=_ledger(tmp_path, base_cfg), now=lambda: T0)
    assert [p for p, _ in log] == ["serpapi", "serper"] * 4
    assert [q for _, q in log][::2] == [q for _, q in log][1::2]
    with pytest.raises(ValueError, match="tag"):
        run_calibration(base_cfg, work, batch=19, tag="nope", n=4,
                        serpapi=EchoClient("serpapi", []), serper=EchoClient("serper", []),
                        ledger=_ledger(tmp_path, base_cfg))


def test_cli_refuses_to_run_without_our_own_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ISEL_SERPER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ISEL_SERPER_KEY is not set"):
        main(["--config", str(BASE), "pseudo-batch", "--source", "official:19",
              "--batch-id", "10019", "--random", "5", "--provider", "serper"])


def test_positive_gate_compares_a_backfill_with_its_own_official_batch(base_cfg):
    summary = {"19": {"golden_urls": 1000, "positive_rows": 200},
               "10019": {"golden_urls": 500, "positive_rows": 70}}
    gate = positive_gate(base_cfg, summary, {"source": "official:19"}, 10019)
    assert gate["official_rate"] == 0.2 and gate["pseudo_rate"] == 0.14
    assert gate["ratio"] == pytest.approx(0.7) and gate["passed"] is True
    low = {**summary, "10019": {"golden_urls": 500, "positive_rows": 50}}
    assert positive_gate(base_cfg, low, {"source": "official:19"}, 10019)["passed"] is False
    assert positive_gate(base_cfg, summary, {"source": "snapshot:x"}, 10019)["passed"] is None
