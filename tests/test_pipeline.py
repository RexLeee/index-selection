from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest
from conftest import replace, small_cfg
from synth import SNAPSHOT, make_world, write_pseudo, write_work_dir

from isel.backtest import forward_eval, run_backtest, select_sample, train_frozen
from isel.dataset import train_batches
from isel.features import FeatureStore
from isel.golden import PSEUDO, golden_table, pseudo_batch_path
from isel.io import atomic_write_json, open_duckdb, read_json, write_parquet_atomic
from isel.manifest import validate_run


@pytest.fixture(scope="module")
def world():
    return make_world()


@pytest.fixture
def work(tmp_path, world):
    write_work_dir(world, tmp_path)
    return tmp_path


def _store(cfg, work, next_batch=False):
    return FeatureStore(cfg, open_duckdb(cfg, work / "tmp"), work, next_batch)


def test_pool_excludes_rows_fetched_after_as_of(work):
    cfg = small_cfg(work)
    store = _store(cfg, work)
    for batch in (3, 6):
        b = store.batch(batch)
        frame = store.frame(batch, "eval")
        (late,) = store.con.execute(
            f"SELECT count(*) FROM crawl WHERE last_fetch_ok >= '{b.as_of.isoformat()}'"
        ).fetchone()
        assert late > 0
        assert frame.num_rows == store.pool_size(batch) + int(pc.sum(pc.invert(
            frame.column("is_sample"))).as_py() or 0)
        assert min(frame.column("fetch_age_days").to_pylist()) > 0


def test_future_batches_do_not_change_features(work, world):
    cfg = small_cfg(work)
    before = _store(cfg, work).frame(5, "eval").sort_by([("url", "ascending")])
    rows = list(world.golden_rows)
    rows += [(6, world.batches[6], 99999, "x", 1, ["head"], 1, p["url"])
             for p in world.pages[:300]]
    rows += [(5, world.batches[5], 99998, "y", 1, ["head"], 1, world.pages[1]["url"])]
    write_parquet_atomic(golden_table(rows), work / "golden" / "golden.parquet")
    after = _store(cfg, work).frame(5, "eval").sort_by([("url", "ascending")])
    common = after.filter(pc.is_in(after.column("url"), value_set=before.column("url")))
    assert common.num_rows == before.num_rows
    for name in ("hist_decayed", "hist_days_since", "lift_hp", "lift_dkey", "dem_dkey",
                 "log_size_dkey", "rel_inlinks"):
        a = np.nan_to_num(np.array(before.column(name).to_pylist(), float), nan=-9)
        b = np.nan_to_num(np.array(common.column(name).to_pylist(), float), nan=-9)
        assert np.allclose(a, b), name


def test_training_batches_precede_the_test_batch(work):
    cfg = small_cfg(work)
    store = _store(cfg, work)
    for t in (5, 8):
        test = store.batch(t)
        chosen = train_batches(cfg, store, t)
        assert chosen and t not in chosen
        assert all(store.batch(b).created_at <= test.as_of for b in chosen)


PSEUDO_ID = 10005
PSEUDO_FETCHED = datetime(2026, 5, 15, tzinfo=UTC)   # after batch 8's as-of, before S0


def _with_pseudo(work, world):
    urls = write_pseudo(world, work, PSEUDO_ID, world.batches[5], PSEUDO_FETCHED)
    return replace(small_cfg(work), "golden", use_pseudo=True), urls


def _sorted_frame(store, batch):
    return store.frame(batch, "eval").sort_by([("url", "ascending"), ("shard", "ascending")])


