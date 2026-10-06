from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import replace

from isel.dataset import tag_weights
from isel.evaluate import Golden, Ranker, _per_query, bootstrap, paired_bootstrap, rates
from isel.golden import golden_table, summarize
from isel.manifest import ManifestError, validate_run, write_run


def _frame(scores_h_sample_label_url):
    s, h, sample, label, url = zip(*scores_h_sample_label_url, strict=True)
    return np.array(s, float), pa.table({
        "h": pa.array(h, pa.uint64()), "is_sample": pa.array(sample),
        "label": pa.array([int(x) for x in label], pa.int32()), "url": pa.array(url)})


def _exact_topk(rows, k):
    sample = sorted((r for r in rows if r[2]), key=lambda r: (-r[0], r[1]))
    if k >= len(sample):
        return {r[4] for r in rows if r[3]}
    bs, bh = sample[k - 1][0], sample[k - 1][1]
    return {r[4] for r in rows if r[3] and (r[0] > bs or (r[0] == bs and r[1] <= bh))}


@pytest.mark.parametrize("k", [1, 3, 5, 8, 50])
def test_ranker_equals_exact_topk_at_full_sample(k):
    rows = [(1.0, 5, True, False, "a"), (2.0, 1, True, True, "b"), (2.0, 9, True, True, "c"),
            (0.5, 2, True, False, "d"), (2.0, 4, True, False, "e"), (3.0, 7, False, True, "f"),
            (0.1, 3, True, True, "g"), (np.nan, 8, True, True, "h")]
    scores, frame = _frame(rows)
    ranker = Ranker(scores, frame, rate=1.0)
    clean = [(-np.inf if np.isnan(r[0]) else r[0], *r[1:]) for r in rows]
    assert ranker.listed_at_rank(ranker.rank_for_k(k)) == _exact_topk(clean, k)


def test_ranker_scales_rank_by_sample_rate():
    rng = np.random.default_rng(0)
    n = 20_000
    scores = rng.normal(size=n)
    frame = pa.table({"h": pa.array(rng.integers(0, 2**63, n), pa.uint64()),
                      "is_sample": pa.array(rng.random(n) < 0.1),
                      "label": pa.array((rng.random(n) < 0.05).astype(np.int32)),
                      "url": pa.array([f"u{i}" for i in range(n)])})
    estimate = Ranker(scores, frame, rate=0.1).listed_at_rank(round(5000 * 0.1))
    labels = frame.column("label").to_numpy().astype(bool)
    exact = {f"u{i}" for i in np.flatnonzero(labels & (scores >= np.sort(scores)[-5000]))}
    overlap = len(estimate & exact) / len(exact)
    assert overlap > 0.9


def test_per_query_weights_shared_urls():
    queries = {1: {"a", "b"}, 2: {"b"}}
    assert _per_query(queries, {"b"}) == [(0.5, 1.5), (0.5, 0.5)]


def test_bootstrap_is_deterministic_and_paired():
    queries = {i: {f"u{i}", f"v{i}"} for i in range(40)}
    hits = {f"u{i}" for i in range(20)}
    assert bootstrap(queries, hits, 200, 1) == bootstrap(queries, hits, 200, 1)
    lo, hi = paired_bootstrap(queries, hits, hits, 200, 1)
    assert lo == hi == 0.0


def test_rates_count_exact_and_canonical():
    g = Golden({1: {"https://a/#x", "https://b/"}}, {"https://a/#x": "https://a/",
                                                    "https://b/": "https://b/"})
    exact, r_exact, r_canon = rates(g, {"https://a/"})
    assert exact == set() and r_exact == 0.0 and r_canon == 0.5


def test_tag_weights_balance_head_and_random():
    head = np.array([1, 1, 1, 0, 0], bool)
    rand = np.array([0, 0, 0, 1, 1], bool)
    w = tag_weights(head, rand)
    assert np.isclose(w.sum(), 5)
    assert np.isclose(w[head].sum(), w[rand].sum())


def test_golden_summary_reports_drift_and_recurrence():
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 15, tzinfo=UTC)
    rows = [(1, t1, 1, "k", 10, ["head"], 1, "https://a.com/x#f"),
            (1, t1, 2, "k2", 5, ["random"], 1, "https://b.com/"),
            (2, t2, 3, "k", 10, ["head"], 1, "https://a.com/x")]
    summary = summarize(golden_table(rows), ["head", "random"])
    assert summary["1"]["head"]["raw_ne_canonical_rate"] == 1.0
    assert summary["2"]["recurring_canonical_rate"] == 1.0
    assert summary["1"]["random"]["queries"] == 1


def _rows(urls, scores):
    return pa.table({"url": urls, "canonical_url": urls, "domain_key": ["d"] * len(urls),
                     "crawl_shard": pa.array([1] * len(urls), pa.int16()),
                     "score": pa.array(scores, pa.float64()),
                     "reason": pa.array([["x:+1"]] * len(urls), pa.list_(pa.string()))})


def test_manifest_runs_deltas_and_validation(tmp_path, base_cfg):
    cfg = replace(base_cfg, "manifest", index_shards=4)
    sel = tmp_path / "selection"
    first = write_run(cfg, _rows(["https://a/", "https://b/", "https://c/"], [3, 2, 1]), sel, "r1")
    assert first["parent_run_id"] is None and first["rows"] == 3
    second = write_run(cfg, _rows(["https://b/", "https://d/"], [5, 4]), sel, "r2")
    assert second["parent_run_id"] == "r1"
    run = sel / "run=r2"
    added = pq.read_table(run / "deltas" / "added.parquet").column("url").to_pylist()
    removed = pq.read_table(run / "deltas" / "removed.parquet").column("url").to_pylist()
    assert added == ["https://d/"] and sorted(removed) == ["https://a/", "https://c/"]
    manifest = pq.read_table(run / "manifest")
    assert sorted(manifest.column("rank").to_pylist()) == [1, 2]
    assert len(list((run / "manifest").glob("index_shard=*.parquet"))) == 4
    assert validate_run(run)["rows"] == 2
    pq.write_table(manifest.slice(0, 1), run / "manifest" / "index_shard=00.parquet")
    with pytest.raises(ManifestError):
        validate_run(run)


def test_manifest_rejects_duplicate_canonical(tmp_path, base_cfg):
    with pytest.raises(ManifestError, match="duplicate"):
        write_run(base_cfg, _rows(["https://a/", "https://a/"], [1, 2]), tmp_path, "r1")
