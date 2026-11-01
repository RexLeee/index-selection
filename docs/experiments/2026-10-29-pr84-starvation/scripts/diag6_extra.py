"""Round 6: does a recrawl host's rate, redirects and retries included, hold at the interval?

Usage: diag6_extra.py <run dir> <interval s>
Window: submit + 120 s to the end. From the mock web's access log, per recrawl host: all
manual requests, the first request of each URL, redirect hops (/moved/ paths), and repeats
of a path (retries after a 429). Also the busiest 60 s of recrawl alone and with normal.
"""
import bisect
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

run, interval = Path(sys.argv[1]), float(sys.argv[2])
ts, te = (int((run / n).read_text()) for n in ("t_submit", "t_end"))
a, b = ts + 120, te
manual, total = defaultdict(list), defaultdict(list)
seen, first, hops, repeats = set(), defaultdict(int), defaultdict(int), defaultdict(int)
for line in (run / "access.log").read_text().splitlines():
    t, host, kind, _, path = line.split(maxsplit=4)
    t = float(t)
    if not host.startswith("r") or kind not in "nm":
        continue
    if kind == "m":
        key = (host, path)
        if "/moved/" in path:
            kind_of = hops
        elif key in seen:
            kind_of = repeats
        else:
            kind_of = first
        seen.add(key)
    if not a <= t < b:
        continue
    total[host].append(t)
    if kind == "m":
        manual[host].append(t)
        kind_of[host] += 1


def peak(times):
    times = sorted(times)
    return max((bisect.bisect_left(times, t + 60) - i for i, t in enumerate(times)), default=0)


span = b - a
hosts = sorted(manual)
rate = [len(manual[h]) / span for h in hosts]
print(f"{run.name}: {len(hosts)} recrawl hosts, {span} s, interval {interval} s "
      f"(target {1 / interval:.3f} per host per s)")
print(f"all manual requests {sum(len(manual[h]) for h in hosts)}: first {sum(first.values())}, "
      f"redirect hops {sum(hops.values())}, repeats {sum(repeats.values())}")
print(f"per host per s, all requests: p50 {st.median(rate):.3f} max {max(rate):.3f}; "
      f"first requests only: p50 {st.median(first[h] / span for h in hosts):.3f}")
print(f"busiest 60 s, recrawl only: {max(peak(manual[h]) for h in hosts)}; "
      f"normal + recrawl: {max(peak(total[h]) for h in hosts)}")
