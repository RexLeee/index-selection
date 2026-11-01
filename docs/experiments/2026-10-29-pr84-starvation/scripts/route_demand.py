"""Per-process recrawl demand of a capped list, and the slot room on its capped hostnames.

Usage: route_demand.py <host counts csv> <prod hosts json> <days> <cap> [<worker> <sizes out>]
The CSV (host,n) comes from host_counts.py; the JSON from prod3_hosts.py. Routing follows
WebCrawler `libs/db/sharding`: eTLD+1, the ingest.yaml overrides, else md5 % 256, and
process = shard // 16. The DB's split-subdomain list is not applied. A hostname's
AutoThrottle slot rate is 1 / (1.218 x max(0.25, latency / 2)); 1.218 is the mean gap of
a busy slot in delay units, because Scrapy draws the +-50 % jitter again at every check.
With <worker>, writes that process's host sizes, largest first, for iselsim.py.
"""
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import tldextract

OVERRIDES = {"wikipedia.org": 0, "x.com": 32, "youtube.com": 64, "reddit.com": 96, "instagram.com": 128,
             "espn.com": 160, "facebook.com": 192, "yahoo.com": 224}
counts, prod, days, cap = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
worker_out = (int(sys.argv[5]), sys.argv[6]) if len(sys.argv) > 6 else None
ext = tldextract.TLDExtract(suffix_list_urls=())
post = json.loads(Path(prod).read_text())["hosts"]
secs = days * 86400
demand, hosts, capped, sizes = defaultdict(float), defaultdict(int), [], []
for r in csv.DictReader(Path(counts).read_text().splitlines()):
    host, n = r["host"], int(r["n"])
    key = ext(host).top_domain_under_public_suffix or host
    worker = OVERRIDES.get(key, int(hashlib.md5(key.encode()).hexdigest(), 16) % 256) // 16
    demand[worker] += n / secs
    hosts[worker] += 1
    if n >= cap * 0.99:
        capped.append((host, n, worker))
    if worker_out and worker == worker_out[0]:
        sizes.append(n)
total = sum(demand.values())
print(f"recrawl demand over {days:g} d: {total:.1f} req/s, {total / 16:.2f} per process on average")
for w in range(16):
    print(f"  process {w:02d}: {demand[w]:5.2f} req/s, {hosts[w]:,} hostnames")
print("capped hostnames: URLs, routed process, Loki top process, normal u, p50 latency, slot r, r - u")
for host, n, w in sorted(capped, key=lambda x: -x[1]):
    p = post.get(host)
    if p and p["lat_ms"]:
        r = 1 / (1.218 * max(0.25, p["lat_ms"] / 2000))
        print(f"  {host:30s} {n:8,d} {w:02d} {int(p['top_worker']):02d} {p['u']:.3f} {p['lat_ms']:5.0f} "
              f"{r:.2f} {r - p['u']:.2f}")
    else:
        print(f"  {host:30s} {n:8,d} {w:02d} (no normal traffic in the Loki window)")
if worker_out:
    sizes.sort(reverse=True)
    Path(worker_out[1]).write_text("\n".join(map(str, sizes)) + "\n")
    print(f"process {worker_out[0]:02d}: {len(sizes):,} hostnames, {sum(sizes):,} URLs -> {worker_out[1]}")
