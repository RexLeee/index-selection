"""The primary comparison of nm_analyze.py with corrected language weights.

Usage: nm_sensitivity.py OUT.json RUNS.parquet [RUNS.parquet ...]
The pre-registered weights counted 34 Telugu, Malayalam, Kannada, Gujarati and Greek
keywords of batch 20 as Latin (de, fr, es). Here Latin-extended has its true 131
keywords; the other scripts have no NanoMIRACL split and drop out, as Tamil did.
Same paired bootstrap as nm_analyze.py: queries resampled within each language,
10,000 draws, seed 20261023.
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

COUNTS = {"en": 1433, "zh": 177, "ja": 106, "de": 131 / 3, "fr": 131 / 3, "es": 131 / 3,
          "hi": 38, "bn": 4}
W = {k: v / sum(COUNTS.values()) for k, v in COUNTS.items()}
A, B = "qw_chinese_compatible", "icu"
DRAWS, SEED = 10_000, 20261023

rows = pa.concat_tables([pq.read_table(p) for p in sys.argv[2:]]).to_pylist()
by: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
for r in rows:
    by[(r["variant"], r["lang"])][r["qid"]] = r["ndcg"]
diffs = {lang: [by[(B, lang)][q] - by[(A, lang)][q] for q in sorted(by[(A, lang)])]
         for lang in W}
point = sum(W[lang] * float(np.mean(d)) for lang, d in diffs.items())
rng = random.Random(SEED)
draws = [sum(W[lang] * sum(rng.choices(d, k=len(d))) / len(d) for lang, d in diffs.items())
         for _ in range(DRAWS)]
lo, hi = np.percentile(draws, [2.5, 97.5])
out = {"weights": W, "a": A, "b": B, "diff": point, "ci95": [float(lo), float(hi)],
       "Q_holds": float(lo) >= -0.02}
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
