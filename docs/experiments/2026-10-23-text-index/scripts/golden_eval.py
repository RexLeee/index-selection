"""Recall@10 of golden batch 20 when its pages are in the index, under several settings.

Usage: golden_eval.py GOLDEN_TEXT BACKGROUND_TEXT WORK OUT.json [--variants a,b,...]

GOLDEN_TEXT holds the fetched golden pages (golden_fetch.py); BACKGROUND_TEXT holds the
36 crawl_result files, the pages that compete with them. Each variant is built with
isel.textindex.build and measured with isel.textindex.evaluate_golden, the product path,
under its own alias. `icu` is the production config; it also runs with no background
and with about 1/4 and 1/2 of it, to see how recall falls as the index grows.
"""
import argparse
import copy
import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

from isel.textindex import OpenSearch, build, evaluate_golden

from isel.config import load_config
from isel.io import utc_id

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
ICU = json.loads((V / "config/text_index/icu.json").read_text())
FIELDS = ["title^2", "description", "body"]
EN_FIELDS = [*FIELDS, "title.en^2", "description.en", "body.en"]
# name: (background share, analysis, English subfields, query type, fields)
VARIANTS = {
    "icu": ("bg44k", "icu", False, "combined_fields", FIELDS),
    "icu_bg0": ("bg0", "icu", False, "combined_fields", FIELDS),
    "icu_bg11k": ("bg11k", "icu", False, "combined_fields", FIELDS),
    "icu_bg22k": ("bg22k", "icu", False, "combined_fields", FIELDS),
    "standard": ("bg44k", "standard", False, "combined_fields", FIELDS),
    "icu_most": ("bg44k", "icu", False, "most_fields", FIELDS),
    "icu_en_most": ("bg44k", "icu", True, "most_fields", EN_FIELDS),
}
STEP = {"bg0": 0, "bg11k": 4, "bg22k": 2, "bg44k": 1}


def spec(analysis: str, english: bool) -> dict:
    s = copy.deepcopy(ICU)
    if analysis == "standard":
        s["settings"]["analysis"] = {"analyzer": {"text": {
            "type": "custom", "tokenizer": "standard", "filter": ["lowercase"]}}}
    if english:
        for f in ("title", "description", "body"):
            s["mappings"]["properties"][f]["fields"] = {
                "en": {"type": "text", "analyzer": "english"}}
    return s


def text_root(work: Path, share: str, golden: Path, background: Path) -> Path:
    """A directory of links: every golden part plus every STEP-th background file."""
    root = work / "roots" / share
    root.mkdir(parents=True, exist_ok=True)
    files = sorted(golden.rglob("*.parquet"))
    if STEP[share]:
        files += sorted(background.glob("*.parquet"))[::STEP[share]]
    for f in files:
        link = root / f.name
        if not link.exists():
            link.symlink_to(f)
    return root


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("golden", type=Path)
    ap.add_argument("background", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    args = ap.parse_args()

    base = load_config([V / "config/isel.toml"])
    client = OpenSearch(base.text_index.url, 600.0, 5, 2.0)
    golden_link = args.work / "golden" / "golden.parquet"  # evaluate_golden reads it here
    golden_link.parent.mkdir(parents=True, exist_ok=True)
    if not golden_link.exists():
        golden_link.symlink_to(V / "work/golden/golden.parquet")
    results = json.loads(args.out.read_text()) if args.out.exists() else {}
    for name in args.variants.split(","):
        share, analysis, english, query_type, fields = VARIANTS[name]
        settings_file = args.work / "settings" / f"{name}.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(spec(analysis, english)))
        t = dataclasses.replace(base.text_index, alias=f"g20-{name.replace('_', '-')}",
                                settings_file=str(settings_file), keep_prev=False,
                                query_type=query_type, fields=fields)
        cfg = dataclasses.replace(base, text_index=t,
                                  run=dataclasses.replace(base.run, work_dir=str(args.work)))
        m = build(cfg, client, text_root(args.work, share, args.golden, args.background))
        now = datetime.now(UTC)
        ev = evaluate_golden(cfg, client, 20, now=now)
        queries = args.work / "text_index" / "eval" / f"{utc_id(now)}.queries.parquet"
        if name != "icu":
            client.call("DELETE", f"/{m['index']}")
        results[name] = {"background": share, "docs": m["docs"], "index": m["index"],
                         "queries_file": str(queries),
                         **{tag: ev[tag] for tag in ("all", "head", "random") if tag in ev}}
        args.out.write_text(json.dumps(results, indent=1))
        a = ev["all"]
        print(json.dumps({name: {k: a[k] for k in ("presence", "recall_pairs",
                                                   "recall_given_present", "ndcg",
                                                   "zero_hit_rate")}}), flush=True)


if __name__ == "__main__":
    main()
