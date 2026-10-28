"""Golden batch 20 as the official metric would count it, through the product code.

Usage: golden_official.py FLAGS.parquet GOLDEN_TEXT BACKGROUND_TEXT WORK OUT.json

FLAGS.parquet holds batch 20's official coverage flags (golden_flags.py). Each index
holds the golden pages that are on the list (`is_indexed`) and were fetched, plus every
STEP-th background crawl file: 0, about 11k, 22k and 44k docs. It is built with
isel.textindex.build and searched with isel.textindex.search, with the product settings
and query.

Per tag the counts follow CrawlerAllMetricMeasure's coverage table: N distinct golden
URLs, I of them on the list, F on the list with a page in the index, and H in the top 10
of at least one of the tag's keywords. Only on-list ids count as ranked, so H <= F <= I.
Rates come with a by-query bootstrap interval in which a URL shared by c keywords weighs
1/c (evaluate.bootstrap); differences use a paired bootstrap on the same draws. The
query was chosen on the dev half of the keywords (tune_rule.md), so the test half is the
headline. The query before tuning (`combined_fields` over title^2, description and body)
also runs on the 44k index.
"""
import dataclasses
import json
import sys
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from isel.evaluate import bootstrap, paired_bootstrap
from isel.textindex import OpenSearch, build, evaluate_golden, present_ids, search

from isel.config import load_config
from isel.io import write_parquet_atomic

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
STEP = {"bg0": 0, "bg11k": 4, "bg22k": 2, "bg44k": 1}
SAMPLES, SEED = 10_000, 20
BEFORE = ("combined_fields", ["title^2", "description", "body"])
OFFICIAL = {"head": (7329, 1814), "random": (8031, 1852)}   # N and indexed, 10-18 run
TAGS = ("head", "random")
HALVES = {"test": True, "dev": False, "all": None}

flags_file, golden_dir, background_dir, work, out = map(Path, sys.argv[1:6])
flags = pq.read_table(flags_file).to_pylist()
base = load_config([V / "config/isel.toml"])
client = OpenSearch(base.text_index.url, 600.0, 5, 2.0)


def in_test_half(text: str) -> bool:
    return int(sha256(text.encode()).hexdigest(), 16) % 2 == 1


judged: dict[str, dict[int, set[str]]] = {tag: defaultdict(set) for tag in TAGS}
keyword: dict[int, str] = {}
on_list: set[str] = set()
crawled: set[str] = set()
for r in flags:
    keyword[r["query_id"]] = r["keyword"]
    on_list |= {r["doc_id"]} if r["is_indexed"] else set()
    crawled |= {r["doc_id"]} if r["is_crawled"] else set()
    for tag in r["tags"]:
        if tag in judged:
            judged[tag][r["query_id"]].add(r["doc_id"])
for tag, expected in OFFICIAL.items():
    urls = set().union(*judged[tag].values())
    if (len(urls), len(urls & on_list)) != expected:
        raise SystemExit(f"{tag}: {len(urls)} URLs, {len(urls & on_list)} on the list; "
                         f"the official run has {expected}")

# The on-list golden pages, in one Parquet beside the background files.
pages = pa.concat_tables(pq.read_table(f) for f in sorted(golden_dir.rglob("*.parquet")))
keep = pc.is_in(pc.cast(pages["doc_id"], pa.string()), value_set=pa.array(sorted(on_list)))
golden_file = work / "golden_onlist" / "golden-onlist.parquet"
write_parquet_atomic(pages.filter(keep), golden_file)
background = sorted(background_dir.glob("*.parquet"))


def text_root(share: str) -> Path:
    root = work / "roots" / share
    root.mkdir(parents=True, exist_ok=True)
    for f in [golden_file, *(background[::STEP[share]] if STEP[share] else [])]:
        if not (root / f.name).exists():
            (root / f.name).symlink_to(f)
    return root


def top10(t) -> dict[str, list[str]]:
    texts = sorted(set(keyword.values()))
    for text in texts[:t.warmup_queries]:
        search(client, t, text)
    return {text: search(client, t, text)[0] for text in texts}


def queries(tag: str, half: bool | None) -> dict[int, set[str]]:
    return {q: ids for q, ids in judged[tag].items()
            if half is None or in_test_half(keyword[q]) == half}


def ranked(qs: dict[int, set[str]], top: dict[str, list[str]]) -> set[tuple[int, str]]:
    """(query, id) pairs in the top 10; only on-list ids count, as for the indexed rate."""
    return {(q, d) for q, ids in qs.items()
            for d in ids & set(top[keyword[q]][:10]) if d in on_list}


def rate(qs: dict[int, set[str]], within: set[str], hit: set[str]) -> dict:
    """The share of `within` in `hit`, resampled by query."""
    sub = {q: ids & within for q, ids in qs.items() if ids & within}
    lo, hi = bootstrap(sub, hit, SAMPLES, SEED) if sub else (None, None)
    return {"rate": len(hit & within) / len(within) if within else None, "ci95": [lo, hi]}


