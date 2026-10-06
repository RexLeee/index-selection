"""Command line: `isel --config base.toml [--config overlay.toml] <command> [options]`.

Exit codes: 0 success, 2 config error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from isel.config import Config, ConfigError, load_config
from isel.features import FeatureStore
from isel.io import atomic_write_json, open_duckdb, read_json, sql_str


def _ids(text: str | None) -> list[int] | None:
    """Parses `0-3,7,10-12` into a sorted id list."""
    if not text:
        return None
    out: set[int] = set()
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return sorted(out)


def _names(text: str | None, default: list[str]) -> list[str]:
    return [n for n in text.split(",") if n] if text else list(default)


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _router(cfg: Config):
    from isel.urlnorm import DomainKeyer, ShardRouter

    split = cfg.work_dir / "dims" / "split_hosts.parquet"
    if not split.exists():
        raise SystemExit("dims/split_hosts.parquet is missing; run pull-dims first")
    hosts = pq.read_table(split, columns=["host"]).column("host").to_pylist()
    keyer = DomainKeyer(hosts, offline=cfg.routing.tldextract_offline)
    return ShardRouter(keyer, cfg.crawlerdb.num_shards, cfg.routing.overrides)


def cmd_pull_golden(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.golden import pull_golden
    from isel.pg import open_metricdb

    session = open_metricdb(cfg)
    try:
        return pull_golden(session, cfg.work_dir / "golden", cfg.golden.tags)
    finally:
        session.close()


def cmd_pull_dims(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.pg import open_crawlerdb
    from isel.pull import pull_dims

    session = open_crawlerdb(cfg)
    try:
        return pull_dims(cfg, session, cfg.work_dir / "dims")
    finally:
        session.close()


def cmd_pull_sample(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.pg import open_crawlerdb
    from isel.pull import estimate_sample, pull_sample

    session = open_crawlerdb(cfg)
    try:
        if args.dry_run:
            return estimate_sample(cfg, session, _ids(args.shards))
        return pull_sample(cfg, session, cfg.work_dir / "sample", _ids(args.shards))
    finally:
        session.close()


def cmd_pull_positives(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.golden import read_golden
    from isel.pg import open_crawlerdb
    from isel.pull import pull_positives

    router = _router(cfg)
    session = open_crawlerdb(cfg)
    try:
        return pull_positives(cfg, session, router, read_golden(cfg.work_dir),
                              cfg.work_dir / "positives", _ids(args.batches), args.force)
    finally:
        session.close()


def cmd_validate_routing(cfg: Config, args: argparse.Namespace) -> Any:
    """Row-weighted share of sample rows whose shard the router predicts."""
    from isel.features import HOST_RE

    router = _router(cfg)
    con = open_duckdb(cfg, cfg.work_dir / "duckdb_tmp")
    glob = sql_str(str(cfg.work_dir / "sample" / "shard=*.parquet"))
    pairs = con.execute(
        f"SELECT lower(regexp_extract(url, {sql_str(HOST_RE)}, 1)) AS host, shard, count(*) "
        f"FROM read_parquet({glob}) GROUP BY ALL").fetchall()
    total = primary = candidate = 0
    misses: Counter[str] = Counter()
    for host, shard, n in pairs:
        url = f"https://{host}/"
        total += n
        if router.shard_for_key(router.keyer.key_for_host(host)) == shard:
            primary += n
        if shard in router.candidate_shards(url):
            candidate += n
        else:
            misses[host] += n
    return {"rows": total, "primary_rate": primary / total if total else None,
            "candidate_rate": candidate / total if total else None,
            "top_misses": misses.most_common(20)}


def cmd_build_dataset(cfg: Config, args: argparse.Namespace) -> Any:
    """Writes per-batch pool sizes, label counts and history flags for inspection."""
    store = FeatureStore(cfg, open_duckdb(cfg, cfg.work_dir / "duckdb_tmp"), cfg.work_dir)
    summary = {}
    for b in store.batches:
        (positives, head, rand) = store.con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE l.in_head), "
            f"count(*) FILTER (WHERE l.in_random) FROM crawl c JOIN label_urls l "
            f"ON l.url = c.url AND l.batch_id = {b.batch_id} "
            f"WHERE c.last_fetch_ok < '{b.as_of.isoformat()}'::TIMESTAMPTZ").fetchone()
        summary[str(b.batch_id)] = {
            "created_at": b.created_at, "as_of": b.as_of,
            "pool_rows_est": store.pool_size(b.batch_id) / store.rate,
            "positive_rows": positives, "head_rows": head, "random_rows": rand,
            "has_history": store.has_history(b.batch_id),
        }
    atomic_write_json(cfg.work_dir / "datasets" / "summary.json", summary)
    return summary


def cmd_backtest(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.backtest import run_backtest

    out = run_backtest(cfg, cfg.work_dir, _names(args.feature_sets, [cfg.model.feature_set]),
                       _names(args.baselines, cfg.eval.baselines))
    return {"results": str(out / "results.json"), "report": str(out / "report.md")}


def cmd_report(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.evaluate import render_report

    results = read_json(Path(args.results))
    if results is None:
        raise SystemExit(f"{args.results} not found")
    sys.stdout.write(render_report(results))
    return None


def cmd_train(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.backtest import train_frozen

    return {"model_dir": str(train_frozen(cfg, cfg.work_dir,
                                          _names(args.baselines, cfg.eval.baselines)))}


def cmd_forward_eval(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.backtest import forward_eval

    out = forward_eval(cfg, cfg.work_dir, Path(args.model_dir), args.batch,
                       _names(args.baselines, cfg.eval.baselines))
    return {"results": str(out / "results.json"), "report": str(out / "report.md")}


def cmd_select_sample(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.backtest import select_sample

    meta = select_sample(cfg, cfg.work_dir, Path(args.model_dir), args.k or cfg.eval.k_headline)
    return {k: v for k, v in meta.items() if k != "files"}


def cmd_validate_manifest(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.manifest import validate_run

    meta = validate_run(Path(args.run_dir))
    return {"run_id": meta["run_id"], "rows": meta["rows"], "ok": True}


def cmd_check_config(cfg: Config, args: argparse.Namespace) -> Any:
    from isel.features import resolve_set

    return {"config_sha256": cfg.sha256(),
            "feature_sets": {n: len(resolve_set(cfg, n)) for n in cfg.features.sets}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="isel")
    parser.add_argument("--config", action="append", required=True,
                        help="TOML config; repeat to layer overlays")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config")
    sub.add_parser("pull-golden")
    sub.add_parser("pull-dims")
    p = sub.add_parser("pull-sample")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--shards")
    p = sub.add_parser("pull-positives")
    p.add_argument("--batches")
    p.add_argument("--force", action="store_true")
    sub.add_parser("validate-routing")
    sub.add_parser("build-dataset")
    p = sub.add_parser("backtest")
    p.add_argument("--feature-sets")
    p.add_argument("--baselines")
    p = sub.add_parser("report")
    p.add_argument("--results", required=True)
    p = sub.add_parser("train")
    p.add_argument("--baselines")
    p = sub.add_parser("forward-eval")
    p.add_argument("--model-dir", required=True)
    p.add_argument("--batch", type=int, required=True)
    p.add_argument("--baselines")
    p = sub.add_parser("select-sample")
    p.add_argument("--model-dir", required=True)
    p.add_argument("--k", type=int)
    p = sub.add_parser("validate-manifest")
    p.add_argument("--run-dir", required=True)
    return parser


COMMANDS = {
    "check-config": cmd_check_config,
    "pull-golden": cmd_pull_golden,
    "pull-dims": cmd_pull_dims,
    "pull-sample": cmd_pull_sample,
    "pull-positives": cmd_pull_positives,
    "validate-routing": cmd_validate_routing,
    "build-dataset": cmd_build_dataset,
    "backtest": cmd_backtest,
    "report": cmd_report,
    "train": cmd_train,
    "forward-eval": cmd_forward_eval,
    "select-sample": cmd_select_sample,
    "validate-manifest": cmd_validate_manifest,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    result = COMMANDS[args.command](cfg, args)
    if result is not None:
        _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
