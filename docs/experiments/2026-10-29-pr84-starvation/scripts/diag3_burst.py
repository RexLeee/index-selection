"""Are recrawl hosts' busiest minutes set by the slot rate or by the job's docs per host?

Usage: diag_burst.py <run dir> <docs per host per job>
For each recrawl host: median download latency, the slot rate 60 / (1.218 x max(0.25, lat / 2))
per minute, its busiest minute (normal + manual), and its most manual docs in any sliding
window of one release period (docs x 5 s, isel's min_gap_s).
"""
import bisect
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

run, docs = Path(sys.argv[1]), int(sys.argv[2])
ts, te = (int((run / n).read_text()) for n in ("t_submit", "t_end"))
a, b = ts + 60, te
lat = defaultdict(list)
for line in (run / "crawler.log").read_text().splitlines():
    if '"request.end"' in line and "recrawl-sim" in line:
        r = json.loads(line)
        if r.get("download_latency_ms") is not None:
            lat[r["domain"]].append(r["download_latency_ms"])
sends = defaultdict(list)
msends = defaultdict(list)
for line in (run / "access.log").read_text().splitlines():
    t, host, kind, _, path = line.split(maxsplit=4)
    t = float(t)
    if host.startswith("r") and kind in "nm" and a <= t < b:
        sends[host].append(t)
        if kind == "m" and "/moved/" not in path:
            msends[host].append(t)


def busiest(times, width):
    times = sorted(times)
    return max((bisect.bisect_left(times, x + width) - i for i, x in enumerate(times)), default=0)


rows = []
period = docs * 5
for host, ms in msends.items():
    if len(lat[host]) < 5:
        continue
    p50 = statistics.median(lat[host]) / 1000
    slot = 60 / (1.218 * max(0.25, p50 / 2))
    rows.append((host, p50, slot, busiest(sends[host], 60), busiest(ms, period), len(ms)))
rows.sort(key=lambda r: -r[3] / r[2])
print(f"{run.name}: {len(rows)} recrawl hosts; release period {period} s ({docs} docs)")
print("host                       lat s  slot/min  busiest min  ratio  most manual per period  manual total")
for host, p50, slot, top, per, n in rows[:8]:
    print(f"{host:26} {p50:5.2f} {slot:9.1f} {top:12d} {top / slot:6.2f} {per:23d} {n:13d}")
ratios = sorted(r[3] / r[2] for r in rows)
over = [r for r in rows if r[4] > docs]
print(f"busiest minute / slot rate: p50 {ratios[len(ratios) // 2]:.2f}, p90 {ratios[int(0.9 * len(ratios))]:.2f},"
      f" max {ratios[-1]:.2f}")
print(f"hosts that got more than one release ({docs} docs) inside one release period: {len(over)} of {len(rows)};"
      f" worst {max((r[4] for r in rows), default=0)}")
