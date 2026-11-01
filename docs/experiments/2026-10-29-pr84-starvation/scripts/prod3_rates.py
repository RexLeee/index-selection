"""Host2 Loki: request.end per second and active_domains of each crawler process at fixed times.

Run on Host2: ssh ntu-sa2 python3 - < prod3_rates.py
"""
import datetime as dt
import json
import urllib.parse
import urllib.request

L = "http://127.0.0.1:3100/loki/api/v1/"
def q(expr, t):
    u = L + "query?" + urllib.parse.urlencode({"query": expr, "time": str(int(t.timestamp()))})
    return json.load(urllib.request.urlopen(u, timeout=300))["data"]["result"]
def ts(s): return dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC)
cols = ["2026-10-28T12:00:00", "2026-10-29T04:00:00", "2026-10-29T15:00:00", "2026-10-29T22:00:00", "2026-10-30T02:15:00"]
rate, ad = {}, {}
for t in cols:
    for x in q('sum by (worker_id) (count_over_time({service="crawler",event="request.end"}[10m]))', ts(t)):
        rate.setdefault(int(x["metric"]["worker_id"]), {})[t] = float(x["value"][1]) / 600
    for x in q('avg by (worker_id) (avg_over_time({service="crawler",event="spider.heartbeat"} | json | unwrap active_domains [10m]))', ts(t)):
        ad.setdefault(int(x["metric"]["worker_id"]), {})[t] = float(x["value"][1])
print("worker  " + "  ".join(f"{c[5:16]:>11}" for c in cols) + "   (req/s / active_domains)")
for w in sorted(rate):
    print(f"{w:>4}    " + "  ".join(f"{rate[w].get(c, 0):5.1f}/{ad.get(w, {}).get(c, 0):5.0f}" for c in cols))
print("sum     " + "  ".join(f"{sum(d.get(c, 0) for d in rate.values()):11.1f}" for c in cols))
