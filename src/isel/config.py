"""Strict TOML configuration.

Every tunable lives in the config file. Loading fails on unknown keys, missing
keys, wrong types and inconsistent values, so a typo cannot silently fall back
to a default. Several files can be layered; later files override earlier ones.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tomllib
import types
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a config file is invalid."""


@dataclass(frozen=True)
class RunCfg:
    work_dir: str
    app_name: str


@dataclass(frozen=True)
class CrawlerDbCfg:
    dsn_env: str
    connect_timeout_s: int
    num_shards: int
    url_table_fmt: str
    split_table: str
    session: dict[str, str]


@dataclass(frozen=True)
class MetricDbCfg:
    dsn_env: str
    connect_timeout_s: int
    session: dict[str, str]


@dataclass(frozen=True)
class RoutingCfg:
    tldextract_offline: bool
    overrides: dict[str, int]


@dataclass(frozen=True)
class PullCfg:
    sample_percent: float
    sample_seed: int
    lookup_chunk: int
    min_interval_s: float
    shards: list[int]


@dataclass(frozen=True)
class GoldenCfg:
    lead_h: float
    tags: list[str]
    exclude_batches: list[int]


@dataclass(frozen=True)
class FeaturesCfg:
    half_life_days: float
    eb_pseudo_hits: float
    url_len_cap: int
    path_markers: list[str]
    ext_classes: dict[str, int]
    date_regex: str
    sets: dict[str, list[str]]


@dataclass(frozen=True)
class DatasetCfg:
    neg_rate: float
    neg_salt: str
    first_label_batch: int


@dataclass(frozen=True)
class ModelCfg:
    feature_set: str
    num_boost_round: int
    early_stopping_rounds: int
    refit_on_all: bool
    params: dict[str, int | float | str | bool]
    logreg_c: float
    logreg_max_iter: int


@dataclass(frozen=True)
class EvalCfg:
    test_batches: list[int]
    k_values: list[int]
    k_headline: int
    fractions: list[float]
    gain_curve_k: list[int]
    bootstrap_samples: int
    bootstrap_seed: int
    baselines: list[str]
    v2_formula: dict[str, float]


@dataclass(frozen=True)
class ManifestCfg:
    schema_version: int
    index_shards: int
    reason_top_n: int


@dataclass(frozen=True)
class DuckDbCfg:
    threads: int
    memory_limit: str
    max_temp_directory_size: str


