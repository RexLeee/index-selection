from __future__ import annotations

import lightgbm as lgb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from conftest import replace, small_cfg
from synth import SNAPSHOT, make_world, write_work_dir

from isel.backtest import open_store, select_sample, train_frozen
from isel.features import FeatureStore
from isel.io import atomic_write_json, read_json, write_parquet_atomic
from isel.pull import CRAWL_SCHEMA, FULL_SCHEMA
from isel.score import SCORED_SCHEMA, NotEnoughCandidates, score_full, select_full


@pytest.fixture(scope="module")
def world():
    return make_world()


@pytest.fixture
def work(tmp_path, world):
    write_work_dir(world, tmp_path)
    return tmp_path


def _export(work, world, new_hosts=0):
    """Writes every crawled page as if `pull-full` had exported it, two files per shard.

    `new_hosts` adds pages on hosts that neither the sample nor golden has seen.
    """
    rows = [{k: v for k, v in p.items() if not k.startswith("_")} for p in world.pages]
    rows += [{**rows[i], "url": f"https://fresh{i}.example.org/p", "shard": i % 4}
             for i in range(new_hosts)]
    pages = pa.Table.from_pylist(rows, schema=CRAWL_SCHEMA)
    pages = pages.select(FULL_SCHEMA.names).cast(FULL_SCHEMA)
    shards = sorted(set(pages.column("shard").to_pylist()))
    for shard in shards:
        table = pages.filter(pc.equal(pages.column("shard"), shard))
        half = table.num_rows // 2
        for lo, part in ((0, table.slice(0, half)), (1, table.slice(half))):
            write_parquet_atomic(part, work / "full" / "export" / f"shard={shard:03d}"
                                 / f"part-{lo:010d}-{lo + 1:010d}.parquet")
    atomic_write_json(work / "full" / "export" / "_meta.json", {
        "started_at": SNAPSHOT.isoformat(), "finished_at": SNAPSHOT.isoformat(),
        "shards_done": shards})


NEW_HOSTS = 30


def _half_sample_model(work, world, **full):
    # A half sample: the export holds pages and hosts the sample never saw.
    write_work_dir(world, work, sample_percent=50.0)
    cfg = replace(small_cfg(work), "pull", sample_percent=50.0)
    cfg = replace(cfg, "full", **full) if full else cfg
    model_dir = train_frozen(cfg, work, [])
    _export(work, world, NEW_HOSTS)
    return cfg, model_dir


def test_full_scores_equal_select_sample_and_cover_pages_outside_the_sample(tmp_path, world):
    cfg, model_dir = _half_sample_model(tmp_path, world)
    report = score_full(cfg, tmp_path, model_dir, 1000)
    assert report["scored"] == len(world.pages) + NEW_HOSTS
    meta = select_sample(cfg, tmp_path, model_dir, 1000)
    manifest = pq.read_table(tmp_path / "selection" / f"run={meta['run_id']}" / "manifest")
    scored = pq.read_table(tmp_path / "full" / "scored")
    full = dict(zip(scored.column("url").to_pylist(), scored.column("score").to_pylist(),
                    strict=True))
    sample = dict(zip(manifest.column("url").to_pylist(), manifest.column("score").to_pylist(),
                      strict=True))
    assert len(sample) == 500
    assert {u: full.get(u) for u in sample} == sample
    assert read_json(tmp_path / "full" / "scored" / "_meta.json")["model_meta"]["train_batches"]


def test_the_score_floor_keeps_every_row_of_the_exact_top_k(tmp_path, world):
    k = 1000
    everything, cut = tmp_path / "all", tmp_path / "cut"
    cfg_all, model_all = _half_sample_model(everything, world, tau_margin=100.0)
    cfg_cut, model_cut = _half_sample_model(cut, world)
    assert score_full(cfg_all, everything, model_all, k)["kept"] == len(world.pages) + NEW_HOSTS
    assert score_full(cfg_cut, cut, model_cut, k)["kept"] < len(world.pages)
    rows = pq.read_table(everything / "full" / "scored").to_pylist()
    expected = [r["url"] for r in sorted(rows, key=lambda r: (-r["score"], r["h"], r["url"]))][:k]
    run = select_full(cfg_cut, cut, k)
    selected = pq.read_table(cut / "full" / "selection" / f"run={run['run_id']}"
                             / "selected.parquet")
    assert selected.column("url").to_pylist() == expected


def test_score_full_resumes_and_refuses_another_k(work, world):
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, [])
    _export(work, world)
    first = score_full(cfg, work, model_dir, 1000)
    assert score_full(cfg, work, model_dir, 1000) == first
    with pytest.raises(RuntimeError, match="another model, K or export"):
        score_full(cfg, work, model_dir, 500)


