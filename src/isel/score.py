"""Full-pool scoring with a frozen model, and the exact top-K list.

The domain aggregates come from the sample, as in training. Each exported
file replaces the sample rows in the FeatureStore and goes through the same
frame SQL, so a row gets the same score here as in `select-sample`. One file
at a time bounds the memory: the largest shard does not fit at once.

Only rows scoring at least tau are kept. tau is the sample's score at rank
round(K·p·(1 + tau_margin)). When at least K distinct URLs score >= tau, the
K-th best score is >= tau, so every row of the exact top-K was kept.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from isel.backtest import load_models, model_name, open_store
from isel.config import Config
from isel.features import FeatureStore
from isel.io import (
    atomic_write_json,
    code_version,
    copy_atomic,
    open_duckdb,
    read_json,
    sql_str,
    utc_id,
    write_parquet_atomic,
)
from isel.model import TrainedModel

SCORED_SCHEMA = pa.schema([("url", pa.string()), ("shard", pa.int16()), ("h", pa.uint64()),
                           ("score", pa.float64())])

# Model facts copied into the run, and from there into selectdb's model_runs.
MODEL_META = ("best_iteration", "train_batches", "valid_batch", "rows", "positives",
              "trained_at", "code_version")

log = logging.getLogger(__name__)


class NotEnoughCandidates(RuntimeError):
    """Raised when fewer than K distinct URLs scored at or above tau."""


def _frozen_model(cfg: Config, store: FeatureStore, model_dir: Path) -> TrainedModel:
    model = load_models(model_dir, cfg.model.num_threads)[model_name(cfg.model.feature_set)]
    if str(model.meta.get("snapshot_at")) != str(store.snapshot_at):
        raise RuntimeError(f"the model was trained on snapshot {model.meta.get('snapshot_at')}, "
                           f"but the sample is {store.snapshot_at}")
    return model


def _scored(model: TrainedModel, frame: pa.Table) -> tuple[pa.Table, np.ndarray]:
    """Rows with a domain key and their raw scores; NaN scores sort last."""
    frame = frame.filter(pc.is_valid(frame.column("dkey")))
    return frame, np.nan_to_num(model.raw_score(frame), nan=-np.inf)


def threshold(cfg: Config, store: FeatureStore, model: TrainedModel, batch_id: int,
              k: int) -> dict[str, Any]:
    """The score floor tau from the sample, under the (score desc, URL hash asc) order."""
    frame = store.frame(batch_id, "eval")
    frame, scores = _scored(model, frame.filter(frame.column("is_sample")))
    h = frame.column("h").to_numpy(zero_copy_only=False).astype(np.uint64)
    ordered = scores[np.lexsort((h, -scores))]
    rank = max(1, round(k * store.rate * (1 + cfg.full.tau_margin)))
    boundary = max(1, round(k * store.rate))
    return {"tau": float(ordered[rank - 1]) if rank <= len(ordered) else float("-inf"),
            "tau_rank": rank, "sample_rows": len(ordered),
            "sample_boundary_score": (float(ordered[boundary - 1])
                                      if boundary <= len(ordered) else None)}


def score_full(cfg: Config, work_dir: Path, model_dir: Path, k: int) -> dict[str, Any]:
    """Scores every exported shard and writes `full/scored/shard=NNN.parquet`, resumably.

    Raises:
        RuntimeError: If the export is unfinished, the model belongs to another
            snapshot, or `full/scored` holds scores of another model, K or export.
    """
    export = work_dir / "full" / "export"
    exported = read_json(export / "_meta.json") or {}
    if "finished_at" not in exported:
        raise RuntimeError("the full export is unfinished; rerun pull-full until none is missing")
    store = open_store(cfg, work_dir, next_batch=True)
    model = _frozen_model(cfg, store, model_dir)
    target = store.batches[-1].batch_id
    t = threshold(cfg, store, model, target, k)
    out = work_dir / "full" / "scored"
    meta_path = out / "_meta.json"
    meta = read_json(meta_path) or {}
    header = {"model_dir": str(model_dir), "k": k, "tau": t["tau"],
              "snapshot_at": store.snapshot_at.isoformat(), "target_batch": target,
              "export_started_at": exported["started_at"]}
    if meta and any(meta.get(key) != value for key, value in header.items()):
        raise RuntimeError(f"{out} holds scores of another model, K or export; move it away")
    meta.update(header, threshold=t, config_sha256=cfg.sha256(), code_version=code_version(),
                model_meta={key: model.meta.get(key) for key in MODEL_META})
    shards: dict[str, dict[str, int]] = meta.setdefault("shards", {})
    todo = [s for s in sorted(exported["shards_done"]) if str(s) not in shards]
    for i, shard in enumerate(todo, start=1):
        kept, scored = [SCORED_SCHEMA.empty_table()], 0
        for path in sorted((export / f"shard={shard:03d}").glob("part-*.parquet")):
            store.load_chunk(path)
            frame, scores = _scored(model, store.frame(target, "score"))
            kept.append(pa.table({"url": frame.column("url"), "shard": frame.column("shard"),
                                  "h": frame.column("h"), "score": pa.array(scores)},
                                 schema=SCORED_SCHEMA).filter(pa.array(scores >= t["tau"])))
            scored += frame.num_rows
        table = pa.concat_tables(kept)
        write_parquet_atomic(table, out / f"shard={shard:03d}.parquet")
        shards[str(shard)] = {"scored": scored, "kept": table.num_rows}
        atomic_write_json(meta_path, meta)
        log.info("shard %03d: kept %d of %d rows (%d/%d)", shard, table.num_rows, scored, i,
                 len(todo))
    meta.setdefault("finished_at", datetime.now(UTC).isoformat())
    atomic_write_json(meta_path, meta)
    return {"shards": len(shards), "scored": sum(v["scored"] for v in shards.values()),
            "kept": sum(v["kept"] for v in shards.values()), "tau": t["tau"],
            "tau_rank": t["tau_rank"]}


def select_full(cfg: Config, work_dir: Path, k: int) -> dict[str, Any]:
    """Writes the exact top-K distinct URLs to `full/selection/run=<id>/selected.parquet`.

    Order: score desc, URL hash asc, URL asc, the same order as the backtest
    boundary. A URL found in two shards keeps its best score. `_meta.json` is
    written last.

    Raises:
        NotEnoughCandidates: If fewer than K distinct URLs were kept.
    """
    scored = work_dir / "full" / "scored"
    meta = read_json(scored / "_meta.json") or {}
    if "finished_at" not in meta:
        raise RuntimeError("scoring is unfinished; rerun score-full")
    if k > meta["k"]:
        raise ValueError(f"tau was set for K = {meta['k']}; rerun score-full for K = {k}")
    con = open_duckdb(cfg, work_dir / "duckdb_tmp")
    best = (f"SELECT url, max(score) AS score, min(h) AS h "
            f"FROM read_parquet({sql_str(str(scored / 'shard=*.parquet'))}) GROUP BY url")
    (candidates,) = con.execute(f"SELECT count(*) FROM ({best})").fetchone()
    if candidates < k:
        raise NotEnoughCandidates(f"only {candidates} URLs scored >= tau, need {k}; "
                                  "raise full.tau_margin and rerun score-full")
    run_id = utc_id()
    out = work_dir / "full" / "selection" / f"run={run_id}"
    selected = out / "selected.parquet"
    rows = copy_atomic(con, f"SELECT url, score FROM ({best}) "
                            f"ORDER BY score DESC, h ASC, url ASC LIMIT {int(k)}", selected)
    (boundary,) = con.execute(
        f"SELECT min(score) FROM read_parquet({sql_str(str(selected))})").fetchone()
    result = {"run_id": run_id, "k": k, "rows": rows, "candidates": candidates,
              "boundary_score": boundary, "tau": meta["tau"], "model_dir": meta["model_dir"],
              "snapshot_at": meta["snapshot_at"], "target_batch": meta["target_batch"],
              "model_meta": meta["model_meta"],
              "config_sha256": cfg.sha256(), "code_version": code_version(),
              "selected_at": datetime.now(UTC).isoformat()}
    atomic_write_json(out / "_meta.json", result)
    return result
