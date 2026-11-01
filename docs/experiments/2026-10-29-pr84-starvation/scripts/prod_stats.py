"""Production calibration from two Loki query_range exports (read-only, Host2 :3100).

Usage: prod_stats.py <request.end export.json> <spider.heartbeat export.json>
Exports: {service="crawler", event="request.end"} (limit 5000) and
{service="crawler", event="spider.heartbeat"} (last 120 s, limit 400).
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def lines(path):
    for stream in json.loads(Path(path).read_text())["data"]["result"]:
        for t, line in stream["values"]:
            try:
                yield int(t), stream["stream"], json.loads(line)
            except json.JSONDecodeError:
                continue


def q(xs, f):
    return xs[min(len(xs) - 1, int(len(xs) * f))]


lat, total, ts, per_host = [], [], [], defaultdict(list)
for t, _, r in lines(sys.argv[1]):
    if r.get("download_latency_ms") is None:
        continue
    lat.append(r["download_latency_ms"])
    total.append(r.get("latency_ms") or 0)
    per_host[r.get("domain")].append(r["download_latency_ms"])
    ts.append(t)
lat.sort()
total.sort()
span = (max(ts) - min(ts)) / 1e9
print(f"request.end lines {len(lat)} over {span:.1f} s ({len(lat) / span:.0f} req/s), "
      f"hosts {len(per_host)}")
print("download_latency_ms " + " ".join(f"p{round(f * 100)} {q(lat, f):.0f}"
                                         for f in (.5, .75, .9, .99)))
print("latency_ms incl. slot wait " + " ".join(f"p{round(f * 100)} {q(total, f):.0f}"
                                                for f in (.5, .9, .99)))
medians = sorted(statistics.median(v) for v in per_host.values())
print(f"per-host median download latency p50 {q(medians, .5):.0f} p90 {q(medians, .9):.0f}")

keys = ("active_domains", "pending", "inflight", "transferring", "slot_queue", "slots",
        "scheduler_size", "engine_slot_inprogress", "scraper_itemproc_size")
per_worker = defaultdict(list)
for _, stream, r in lines(sys.argv[2]):
    per_worker[stream.get("worker_id")].append(r)
print("worker " + " ".join(keys))
for w in sorted(per_worker, key=int):
    print(w, *(int(statistics.median(r.get(k) or 0 for r in per_worker[w])) for k in keys))
