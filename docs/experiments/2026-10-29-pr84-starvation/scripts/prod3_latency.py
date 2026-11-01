"""Host2 Loki: download latency p50/p90/p99 (median over processes) before and after the 10-29 deploy.

Run on Host2: ssh ntu-sa2 python3 - < prod3_latency.py
"""
import datetime as dt
import json
import urllib.parse
import urllib.request

L = "http://127.0.0.1:3100/loki/api/v1/"
def q(expr, t):
    u = L + "query?" + urllib.parse.urlencode({"query": expr, "time": str(t)})
    return json.load(urllib.request.urlopen(u, timeout=300))["data"]["result"]
for s in ("2026-10-29T15:00:00", "2026-10-30T02:00:00"):
    t = int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC).timestamp())
    out = []
    for p in (0.5, 0.9, 0.99):
        r = q(f'quantile_over_time({p}, {{service="crawler",event="request.end"}} | json download_latency_ms | unwrap download_latency_ms [10m]) without (filename)', t)
        vals = sorted(float(x["value"][1]) for x in r)
        out.append(f"p{int(p*100)} median-over-workers {vals[len(vals)//2]:6.0f} ms")
    print(s, "; ".join(out))