def test_pseudo_fetched_after_as_of_leaves_backtests_unchanged(work, world):
    cfg = small_cfg(work)
    cfg_p, _ = _with_pseudo(work, world)
    plain, with_p = _store(cfg, work), _store(cfg_p, work)
    assert PSEUDO_ID in [b.batch_id for b in with_p.batches]
    for t in (6, 8):
        a, b = _sorted_frame(plain, t), _sorted_frame(with_p, t)
        assert a.schema.names == b.schema.names and a.num_rows == b.num_rows
        changed = []
        for n in a.schema.names:
            x, y = a.column(n), b.column(n)
            if pa.types.is_floating(x.type):
                same = np.allclose(np.array(x.to_pylist(), float), np.array(y.to_pylist(), float),
                                   rtol=1e-9, atol=1e-12, equal_nan=True)
            else:
                same = x.equals(y)
            if not same:
                changed.append(n)
        assert changed == [], (t, changed)
        assert train_batches(cfg, plain, t) == train_batches(cfg_p, with_p, t)


def test_pseudo_available_before_snapshot_feeds_the_next_batch(work, world):
    cfg_p, urls = _with_pseudo(work, world)
    plain = _store(small_cfg(work), work, next_batch=True)
    with_p = _store(cfg_p, work, next_batch=True)
    assert plain.batches[-1].batch_id == with_p.batches[-1].batch_id == 10
    assert PSEUDO_ID in train_batches(cfg_p, with_p, 10)
    a, b = _sorted_frame(plain, 10), _sorted_frame(with_p, 10)
    hist_a = dict(zip(a.column("url").to_pylist(), a.column("hist_decayed").to_pylist(),
                      strict=True))
    hist_b = dict(zip(b.column("url").to_pylist(), b.column("hist_decayed").to_pylist(),
                      strict=True))
    assert any((hist_b[u] or 0) > (hist_a[u] or 0) for u in urls if u in hist_a)


def test_frozen_model_validates_on_the_last_official_batch(work, world):
    cfg_p, _ = _with_pseudo(work, world)
    model_dir = train_frozen(cfg_p, work, [])
    meta = read_json(model_dir / "lgbm_full" / "meta.json")
    assert PSEUDO_ID in meta["train_batches"]
    assert meta["valid_batch"] == 8
    assert meta["use_pseudo"] is True


def test_forward_eval_pairs_the_official_and_pseudo_models(work, world):
    cfg = small_cfg(work)
    cfg_p, _ = _with_pseudo(work, world)
    m0, m1 = train_frozen(cfg, work, []), train_frozen(cfg_p, work, [])
    out = forward_eval(cfg, work, [m0, m1], 9, ["random"])
    result = read_json(out / "results.json")["batches"]["9"]
    scorers = result["tags"]["head"]["scorers"]
    assert {"lgbm:full", "lgbm:full+pseudo", "random"} <= set(scorers)
    assert "diff_vs_reference_ci95" in scorers["lgbm:full+pseudo"]
    overlap = result["pseudo_overlap"]["head"]
    assert 0 < overlap["in_pseudo"] <= overlap["golden_urls"]
    split = overlap["hits"]["lgbm:full+pseudo"]
    k = str(cfg.eval.k_headline)
    rate = scorers["lgbm:full+pseudo"]["k"][k]["indexed_exact_rate"]
    assert split["in_pseudo"] + split["not_in_pseudo"] == round(
        rate * result["tags"]["head"]["golden_urls"])


def test_store_refuses_an_unfinished_sample(work):
    meta = read_json(work / "sample" / "_meta.json")
    del meta["finished_at"]
    atomic_write_json(work / "sample" / "_meta.json", meta)
    with pytest.raises(RuntimeError, match="unfinished"):
        _store(small_cfg(work), work)


def test_store_refuses_batches_without_positives(work):
    (work / "positives" / "batch=3.parquet").unlink()
    with pytest.raises(RuntimeError, match=r"positives are missing for batches \[3\]"):
        _store(small_cfg(work), work)


def test_store_refuses_a_pseudo_batch_that_reuses_an_official_id(work, world):
    write_pseudo(world, work, 5, world.batches[5], PSEUDO_FETCHED)
    cfg = replace(small_cfg(work), "golden", use_pseudo=True)
    with pytest.raises(RuntimeError, match=r"batch ids \[5\] hold both"):
        _store(cfg, work)


