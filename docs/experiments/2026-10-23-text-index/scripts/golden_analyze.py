"""Paired comparison and breakdowns of golden_eval.py's runs.

Usage: golden_analyze.py EVAL.json WORK FETCH_LOG OUT.json

Recall given present is per (keyword, golden URL) pair whose page is in the index:
found pairs / present pairs. Variants are compared with `icu` by a paired bootstrap over
keywords (10,000 draws, seed 20), on the keywords with a present pair in either run.
The breakdowns re-run `icu`'s queries on its kept index to see which pairs it found.
"""
import dataclasses
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
from isel.textindex import OpenSearch, search

from isel.config import load_config
from isel.urlnorm import md5_number_lower

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
SAMPLES, SEED = 10_000, 20


def script(keyword: str) -> str:
    """As golden_scripts.py: the first word of the Unicode names of non-ASCII letters."""
    names = {unicodedata.name(c, "?").split()[0] for c in keyword
             if not c.isascii() and c.isalpha()}
    if not names:
        return "ascii"
    if names <= {"LATIN"}:
        return "latin-ext"
    if names & {"HIRAGANA", "KATAKANA"}:
        return "kana"
    if "CJK" in names:
        return "han"
    return "other"


def ids_under(root: Path) -> set[str]:
    return {str(i) for f in root.glob("*.parquet")
            for i in pq.read_table(f, columns=["doc_id"]).column("doc_id").to_pylist()}


def rate(found: np.ndarray, present: np.ndarray) -> float:
    return float(found.sum() / present.sum()) if present.sum() else float("nan")


def main() -> None:
    runs = json.loads(Path(sys.argv[1]).read_text())
    work, log_path, out = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    g = pq.read_table(V / "work/golden/golden.parquet")
    g = g.filter(pc.field("batch_id") == 20).to_pylist()
    judged: dict[int, set[str]] = defaultdict(set)
    keyword, tags = {}, {}
    for r in g:
        judged[r["query_id"]].add(str(md5_number_lower(r["canonical_url"])))
        keyword[r["query_id"]], tags[r["query_id"]] = r["keyword"], r["tags"] or []
    qs = sorted(judged)

    def per_query(name: str) -> tuple[np.ndarray, np.ndarray]:
        present_ids = ids_under(work / "roots" / runs[name]["background"])
        found = {r["query_id"]: r["found"]
                 for r in pq.read_table(runs[name]["queries_file"]).to_pylist()}
        return (np.array([found.get(q, 0) for q in qs]),
                np.array([len(judged[q] & present_ids) for q in qs]))

    data = {name: per_query(name) for name in runs}
    rng = np.random.default_rng(SEED)
    fb, pb = data["icu"]
    report: dict = {"variants": {}}
    for name, (f, p) in data.items():
        row = {"docs": runs[name]["docs"], "present_pairs": int(p.sum()),
               "found_pairs": int(f.sum()), "recall_given_present": rate(f, p),
               "head": runs[name]["head"]["recall_given_present"],
               "random": runs[name]["random"]["recall_given_present"],
               "ndcg": runs[name]["all"]["ndcg"],
               "zero_hit_rate": runs[name]["all"]["zero_hit_rate"]}
        if name != "icu":
            keep = np.flatnonzero((p > 0) | (pb > 0))
            draws = rng.integers(0, len(keep), size=(SAMPLES, len(keep)))
            idx = keep[draws]
            delta = f[idx].sum(1) / p[idx].sum(1) - fb[idx].sum(1) / pb[idx].sum(1)
            row["delta_vs_icu"] = rate(f[keep], p[keep]) - rate(fb[keep], pb[keep])
            row["delta_ci95"] = [float(np.percentile(delta, 2.5)),
                                 float(np.percentile(delta, 97.5))]
        report["variants"][name] = row

    # Breakdowns of icu: which present pairs made its top 10.
    base = load_config([V / "config/isel.toml"])
    t = dataclasses.replace(base.text_index, alias="g20-icu")
    client = OpenSearch(t.url, 120.0, 3, 1.0)
    present_ids = ids_under(work / "roots" / runs["icu"]["background"])
    log = {}
    for line in log_path.open():
        rec = json.loads(line)
        log[rec["doc_id"]] = rec
    groups: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    misses = []
    for q in qs:
        ranked = search(client, t, keyword[q])[0]
        for d in judged[q] & present_ids:
            hit = d in ranked
            rec = log.get(d, {})
            n = rec.get("text_len") or 0
            length = "<200" if n < 200 else "200-2k" if n < 2000 else ">=2k"
            host = rec.get("host", "background")
            site = host if host in ("www.youtube.com", "en.wikipedia.org") else "other"
            for dim, key in (("script", script(keyword[q])), ("text_len", length),
                             ("site", site), ("tag", "+".join(sorted(tags[q])))):
                groups[dim][key][0] += hit
                groups[dim][key][1] += 1
            if not hit and len(misses) < 40:
                misses.append({"keyword": keyword[q], "url": rec.get("url"),
                               "text_len": n, "top1": ranked[0] if ranked else None})
    report["icu_breakdown"] = {
        dim: {k: {"present_pairs": v[1], "recall_given_present": round(v[0] / v[1], 3)}
              for k, v in sorted(g.items(), key=lambda kv: -kv[1][1])}
        for dim, g in groups.items()}
    for m in misses:
        if m["top1"]:
            hit = client.call("POST", "/g20-icu/_search", {
                "query": {"ids": {"values": [m["top1"]]}}, "_source": ["url", "title"]})
            src = hit["hits"]["hits"][0]["_source"] if hit["hits"]["hits"] else {}
            m["top1"] = {"url": src.get("url"), "title": src.get("title")}
    report["icu_misses"] = misses
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(report["variants"], indent=1))


if __name__ == "__main__":
    main()