def test_score_full_predicts_with_the_configured_threads(work, world, monkeypatch):
    # A loaded Booster ignores its saved params at predict time; without an explicit
    # num_threads, OpenMP starts one thread per host CPU and the container quota throttles it.
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, [])
    _export(work, world)
    seen, predict = [], lgb.Booster.predict

    def recording_predict(self, data, *args, **kwargs):
        seen.append(kwargs.get("num_threads"))
        return predict(self, data, *args, **kwargs)

    monkeypatch.setattr(lgb.Booster, "predict", recording_predict)
    score_full(cfg, work, model_dir, 1000)
    assert seen and set(seen) == {cfg.model.params["num_threads"]}


def test_score_full_loads_one_export_file_at_a_time(work, world, monkeypatch):
    # The largest shard holds about 35M rows and does not fit in the 64 GiB container;
    # one file (8 GiB of heap) does.
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, [])
    _export(work, world)
    # A block range with no fetched rows exports an empty file.
    write_parquet_atomic(FULL_SCHEMA.empty_table(), work / "full" / "export" / "shard=000"
                         / "part-0000000002-0000000003.parquet")
    loaded, load_chunk = [], FeatureStore.load_chunk

    def recording_load(self, path):
        loaded.append(path)
        return load_chunk(self, path)

    monkeypatch.setattr(FeatureStore, "load_chunk", recording_load)
    report = score_full(cfg, work, model_dir, 1000)
    assert sorted(loaded) == sorted((work / "full" / "export").glob("shard=*/part-*.parquet"))
    assert report["scored"] == len(world.pages)


def _crawl_row_tables(store):
    return [t for (t,) in store.con.execute(
        "SELECT table_name FROM duckdb_columns() WHERE database_name = 'temp' "
        "AND column_name = 'last_fetch_ok' ORDER BY 1").fetchall()]


def test_the_store_keeps_one_copy_of_the_crawl_rows(work, world):
    # A second copy of an export file costs as much memory as the first.
    cfg = small_cfg(work)
    _export(work, world)
    store = open_store(cfg, work, next_batch=True)
    assert _crawl_row_tables(store) == ["crawl"]
    store.load_chunk(next((work / "full" / "export").glob("shard=*/part-*.parquet")))
    assert _crawl_row_tables(store) == ["crawl"]


def test_score_full_needs_a_finished_export(work):
    cfg = small_cfg(work)
    model_dir = train_frozen(cfg, work, [])
    atomic_write_json(work / "full" / "export" / "_meta.json", {"started_at": "x"})
    with pytest.raises(RuntimeError, match="export is unfinished"):
        score_full(cfg, work, model_dir, 1000)


def _write_scored(work, k, shards):
    for shard, rows in shards.items():
        write_parquet_atomic(pa.Table.from_pylist(rows, schema=SCORED_SCHEMA),
                             work / "full" / "scored" / f"shard={shard:03d}.parquet")
    atomic_write_json(work / "full" / "scored" / "_meta.json", {
        "finished_at": "2026-10-14T00:00:00+00:00", "k": k, "tau": 0.0, "model_dir": "m",
        "snapshot_at": SNAPSHOT.isoformat(), "target_batch": 20, "model_meta": {}})


def test_select_full_is_the_exact_top_k_with_ties_and_duplicates(tmp_path):
    def row(url, shard, h, score):
        return {"url": url, "shard": shard, "h": h, "score": score}

    shards = {
        0: [row("a", 0, 5, 3.0), row("b", 0, 2, 2.0), row("c", 0, 9, 2.0), row("d", 0, 1, 1.0)],
        1: [row("a", 1, 5, 1.5), row("e", 1, 3, 2.0), row("f", 1, 4, 0.5)],
    }
    _write_scored(tmp_path, 5, shards)
    run = select_full(small_cfg(tmp_path), tmp_path, 4)
    selected = pq.read_table(tmp_path / "full" / "selection" / f"run={run['run_id']}"
                             / "selected.parquet")
    # a keeps its best score; b, e and c tie at 2.0 and order by hash.
    assert selected.column("url").to_pylist() == ["a", "b", "e", "c"]
    assert run["boundary_score"] == 2.0 and run["candidates"] == 6


def test_select_full_refuses_too_few_candidates_or_a_larger_k(tmp_path):
    _write_scored(tmp_path, 5, {0: [{"url": "a", "shard": 0, "h": 1, "score": 1.0}]})
    cfg = small_cfg(tmp_path)
    with pytest.raises(NotEnoughCandidates):
        select_full(cfg, tmp_path, 2)
    with pytest.raises(ValueError, match="rerun score-full"):
        select_full(replace(cfg, "eval", k_headline=6), tmp_path, 6)
