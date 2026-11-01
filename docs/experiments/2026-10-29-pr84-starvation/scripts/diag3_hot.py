"""F4: per hot host, how much normal it keeps against its slot headroom r - u.

Usage: diag_hot.py <run dir> <F0 run dir>
r = 1 / (1.218 x max(0.25, latency p50 / 2)) from the run's own latency for that host;
u = the host's normal rate in F0 over the same window; recrawl demand is 0.2 req/s.
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def window(run):
    ts, te = (int((run / n).read_text()) for n in ("t_submit", "t_end"))
    return ts + 60, min(te, ts + 60 + 59 * 60)


def rates(run, kind):
    a, b = window(run)
    c = Counter()
    for line in (run / "access.log").read_text().splitlines():
        t, host, k, _, _ = line.split(maxsplit=4)
        if k == kind and host.startswith("r") and a <= float(t) < b:
            c[host] += 1
    return {h: n / (b - a) for h, n in c.items()}


run, ref = Path(sys.argv[1]), Path(sys.argv[2])
lat = defaultdict(list)
for line in (run / "crawler.log").read_text().splitlines():
    if '"request.end"' in line and "recrawl-sim" in line:
        r = json.loads(line)
        if r.get("download_latency_ms") is not None:
            lat[r["domain"]].append(r["download_latency_ms"])
u, n, m = rates(ref, "n"), rates(run, "n"), rates(run, "m")
print("host                       lat s  slot r   F0 u  r - u  normal  kept  recrawl  total/r")
for h in sorted(u):
    r = 1 / (1.218 * max(0.25, statistics.median(lat[h]) / 2000))
    print(f"{h:26} {statistics.median(lat[h]) / 1000:5.2f} {r:7.2f} {u[h]:6.3f} {r - u[h]:6.2f}"
          f" {n.get(h, 0):7.3f} {n.get(h, 0) / u[h]:5.0%} {m.get(h, 0):8.3f} {(n.get(h, 0) + m.get(h, 0)) / r:7.0%}")
