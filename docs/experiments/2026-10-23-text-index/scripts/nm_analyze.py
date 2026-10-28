"""Scores the NanoMIRACL runs and tests the pre-registered comparison (docs/TEXT_INDEX.md).

Usage: nm_analyze.py OUT.json RUNS.parquet [RUNS.parquet ...]
Primary: weighted nDCG@10 of `icu` minus `qw_chinese_compatible`, 95 % interval from a
paired bootstrap that resamples queries within each language (10,000 draws, seed 20261023).
Weights are batch 20's golden keyword scripts mapped to languages.
"""
import json
import random
import sys
from collections import defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

WEIGHTS = {"en": 1433, "zh": 177, "ja": 106, "de": 55, "fr": 55, "es": 55, "hi": 38, "bn": 4}
TOTAL = sum(WEIGHTS.values())
W = {k: v / TOTAL for k, v in WEIGHTS.items()}
PRIMARY = ("qw_chinese_compatible", "icu")       # (a, b): interval of b - a
EXPLORE = [("icu", "icu_indic"), ("icu", "icu_bigram"), ("icu", "lang"), ("standard", "icu"),
           ("qw_default", "qw_chinese_compatible")]
DRAWS, SEED = 10_000, 20261023

out_path = sys.argv[1]
rows = pa.concat_tables([pq.read_table(p) for p in sys.argv[2:]]).to_pylist()
by: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
for r in rows:
    by[(r["variant"], r["lang"])][r["qid"]] = r
variants = sorted({v for v, _ in by})
langs = [lang for lang in WEIGHTS if any((v, lang) in by for v in variants)]


def cell(variant: str, lang: str) -> dict:
    q = list(by[(variant, lang)].values())
    took = [r["took_ms"] for r in q]
    return {"ndcg": float(np.mean([r["ndcg"] for r in q])),
            "recall10": sum(r["found"] for r in q) / sum(r["judged"] for r in q),
            "zero_hit": sum(r["hits"] == 0 for r in q) / len(q),
            "took_p50": float(np.percentile(took, 50)), "took_p95": float(np.percentile(took, 95))}


table = {v: {lang: cell(v, lang) for lang in langs if (v, lang) in by} for v in variants}
summary = {}
for v, cells in table.items():
    if len(cells) == len(langs):
        summary[v] = {"weighted_ndcg": sum(W[lang] * cells[lang]["ndcg"] for lang in langs),
                      "macro_ndcg": float(np.mean([cells[lang]["ndcg"] for lang in langs])),
                      "weighted_recall10": sum(W[lang] * cells[lang]["recall10"]
                                               for lang in langs)}


def paired(a: str, b: str, only: list[str] | None = None) -> dict:
    """Stratified paired bootstrap of nDCG(b) - nDCG(a), weighted over `only` (default all)."""
    use = only or langs
    norm = sum(W[lang] for lang in use)
    diffs = {lang: [by[(b, lang)][q]["ndcg"] - by[(a, lang)][q]["ndcg"]
                    for q in sorted(by[(a, lang)])] for lang in use}
    point = sum(W[lang] / norm * float(np.mean(diffs[lang])) for lang in use)
    rng = random.Random(SEED)
    draws = []
    for _ in range(DRAWS):
        total = 0.0
        for lang in use:
            d = diffs[lang]
            total += W[lang] / norm * sum(rng.choices(d, k=len(d))) / len(d)
        draws.append(total)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"a": a, "b": b, "langs": use, "diff": point, "ci95": [float(lo), float(hi)]}


tests = {}
if all((v, lang) in by for v in PRIMARY for lang in langs):
    tests["primary"] = paired(*PRIMARY)
    tests["primary"]["Q_holds"] = tests["primary"]["ci95"][0] >= -0.02
    tests["primary:per_lang"] = {lang: paired(*PRIMARY, [lang]) for lang in langs}
for a, b in EXPLORE:
    if all((v, lang) in by for v in (a, b) for lang in langs):
        tests[f"{b}-vs-{a}"] = paired(a, b)
        tests[f"{b}-vs-{a}:per_lang"] = {lang: paired(a, b, [lang]) for lang in langs}
result = {"weights": W, "cells": table, "summary": summary, "tests": tests}
with open(out_path, "w") as fh:
    json.dump(result, fh, indent=1)
print("variant".ljust(24) + "".join(lang.rjust(7) for lang in langs) + "  w-nDCG  macro")
for v in variants:
    cells = table[v]
    line = v.ljust(24) + "".join(f"{cells[lang]['ndcg']:7.3f}" if lang in cells else "      -"
                                  for lang in langs)
    if v in summary:
        line += f"  {summary[v]['weighted_ndcg']:.3f}  {summary[v]['macro_ndcg']:.3f}"
    print(line)
for name, t in tests.items():
    if "per_lang" not in name:
        print(name, json.dumps({k: t[k] for k in t if k != "langs"}))
