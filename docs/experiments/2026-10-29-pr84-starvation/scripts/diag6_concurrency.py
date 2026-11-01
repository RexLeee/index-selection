"""Round 6: per-host concurrency at the server, and traffic to hosts that answer 429.

Usage: diag6_concurrency.py <run dir> [min requests per host (100)]
Window: submit + 60 s to the end. Reads depart.log ("arrived departed host kind status",
written by mockweb3.py when MOCK_DEPART_LOG is set) and access.log.
- Concurrency: per recrawl-sim host with manual traffic, sweep arrivals (+1) and departures
  (-1). "manual max" is the most manual requests the server held for that host at once; it
  can be at most the crawler's outstanding cap. "all mean" is the time-weighted mean of
  normal + manual requests in flight while the host had any.
- 429 hosts: hosts whose every manual answer in the window is 429. Manual requests per
  minute (mean, busiest 60 s) against the other manual hosts.
"""
import statistics
import sys
from collections import defaultdict
from pathlib import Path

run = Path(sys.argv[1])
min_requests = int(sys.argv[2]) if len(sys.argv) > 2 else 100
a = float((run / "t_submit").read_text()) + 60
b = float((run / "t_end").read_text())


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else float("nan")


events = defaultdict(list)  # host -> [(time, delta, kind)]
for line in (run / "depart.log").read_text().splitlines():
    arrived, departed, host, kind, _ = line.split()
    arrived, departed = float(arrived), float(departed)
    if kind in "nm" and host.startswith("r") and departed > a and arrived < b:
        events[host].append((max(arrived, a), 1, kind))
        events[host].append((min(departed, b), -1, kind))

manual_max, all_mean = [], []
for host, ev in events.items():
    if sum(1 for _, d, k in ev if d == 1 and k == "m") < min_requests:
        continue
    ev.sort(key=lambda e: (e[0], e[1]))  # departures before arrivals at the same instant
    manual = total = peak = 0
    busy = area = 0.0
    last = ev[0][0]
    for t, delta, kind in ev:
        if total:
            busy += t - last
            area += total * (t - last)
        last = t
        total += delta
        if kind == "m":
            manual += delta
            peak = max(peak, manual)
    manual_max.append(peak)
    all_mean.append(area / busy if busy else 0.0)

print(f"{run.name}: {len(manual_max)} hosts with >= {min_requests} manual requests")
if manual_max:
    print(f"  manual in flight, per-host max: p50 {pct(manual_max, 0.5)} p90 {pct(manual_max, 0.9)}"
          f" max {max(manual_max)}")
    print(f"  normal + manual in flight, per-host mean: p50 {pct(all_mean, 0.5):.2f}"
          f" p90 {pct(all_mean, 0.9):.2f} max {max(all_mean):.2f}")

manual_hits = defaultdict(list)  # host -> [(time, status)]
for line in (run / "access.log").read_text().splitlines():
    t, host, kind, status, _ = line.split(maxsplit=4)
    t = float(t)
    if kind == "m" and a <= t < b:
        manual_hits[host].append((t, int(status)))


def per_minute(hits):
    times = sorted(t for t, _ in hits)
    busiest, j = 0, 0
    for i, t in enumerate(times):
        while times[j] < t - 60:
            j += 1
        busiest = max(busiest, i - j + 1)
    return len(times) / ((b - a) / 60), busiest


throttled = {h: v for h, v in manual_hits.items() if v and all(s == 429 for _, s in v)}
others = {h: v for h, v in manual_hits.items() if h not in throttled and len(v) >= min_requests}
for name, group in (("429 hosts", throttled), ("other manual hosts", others)):
    if not group:
        print(f"  {name}: none")
        continue
    stats = [per_minute(v) for v in group.values()]
    print(f"  {name}: {len(group)}, manual per minute mean p50 {statistics.median(s[0] for s in stats):.1f}"
          f" max {max(s[0] for s in stats):.1f}; busiest 60 s max {max(s[1] for s in stats)}")
