"""Round 5: does the crawler's per-host interval hold at send time?

Usage: diag5_pace.py <run dir> <interval s>
Window: submit + 60 s to the end. From the mock web's access log, per recrawl host:
gaps between consecutive recrawl requests, gaps shorter than 0.9 x the interval, and
the busiest 60 s of recrawl alone and of normal + recrawl.
"""
import bisect
import itertools
import sys
from collections import defaultdict
from pathlib import Path

run, interval = Path(sys.argv[1]), float(sys.argv[2])
ts, te = (int((run / n).read_text()) for n in ("t_submit", "t_end"))
a, b = ts + 60, te
manual, total = defaultdict(list), defaultdict(list)
for line in (run / "access.log").read_text().splitlines():
    t, host, kind, _, path = line.split(maxsplit=4)
    t = float(t)
    if host.startswith("r") and kind in "nm" and a <= t < b:
        total[host].append(t)
        if kind == "m" and "/moved/" not in path:
            manual[host].append(t)


def busiest(times):
    times = sorted(times)
    return max((bisect.bisect_left(times, x + 60) - i for i, x in enumerate(times)), default=0)


def q(xs, p):
    return xs[min(len(xs) - 1, int(p * len(xs)))]


gaps = sorted(y - x for times in manual.values() for x, y in itertools.pairwise(sorted(times)))
short = sum(g < 0.9 * interval for g in gaps)
print(f"{run.name}: {(b - a) / 60:.0f} min, interval {interval} s, {len(manual)} recrawl hosts, {len(gaps)} gaps")
if gaps:
    print(f"gap p1 {q(gaps, 0.01):.2f} p5 {q(gaps, 0.05):.2f} p50 {q(gaps, 0.5):.2f} s;"
          f" shorter than {0.9 * interval:.1f} s: {short} ({short / len(gaps):.2%})")
print(f"busiest 60 s, recrawl only: {max(map(busiest, manual.values()), default=0)};"
      f" normal + recrawl: {max(map(busiest, total.values()), default=0)}")
