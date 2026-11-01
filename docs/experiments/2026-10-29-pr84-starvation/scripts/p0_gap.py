"""P0: a busy AutoThrottle slot's mean gap between sends, in units of its delay.

Usage: p0_gap.py <run dir>
Scrapy 2.19 draws the +-50 % jitter again at every queue check, so a busy slot sends on
average every ~1.218 x delay. Under target concurrency 2.0, delay = max(0.25, latency / 2).
Sends start 60 s after the crawler; gaps over 2 x delay are idle time between feed files.
"""
import itertools
import json
import statistics
import sys
from pathlib import Path

EXPECTED, TOLERANCE = 1.218, 0.05

run = Path(sys.argv[1])
t0 = int((run / "t0").read_text())
rows = [line.split() for line in (run / "access.log").read_text().splitlines()]
sends = sorted(float(r[0]) for r in rows if r[2] == "n" and float(r[0]) >= t0 + 60)
latency = [json.loads(line).get("download_latency_ms")
           for line in (run / "crawler.log").read_text().splitlines() if '"request.end"' in line]
p50 = statistics.median(x for x in latency if x is not None)
delay = max(0.25, p50 / 2000)
gaps = [b - a for a, b in itertools.pairwise(sends)]
busy = [g for g in gaps if g <= 2 * delay]
ratio = statistics.mean(busy) / delay
print(f"sends {len(sends)}, latency p50 {p50:.0f} ms, delay {delay:.3f} s")
print(f"gaps: {len(busy)} busy, {len(gaps) - len(busy)} idle; mean busy gap {statistics.mean(busy):.3f} s"
      f" = {ratio:.3f} x delay")
print(f"{'PASS' if abs(ratio - EXPECTED) <= TOLERANCE else 'FAIL'} mean gap / delay = "
      f"{EXPECTED} +- {TOLERANCE}")
