"""Index and query candidates on golden batch 20, through the product build and eval.

Usage: golden_tune.py GOLDEN_TEXT BACKGROUND_TEXT WORK OUT.json --runs INDEX:QUERY[,...]

Each INDEX is built once from the golden pages plus every background file, under the
alias `tune-<index>`, and kept, so its QUERY runs only re-search it. Every run is a full
`evaluate_golden`; tune_report.py splits it into dev and test (tune_rule.md).
"""
import argparse
import copy
import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

from isel.textindex import OpenSearch, _aliased, build, evaluate_golden

from isel.config import load_config
from isel.io import utc_id

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
ICU = json.loads((V / "config/text_index/icu.json").read_text())
BASE = ["description", "body"]
EN = ["title.en^2", "description.en"]
URL_SPLIT = {"type": "pattern_replace",
             "pattern": "(?:%[0-9A-Fa-f]{2}|[^\\p{L}\\p{N}\\p{M}%])+", "replacement": " "}


def stemmed(spec: dict, stem: str) -> dict:
    spec["settings"]["analysis"]["analyzer"]["text"]["filter"] = ["icu_folding", stem]
    return spec


def english_subfields(spec: dict) -> dict:
    spec["settings"]["analysis"]["analyzer"]["text_en"] = {
        "type": "custom", "tokenizer": "icu_tokenizer", "filter": ["icu_folding", "porter_stem"]}
    for f in ("title", "description"):
        spec["mappings"]["properties"][f]["fields"] = {
            "en": {"type": "text", "analyzer": "text_en"}}
    return spec


def url_tokens(spec: dict) -> dict:
    analysis = spec["settings"]["analysis"]
    analysis["char_filter"] = {"url_split": URL_SPLIT}
    analysis["analyzer"]["url"] = {"type": "custom", "char_filter": ["url_split"],
                                   "tokenizer": "icu_tokenizer", "filter": ["icu_folding"]}
    spec["mappings"]["properties"]["url"]["fields"] = {
        "text": {"type": "text", "analyzer": "url"}}
    return spec


INDEXES = {
    "icu": [],
    "kstem": [lambda s: stemmed(s, "kstem")],
    "porter": [lambda s: stemmed(s, "porter_stem")],
    "en": [english_subfields],
    "url": [url_tokens],
}
QUERIES = {
    "comb_t2": ("combined_fields", ["title^2", *BASE]),
    "comb_t3": ("combined_fields", ["title^3", *BASE]),
    "most_t1": ("most_fields", ["title", *BASE]),
    "most_t2": ("most_fields", ["title^2", *BASE]),
    "most_t3": ("most_fields", ["title^3", *BASE]),
    "most_t2_d05": ("most_fields", ["title^2", "description^0.5", "body"]),
    "best_t1": ("best_fields", ["title", *BASE]),
    "best_t2": ("best_fields", ["title^2", *BASE]),
    "best_t3": ("best_fields", ["title^3", *BASE]),
}


def query(name: str) -> tuple[str, list[str]]:
    """A QUERIES entry, with `+en` and `+url` suffixes adding the candidate subfields."""
    base, *extra = name.split("+")
    query_type, fields = QUERIES[base]
    fields = list(fields)
    for e in extra:
        fields += {"en": EN, "url": ["url.text"]}[e]
    return query_type, fields


def index_spec(name: str) -> dict:
    spec = copy.deepcopy(ICU)
    for part in name.split("+"):
        for mod in INDEXES[part]:
            spec = mod(spec)
    return spec


def root(work: Path, golden: Path, background: Path) -> Path:
    out = work / "roots" / "bg44k"
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(golden.rglob("*.parquet")) + sorted(background.glob("*.parquet")):
        if not (out / f.name).exists():
            (out / f.name).symlink_to(f)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("golden", type=Path)
    ap.add_argument("background", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--runs", required=True)
    args = ap.parse_args()

    base = load_config([V / "config/isel.toml"])
    client = OpenSearch(base.text_index.url, 600.0, 5, 2.0)
    golden_link = args.work / "golden" / "golden.parquet"   # evaluate_golden reads it here
    golden_link.parent.mkdir(parents=True, exist_ok=True)
    if not golden_link.exists():
        golden_link.symlink_to(V / "work/golden/golden.parquet")
    text_root = root(args.work, args.golden, args.background)
    results = json.loads(args.out.read_text()) if args.out.exists() else {}
    for run in args.runs.split(","):
        index_name, query_name = run.split(":")
        alias = f"tune-{index_name.replace('+', '-')}"
        settings_file = args.work / "settings" / f"{index_name}.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(index_spec(index_name), ensure_ascii=False))
        query_type, fields = query(query_name)
        t = dataclasses.replace(base.text_index, alias=alias, settings_file=str(settings_file),
                                keep_prev=False, query_type=query_type, fields=fields)
        cfg = dataclasses.replace(base, text_index=t,
                                  run=dataclasses.replace(base.run, work_dir=str(args.work)))
        if not _aliased(client, alias):
            build(cfg, client, text_root)
        index = _aliased(client, alias)[0]
        manifest = json.loads((args.work / "text_index" / "build" / f"{index}.json").read_text())
        now = datetime.now(UTC)
        ev = evaluate_golden(cfg, client, 20, now=now)
        results[run] = {"index": index, "query_type": query_type, "fields": fields,
                        "docs": manifest["docs"], "bytes_per_doc": manifest["bytes_per_doc"],
                        "queries_file": str(args.work / "text_index" / "eval"
                                            / f"{utc_id(now)}.queries.parquet")}
        args.out.write_text(json.dumps(results, indent=1))
        print(json.dumps({run: {"bytes_per_doc": round(manifest["bytes_per_doc"]),
                                "zero_hit_rate": ev["all"]["zero_hit_rate"]}}), flush=True)


if __name__ == "__main__":
    main()
