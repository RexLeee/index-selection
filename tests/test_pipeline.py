from __future__ import annotations

import numpy as np
import pyarrow.compute as pc
import pytest
from conftest import replace, small_cfg
from synth import make_world, write_work_dir

from isel.backtest import forward_eval, run_backtest, select_sample, train_frozen
from isel.dataset import train_batches
from isel.features import FeatureStore
from isel.golden import golden_table
from isel.io import open_duckdb, read_json, write_parquet_atomic
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
    out = forward_eval(cfg, work, model_dir, 9, ["random", "inlinks", "url_score",
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
        forward_eval(cfg, work, model_dir, 8, ["random"])
