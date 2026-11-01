"""Host2 Loki: per hostname normal rate u, processes, top process share, p50 latency, 60 s peak.

Replace @RX@ with an anchored regex of hostnames (the 64M list's hosts with >= 50k URLs,
from host_counts.py), then: ssh ntu-sa2 python3 - <start> <end> < script > hosts.json.
Queries go hour by hour: one 6 h query over request.end outlives Loki's 30 s write timeout.
"""
import datetime as dt
import json
import statistics as st
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

L = "http://127.0.0.1:3100/loki/api/v1/"
RX = "@RX@".replace("\\", "\\\\")
start, end = sys.argv[1], sys.argv[2]
def ts(s): return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC).timestamp())
S, E = ts(start), ts(end)
def q(expr, t):
    u = L + "query?" + urllib.parse.urlencode({"query": expr, "time": str(t)})
    return json.load(urllib.request.urlopen(u, timeout=600))["data"]["result"]
def qr(expr, s, e, step=60):
    u = L + "query_range?" + urllib.parse.urlencode({"query": expr, "start": str(s), "end": str(e), "step": str(step)})
    return json.load(urllib.request.urlopen(u, timeout=600))["data"]["result"]
sel = '{service="crawler",event="request.end"} | json domain, download_latency_ms, worker_id | domain=~"' + RX + '"'
cnt, wk, latw, mx = defaultdict(float), defaultdict(lambda: defaultdict(float)), defaultdict(list), defaultdict(float)
for h in range(S, E, 3600):
    t = h + 3600
    for x in q(f"sum by (domain, worker_id) (count_over_time({sel} [1h]))", t):
        d, w, v = x["metric"]["domain"], x["metric"]["worker_id"], float(x["value"][1])
        cnt[d] += v; wk[d][w] += v
    for x in q(f"quantile_over_time(0.5, {sel} | unwrap download_latency_ms [1h]) by (domain)", t):
        latw[x["metric"]["domain"]].append(float(x["value"][1]))
    for x in qr(f"sum by (domain, worker_id) (count_over_time({sel} [1m]))", h + 60, t):
        d = x["metric"]["domain"]
        mx[d] = max(mx[d], max(float(v[1]) for v in x["values"]))
secs = E - S
out = {"window": [start, end], "hosts": {}}
for d in cnt:
    ws = sorted(wk[d].items(), key=lambda kv: -kv[1])
    out["hosts"][d] = {"u": cnt[d] / secs, "workers": len(ws), "top_worker": ws[0][0], "top_share": ws[0][1] / cnt[d],
                       "lat_ms": st.median(latw[d]) if latw[d] else None, "max60_worker": mx[d]}
print(json.dumps(out))