def measure(qs: dict[int, set[str]], top: dict[str, list[str]], present: set[str]) -> dict:
    urls = set().union(*qs.values())
    listed = urls & on_list
    indexed = listed & present
    pairs = ranked(qs, top)
    hit = {d for _, d in pairs}
    shared = Counter(d for ids in qs.values() for d in ids)
    return {"queries": len(qs), "N": len(urls), "crawled": len(urls & crawled),
            "I": len(listed), "F": len(indexed), "H": len(hit),
            "urls_in_several_keywords": sum(c > 1 for c in shared.values()),
            "I/N": len(listed) / len(urls), "F/I": len(indexed) / len(listed),
            "H/N": rate(qs, urls, hit), "H/I": rate(qs, listed, hit),
            "H/F": rate(qs, indexed, hit),
            "pairs_recall": len(pairs) / sum(len(ids) for ids in qs.values())}


def paired(qs: dict[int, set[str]], within: set[str], a: set[str], b: set[str]) -> list:
    """The 95 % interval of rate(b) - rate(a) on `within`, on the same query draws."""
    sub = {q: ids & within for q, ids in qs.items() if ids & within}
    return list(paired_bootstrap(sub, a, b, SAMPLES, SEED))


every = sorted(set().union(*(set().union(*judged[t].values()) for t in TAGS)))
result = {"samples": SAMPLES, "seed": SEED, "query_type": base.text_index.query_type,
          "fields": base.text_index.fields, "settings_file": base.text_index.settings_file,
          "runs": {}}
hits: dict[tuple[str, str], dict[tuple[str, str], set[str]]] = {}
for share in STEP:
    t = dataclasses.replace(base.text_index, alias=f"off20-{share}", keep_prev=False,
                            settings_file=str(V / base.text_index.settings_file))
    cfg = dataclasses.replace(base, text_index=t,
                              run=dataclasses.replace(base.run, work_dir=str(work)))
    manifest = build(cfg, client, text_root(share))
    present = present_ids(client, t.alias, every, t.lookup_chunk)
    variants = {"tuned": t}
    if share == "bg44k":
        variants["before"] = dataclasses.replace(t, query_type=BEFORE[0], fields=BEFORE[1])
    for name, tv in variants.items():
        top = top10(tv)
        run = {"docs": manifest["docs"], "bytes_per_doc": manifest["bytes_per_doc"],
               "query_type": tv.query_type, "fields": tv.fields}
        for tag in TAGS:
            for half_name, half in HALVES.items():
                qs = queries(tag, half)
                run[f"{tag}/{half_name}"] = measure(qs, top, present)
                hits[(share, name)] = hits.get((share, name), {})
                hits[(share, name)][(tag, half_name)] = {d for _, d in ranked(qs, top)}
                hits[(share, name)][(tag, half_name, "F")] = (
                    set().union(*qs.values()) & on_list & present)
        result["runs"][f"{share}/{name}"] = run
        print(json.dumps({f"{share}/{name}": {
            k: {"H/N": v["H/N"]["rate"], "H/F": v["H/F"]["rate"]}
            for k, v in run.items() if "/" in k and k.endswith("/test")}}), flush=True)
    if share != "bg44k":
        client.call("DELETE", f"/{manifest['index']}")
        continue
    # The product eval on the same index: it counts every golden id it finds, on the list
    # or not, so its distinct-URL recall can only be higher than H/N.
    link = work / "golden" / "golden.parquet"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        link.symlink_to(V / "work/golden/golden.parquet")
    ev = evaluate_golden(dataclasses.replace(cfg, eval=dataclasses.replace(
        cfg.eval, bootstrap_samples=SAMPLES, bootstrap_seed=SEED)), client, 20)
    result["product_eval_bg44k"] = {tag: {k: ev[tag][k] for k in (
        "queries", "pairs", "recall_pairs", "recall_unique", "recall_unique_ci95", "presence",
        "recall_given_present", "zero_hit_rate", "took_ms_p50", "took_ms_p95")}
        for tag in ("all", *TAGS)}

result["paired"] = {}
for tag in TAGS:
    for half_name, half in HALVES.items():
        qs = queries(tag, half)
        urls = set().union(*qs.values())
        key = (tag, half_name)
        f = hits[("bg44k", "tuned")][(tag, half_name, "F")]
        result["paired"][f"{tag}/{half_name}"] = {
            "tuned_minus_before_H/N": paired(qs, urls, hits[("bg44k", "before")][key],
                                             hits[("bg44k", "tuned")][key]),
            **{f"{s}_minus_bg0_H/F": paired(qs, f, hits[("bg0", "tuned")][key],
                                            hits[(s, "tuned")][key])
               for s in ("bg11k", "bg22k", "bg44k")}}
out.write_text(json.dumps(result, indent=1))
print(json.dumps(result["paired"], indent=1))
