"""Selection manifest: the contract between index selection and the future indexer.

Layout of one run (`selection/run=<id>/`):

* `manifest/index_shard=NN.parquet`: one row per selected URL, `MANIFEST_SCHEMA`.
* `deltas/added.parquet`, `deltas/removed.parquet`: doc ids against the parent run.
* `_MANIFEST.json`: schema version, parent run, row counts and sha256 per file;
  written last, so a run without it is incomplete.

`doc_id` is the low 64 bits of MD5 of the canonical URL. It stays the same
for a page across runs, so the fetcher and indexer can diff runs by id.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from isel.config import Config
from isel.io import atomic_write_json, read_json, write_parquet_atomic
from isel.urlnorm import md5_number_lower

MANIFEST_SCHEMA = pa.schema([
    ("doc_id", pa.uint64()),
    ("url", pa.string()),
    ("canonical_url", pa.string()),
    ("domain_key", pa.string()),
    ("crawl_shard", pa.int16()),
    ("index_shard", pa.int16()),
    ("score", pa.float64()),
    ("rank", pa.int64()),
    ("reason", pa.list_(pa.string())),
    ("content_hash", pa.string()),
    ("simhash", pa.uint64()),
    ("lang", pa.string()),
    ("text_len", pa.int32()),
    ("run_id", pa.string()),
    ("schema_version", pa.int32()),
])
DELTA_SCHEMA = pa.schema([("doc_id", pa.uint64()), ("url", pa.string()),
                          ("canonical_url", pa.string())])


class ManifestError(ValueError):
    """Raised when a manifest is inconsistent or fails validation."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_run(selection_dir: Path) -> Path | None:
    """The newest complete run (one with `_MANIFEST.json`)."""
    runs = sorted(p for p in selection_dir.glob("run=*") if (p / "_MANIFEST.json").exists())
    return runs[-1] if runs else None


def build_table(cfg: Config, rows: pa.Table, run_id: str) -> pa.Table:
    """Adds ids, shards and rank to the selected rows, ordered by score then doc_id.

    `rows` holds url, canonical_url, domain_key, crawl_shard, score and reason.
    """
    canonical = rows.column("canonical_url").to_pylist()
    doc_ids = [md5_number_lower(c) for c in canonical]
    seen: dict[int, str] = {}
    for d, c in zip(doc_ids, canonical, strict=True):
        if seen.setdefault(d, c) != c:
            raise ManifestError(f"doc_id collision between {seen[d]!r} and {c!r}")
    if len(seen) != len(doc_ids):
        raise ManifestError("duplicate canonical URLs in the selection")
    n = rows.num_rows
    table = pa.table({
        "doc_id": pa.array(doc_ids, pa.uint64()),
        "url": rows.column("url"),
        "canonical_url": rows.column("canonical_url"),
        "domain_key": rows.column("domain_key"),
        "crawl_shard": pc.cast(rows.column("crawl_shard"), pa.int16()),
        "index_shard": pa.array([d % cfg.manifest.index_shards for d in doc_ids], pa.int16()),
        "score": pc.cast(rows.column("score"), pa.float64()),
        "rank": pa.array([0] * n, pa.int64()),
        "reason": rows.column("reason"),
        "content_hash": pa.nulls(n, pa.string()),
        "simhash": pa.nulls(n, pa.uint64()),
        "lang": pa.nulls(n, pa.string()),
        "text_len": pa.nulls(n, pa.int32()),
        "run_id": pa.array([run_id] * n, pa.string()),
        "schema_version": pa.array([cfg.manifest.schema_version] * n, pa.int32()),
    }, schema=MANIFEST_SCHEMA)
    order = pc.sort_indices(table, sort_keys=[("score", "descending"), ("doc_id", "ascending")])
    table = table.take(order)
    return table.set_column(table.schema.get_field_index("rank"), "rank",
                            pa.array(range(1, n + 1), pa.int64()))


def write_run(cfg: Config, rows: pa.Table, selection_dir: Path, run_id: str,
              extra_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Writes one run plus deltas against the newest complete run; returns its meta."""
    parent = latest_run(selection_dir)
    out = selection_dir / f"run={run_id}"
    if out.exists():
        raise ManifestError(f"{out} already exists")
    table = build_table(cfg, rows, run_id)
    files: dict[str, dict[str, Any]] = {}

    def put(rel: str, part: pa.Table) -> None:
        path = out / rel
        write_parquet_atomic(part, path)
        files[rel] = {"rows": part.num_rows, "sha256": _sha256(path)}

    shard_col = table.column("index_shard")
    for shard in range(cfg.manifest.index_shards):
        put(f"manifest/index_shard={shard:02d}.parquet",
            table.filter(pc.equal(shard_col, shard)))
    current = table.select(["doc_id", "url", "canonical_url"])
    if parent is not None:
        old = pq.read_table(parent / "manifest",
                            columns=["doc_id", "url", "canonical_url"]).cast(DELTA_SCHEMA)
        added = current.filter(pc.invert(pc.is_in(current.column("doc_id"),
                                                  value_set=old.column("doc_id"))))
        removed = old.filter(pc.invert(pc.is_in(old.column("doc_id"),
                                                value_set=current.column("doc_id"))))
    else:
        added, removed = current, DELTA_SCHEMA.empty_table()
    put("deltas/added.parquet", added.cast(DELTA_SCHEMA))
    put("deltas/removed.parquet", removed.cast(DELTA_SCHEMA))
    meta = {
        "schema_version": cfg.manifest.schema_version,
        "run_id": run_id,
        "parent_run_id": None if parent is None else parent.name.removeprefix("run="),
        "rows": table.num_rows,
        "index_shards": cfg.manifest.index_shards,
        "config_sha256": cfg.sha256(),
        "files": files,
        **(extra_meta or {}),
    }
    atomic_write_json(out / "_MANIFEST.json", meta)
    return meta


def validate_run(run_dir: Path) -> dict[str, Any]:
    """Checks every file's checksum and row count against `_MANIFEST.json`."""
    meta = read_json(run_dir / "_MANIFEST.json")
    if meta is None:
        raise ManifestError(f"{run_dir} has no _MANIFEST.json")
    total = 0
    for rel, info in meta["files"].items():
        path = run_dir / rel
        if _sha256(path) != info["sha256"]:
            raise ManifestError(f"checksum mismatch: {rel}")
        if pq.ParquetFile(path).metadata.num_rows != info["rows"]:
            raise ManifestError(f"row count mismatch: {rel}")
        if rel.startswith("manifest/"):
            total += info["rows"]
    if total != meta["rows"]:
        raise ManifestError("manifest partitions do not add up to the run's row count")
    return meta
