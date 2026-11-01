"""Round 4: how fast big hosts get recrawled when a host is in one job at a time.

Usage: diag4_bighost.py <run dir> <sizes file> [<top N> (10)]
Window: submit + 60 s to the end. Per host, from the job manifests and the access log:
jobs that took its docs, mean time between them, recrawl docs sent per second, the days
that rate needs for its size (capped at 241,920 = 0.2 req/s x 14 d), and its busiest
minute (normal + recrawl) against its slot rate 60 / (1.218 x max(0.25, latency / 2)).
"""
import bisect
import itertools
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

run, sizes = Path(sys.argv[1]), [int(x) for x in Path(sys.argv[2]).read_text().split()]
top = int(sys.argv[3]) if len(sys.argv) > 3 else 10
ts, te = (int((run / n).read_text()) for n in ("t_submit", "t_end"))
a, b = ts + 60, te
jobs = defaultdict(list)
for manifest in (run / "mb" / "pending").glob("*.json"):
    r = json.loads(manifest.read_text())
    created = datetime.fromisoformat(r["created_at"]).timestamp()
    for host in {u.split("/")[2].split(":")[0] for u in r["urls"]}:
        jobs[host].append(created)
sends, msends = defaultdict(list), defaultdict(int)
for line in (run / "access.log").read_text().splitlines():
    t, host, kind, _, path = line.split(maxsplit=4)
    t = float(t)
    if host.startswith("r") and kind in "nm" and a <= t < b:
        sends[host].append(t)
        if kind == "m" and "/moved/" not in path:
            msends[host] += 1
lat = defaultdict(list)
for line in (run / "crawler.log").read_text().splitlines():
    if '"request.end"' in line and "recrawl-sim" in line:
        r = json.loads(line)
        if r.get("download_latency_ms") is not None:
            lat[r["domain"]].append(r["download_latency_ms"])


def busiest(times):
    times = sorted(times)
    return max((bisect.bisect_left(times, x + 60) - i for i, x in enumerate(times)), default=0)


print(f"{run.name}: {(b - a) / 60:.0f} min window")
print("host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min")
rates = []
for i, size in enumerate(sizes):
    host = f"r{i:06d}.recrawl-sim.com"
    if host not in jobs:
        continue
    starts = sorted(t for t in jobs[host] if a <= t < b)
    gaps = [y - x for x, y in itertools.pairwise(starts)]
    rate = msends[host] / (b - a)
    rates.append((size, rate))
    if i < top:
        slot = (60 / (1.218 * max(0.25, statistics.median(lat[host]) / 2000))) if lat[host] else float("nan")
        days = size / rate / 86400 if rate else float("inf")
        print(f"r{i:06d} {size:8,d} {len(starts):4d} {statistics.mean(gaps) if gaps else float('nan'):10.0f}"
              f" {rate:7.3f} {days:14.1f} {busiest(sends[host]):12d} {slot:9.1f}")
for lo in (241920, 100000, 50000, 10000):
    band = [r for s, r in rates if s >= lo]
    if band:
        print(f"hosts with >= {lo:,} URLs: {len(band)}, recrawl docs/s p50 {statistics.median(band):.3f},"
              f" min {min(band):.3f}; 14 days need {lo / 14 / 86400:.3f} for the smallest")
