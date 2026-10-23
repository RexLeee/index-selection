"""Recall given present of golden_tune.py's runs on one half of the keywords (tune_rule.md).

Usage: tune_report.py TUNE.json WORK HALF BASELINE [RUN ...] [--out OUT.json]

HALF is `dev`, `test` or `all` (descriptive, after the test decision). Each run is
compared with BASELINE by a paired bootstrap over the half's query ids with a present
pair (10,000 draws, seed 20), overall and per keyword script. Without RUNs, every run
in TUNE.json is reported.
"""
import argparse
import json
import unicodedata
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

from isel.urlnorm import md5_number_lower

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
SAMPLES, SEED = 10_000, 20


def half(keyword: str) -> str:
    return "test" if int(sha256(keyword.encode()).hexdigest(), 16) % 2 else "dev"


def script(keyword: str) -> str:
    """As golden_analyze.py: the first word of the Unicode names of non-ASCII letters."""
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


def interval(f: np.ndarray, p: np.ndarray, fb: np.ndarray, rng: np.random.Generator) -> dict:
    """Recall difference f/p - fb/p over the rows, with its bootstrap 95 % interval."""
    idx = rng.integers(0, len(p), size=(SAMPLES, len(p)))
    d = (f[idx].sum(1) - fb[idx].sum(1)) / p[idx].sum(1)
    return {"delta": float((f.sum() - fb.sum()) / p.sum()),
            "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tune", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("half", choices=["dev", "test", "all"])
    ap.add_argument("baseline")
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    tune = json.loads(args.tune.read_text())
    g = pq.read_table(V / "work/golden/golden.parquet").filter(pc.field("batch_id") == 20)
    judged: dict[int, set[str]] = defaultdict(set)
    keyword = {}
    for r in g.to_pylist():
        judged[r["query_id"]].add(str(md5_number_lower(r["canonical_url"])))
        keyword[r["query_id"]] = r["keyword"]
    present_ids = {str(i) for f in (args.work / "roots" / "bg44k").glob("*.parquet")
                   for i in pq.read_table(f, columns=["doc_id"]).column("doc_id").to_pylist()}
    qs = sorted(q for q in judged if args.half in ("all", half(keyword[q]))
                and judged[q] & present_ids)
    p = np.array([len(judged[q] & present_ids) for q in qs])
    groups = defaultdict(list)
    for i, q in enumerate(qs):
        groups[script(keyword[q])].append(i)

    def found(run: str) -> np.ndarray:
        rows = {r["query_id"]: r["found"]
                for r in pq.read_table(tune[run]["queries_file"]).to_pylist()}
        return np.array([rows[q] for q in qs])

    fb = found(args.baseline)
    report = {"half": args.half, "queries": len(qs), "present_pairs": int(p.sum()),
              "baseline": args.baseline, "runs": {}}
    for run in args.runs or list(tune):
        f = found(run)
        rng = np.random.default_rng(SEED)
        row = {"recall_given_present": float(f.sum() / p.sum()),
               "bytes_per_doc": round(tune[run]["bytes_per_doc"]),
               "size_vs_baseline": round(tune[run]["bytes_per_doc"]
                                         / tune[args.baseline]["bytes_per_doc"] - 1, 3)}
        if run != args.baseline:
            row |= interval(f, p, fb, rng)
            row["by_script"] = {
                s: {"pairs": int(p[i].sum()), **interval(f[i], p[i], fb[i], rng)}
                for s, i in sorted(groups.items(), key=lambda kv: -p[kv[1]].sum())}
        report["runs"][run] = row
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