@dataclass(frozen=True)
class Config:
    run: RunCfg
    crawlerdb: CrawlerDbCfg
    metricdb: MetricDbCfg
    routing: RoutingCfg
    pull: PullCfg
    golden: GoldenCfg
    features: FeaturesCfg
    dataset: DatasetCfg
    model: ModelCfg
    eval: EvalCfg
    manifest: ManifestCfg
    duckdb: DuckDbCfg

    @property
    def work_dir(self) -> Path:
        return Path(self.run.work_dir)

    @property
    def sample_rate(self) -> float:
        return self.pull.sample_percent / 100.0

    def sha256(self) -> str:
        """Returns a stable digest of the effective config, for run auditing."""
        payload = json.dumps(dataclasses.asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


_SCALARS = (int, float, str, bool)
KNOWN_BASELINES = (
    "random", "inlinks", "ext", "domain_prior", "logreg", "v1_features",
    "url_score", "v2_baseline_v1",
)


def _deep_merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def _check_scalar(tp: type, value: Any, where: str) -> Any:
    if tp is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if tp is int and isinstance(value, bool):
        raise ConfigError(f"{where}: expected int, got bool")
    if not isinstance(value, tp):
        raise ConfigError(f"{where}: expected {tp.__name__}, got {type(value).__name__}")
    return value


def _convert(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    if dataclasses.is_dataclass(tp):
        return _build(tp, value, where)
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        if not isinstance(value, Sequence) or isinstance(value, str):
            raise ConfigError(f"{where}: expected a list")
        return [_convert(item_tp, v, f"{where}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        key_tp, val_tp = typing.get_args(tp)
        if not isinstance(value, Mapping):
            raise ConfigError(f"{where}: expected a table")
        return {
            _check_scalar(key_tp, k, f"{where} key"): _convert(val_tp, v, f"{where}.{k}")
            for k, v in value.items()
        }
    if origin in (typing.Union, types.UnionType):
        for option in typing.get_args(tp):
            if option is int and isinstance(value, bool):
                continue
            if option is int and isinstance(value, float):
                continue
            try:
                return _check_scalar(option, value, where)
            except ConfigError:
                continue
        raise ConfigError(f"{where}: expected one of {typing.get_args(tp)}")
    # Session GUC values may be written as TOML numbers for readability.
    if tp is str and isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if tp in _SCALARS:
        return _check_scalar(tp, value, where)
    raise ConfigError(f"{where}: unsupported type {tp}")


def _build(cls: type, data: Any, where: str) -> Any:
    if not isinstance(data, Mapping):
        raise ConfigError(f"{where}: expected a table")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - names)
    missing = sorted(names - set(data))
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")
    if missing:
        raise ConfigError(f"{where}: missing keys {missing}")
    kwargs = {n: _convert(hints[n], data[n], f"{where}.{n}" if where else n) for n in names}
    return cls(**kwargs)


def _validate(cfg: Config) -> None:
    def need(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    need(cfg.crawlerdb.num_shards > 0, "crawlerdb.num_shards must be > 0")
    need(cfg.crawlerdb.session.get("default_transaction_read_only") == "on",
         "crawlerdb.session.default_transaction_read_only must be 'on'")
    need(cfg.metricdb.session.get("default_transaction_read_only") == "on",
         "metricdb.session.default_transaction_read_only must be 'on'")
    need(all(0 <= s < cfg.crawlerdb.num_shards for s in cfg.routing.overrides.values()),
         "routing.overrides must map to valid shards")
    need(0 < cfg.pull.sample_percent <= 100, "pull.sample_percent must be in (0, 100]")
    need(cfg.pull.lookup_chunk > 0, "pull.lookup_chunk must be > 0")
    need(cfg.pull.min_interval_s >= 0, "pull.min_interval_s must be >= 0")
    need(all(0 <= s < cfg.crawlerdb.num_shards for s in cfg.pull.shards),
         "pull.shards must be valid shard ids")
    need(cfg.golden.lead_h >= 0, "golden.lead_h must be >= 0")
    need(bool(cfg.golden.tags), "golden.tags must not be empty")
    need(cfg.features.half_life_days > 0, "features.half_life_days must be > 0")
    need(cfg.features.eb_pseudo_hits > 0, "features.eb_pseudo_hits must be > 0")
    need(cfg.features.url_len_cap > 0, "features.url_len_cap must be > 0")
    need(bool(cfg.features.sets), "features.sets must not be empty")
    need(all(v > 0 for v in cfg.features.ext_classes.values()),
         "features.ext_classes values must be > 0 (0 means no extension)")
    need(0 < cfg.dataset.neg_rate <= 1, "dataset.neg_rate must be in (0, 1]")
    need(cfg.model.feature_set in cfg.features.sets, "model.feature_set must name a features set")
    need(cfg.model.num_boost_round > 0, "model.num_boost_round must be > 0")
    need(cfg.model.early_stopping_rounds > 0, "model.early_stopping_rounds must be > 0")
    need(cfg.model.logreg_c > 0, "model.logreg_c must be > 0")
    need(cfg.model.params.get("objective") == "binary", "model.params.objective must be 'binary'")
    need(bool(cfg.eval.test_batches), "eval.test_batches must not be empty")
    need(cfg.eval.k_headline in cfg.eval.k_values, "eval.k_headline must be in eval.k_values")
    need(all(k > 0 for k in cfg.eval.k_values + cfg.eval.gain_curve_k), "K values must be > 0")
    need(all(0 < f <= 1 for f in cfg.eval.fractions), "eval.fractions must be in (0, 1]")
    need(cfg.eval.bootstrap_samples > 0, "eval.bootstrap_samples must be > 0")
    unknown = sorted(set(cfg.eval.baselines) - set(KNOWN_BASELINES))
    need(not unknown, f"eval.baselines has unknown names {unknown}")
    v2_keys = {"inlinks_cap", "ext_cap", "prior_cap", "w_inlinks", "w_ext", "w_url", "w_prior"}
    need(set(cfg.eval.v2_formula) == v2_keys, f"eval.v2_formula must have keys {sorted(v2_keys)}")
    need(cfg.manifest.index_shards > 0, "manifest.index_shards must be > 0")
    need(cfg.manifest.reason_top_n >= 0, "manifest.reason_top_n must be >= 0")
    need(cfg.duckdb.threads > 0, "duckdb.threads must be > 0")


def load_config(paths: Sequence[str | Path]) -> Config:
    """Loads and validates one or more TOML files; later files override earlier ones.

    Args:
        paths: Config files in override order.

    Returns:
        The validated, frozen config.

    Raises:
        ConfigError: If any file is missing keys, has unknown keys or bad values.
    """
    if not paths:
        raise ConfigError("at least one --config file is required")
    merged: dict[str, Any] = {}
    for path in paths:
        with open(path, "rb") as fh:
            merged = _deep_merge(merged, tomllib.load(fh))
    cfg = _build(Config, merged, "")
    _validate(cfg)
    return cfg