@pytest.mark.parametrize("available_at, known", [
    (SNAPSHOT + timedelta(days=1), False),    # fetched after S0: no model could use it
    (SNAPSHOT - timedelta(days=1), True),
])
def test_pseudo_overlap_counts_only_pseudo_known_by_s0(work, world, available_at, known):
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, [])
    urls = sorted({r[7] for r in world.golden_rows if r[0] == 9})[:20]
    rows = [(10007, world.batches[8], i, f"p{i}", 1, ["random"], 1, u)
            for i, u in enumerate(urls)]
    write_parquet_atomic(golden_table(rows, label_source=PSEUDO, available_at=available_at),
                         pseudo_batch_path(work, 10007))
    out = forward_eval(cfg, work, [model_dir], 9, ["random"])
    overlap = read_json(out / "results.json")["batches"]["9"]["pseudo_overlap"]
    in_pseudo = sum(t["in_pseudo"] for t in overlap.values())
    assert (in_pseudo >= len(urls)) if known else in_pseudo == 0


def test_decay_weights_fall_with_age(work):
    store = _store(small_cfg(work), work)
    rows = store.con.execute("SELECT src, w FROM pairs WHERE dst = 8 ORDER BY src").fetchall()
    weights = [w for _, w in rows]
    assert [s for s, _ in rows] == list(range(1, 8))
    assert weights == sorted(weights) and all(0 < w <= 1 for w in weights)


def test_empirical_bayes_falls_back_to_parent(work):
    store = _store(small_cfg(work), work)
    rows = store.con.execute(
        f"SELECT f.lift_hp, f.lift_host, l.e, l.g FROM ({store.frame_sql(2, 'eval')}) f "
        "JOIN crawl c USING (url, shard) "
        "LEFT JOIN (SELECT * FROM lvl_hp WHERE batch_id = 2) l ON l.k = c.hp").fetchall()
    empty = [(hp, host) for hp, host, e, g in rows if not e and not g]
    seen_no_hits = [(hp, host) for hp, host, e, g in rows if e and not g]
    assert empty and seen_no_hits
    assert all(np.isclose(hp, host) for hp, host in empty)
    assert all(hp < host for hp, host in seen_no_hits)


def test_backtest_beats_random_and_links_on_planted_signal(work):
    cfg = small_cfg(work)
    out = run_backtest(cfg, work, ["full", "no_domain"],
                       ["random", "inlinks", "ext", "domain_prior", "logreg", "v1_features"])
    results = read_json(out / "results.json")
    pooled = results["pooled"]
    for tag in ("head", "random"):
        s = pooled[tag]["scorers"]
        model = s["lgbm:full"]["indexed_exact_rate"]
        assert model > s["random"]["indexed_exact_rate"] + 0.05
        assert model >= s["inlinks"]["indexed_exact_rate"]
        assert s["random"]["diff_vs_reference_ci95"][1] < 0
    b8 = results["batches"]["8"]
    ratio = b8["calibration"]["lgbm:full"]["ratio"]
    assert 0.3 < ratio < 3
    assert b8["leaky_in_backtest"] == []
    assert (out / "report.md").read_text().startswith("# Forward-chained backtest")


def test_frozen_model_forward_eval_and_manifest(work, world):
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, ["v1_features"])
    assert world.batches[9].isoformat() > read_json(work / "sample" / "_meta.json")["snapshot_at"]
    out = forward_eval(cfg, work, [model_dir], 9, ["random", "inlinks", "url_score",
                                                   "v2_baseline_v1"])
    result = read_json(out / "results.json")["batches"]["9"]
    assert result["forward"] is True
    assert "v2_baseline_v1" in result["tags"]["head"]["scorers"]
    meta = select_sample(replace(cfg, "eval", k_headline=1000), work, model_dir, 1000)
    run = work / "selection" / f"run={meta['run_id']}"
    assert validate_run(run)["rows"] == meta["rows"] > 0


def test_backtest_batch_before_snapshot_is_rejected_for_forward_eval(work):
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, [])
    with pytest.raises(RuntimeError, match="existed before the snapshot"):
        forward_eval(cfg, work, [model_dir], 8, ["random"])
