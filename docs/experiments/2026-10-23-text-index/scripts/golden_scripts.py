"""Zero-hit rate and latency of a golden eval, by the script of each keyword.

Usage: golden_scripts.py EVAL.queries.parquet
A keyword's script is the first word of the Unicode names of its non-ASCII letters:
Latin only is "latin-ext", any kana is "kana", Han without kana is "han".
"""
import json
import sys
import unicodedata
from collections import defaultdict

import numpy as np
import pyarrow.parquet as pq


def script(keyword: str) -> str:
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
    return "+".join(sorted(n.lower() for n in names))


groups = defaultdict(list)
for r in pq.read_table(sys.argv[1]).to_pylist():
    groups[script(r["keyword"])].append(r)
out = {}
for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    took = [r["took_ms"] for r in rows]
    out[name] = {"queries": len(rows),
                 "zero_hit": round(sum(r["hits"] == 0 for r in rows) / len(rows), 3),
                 "took_p50": float(np.percentile(took, 50)),
                 "took_p95": float(np.percentile(took, 95))}
print(json.dumps(out, indent=1))
