"""Round 5: send gaps of recrawl requests, hot hosts (r000000-r000019, which also get normal) apart.

Usage: diag5_gapsplit.py <run dir> <interval s>. Window: submit + 60 s to the end.
"""
import itertools
import sys
from collections import defaultdict
from pathlib import Path

run, interval = Path(sys.argv[1]), float(sys.argv[2])
a = int((run / "t_submit").read_text()) + 60
by = defaultdict(list)
for line in (run / "access.log").read_text().splitlines():
    t, host, kind, _, path = line.split(maxsplit=4)
    if kind == "m" and "/moved/" not in path and float(t) >= a:
        by[host].append(float(t))
for name, hot in (("hot", True), ("other", False)):
    g = sorted(y - x for h, ts in by.items() if (int(h[1:7]) < 20) == hot for x, y in itertools.pairwise(sorted(ts)))
    p1, p5, p50 = (g[int(p * len(g))] for p in (0.01, 0.05, 0.5))
    print(f"{run.name} {name}: {len(g)} gaps, mean {sum(g) / len(g):.2f}, p1 {p1:.2f}, p5 {p5:.2f},"
          f" p50 {p50:.2f} s, under {0.9 * interval:.2f} s: {sum(x < 0.9 * interval for x in g) / len(g):.1%}")
