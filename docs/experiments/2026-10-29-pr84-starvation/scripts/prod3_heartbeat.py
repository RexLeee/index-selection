"""Host2 Loki: 6 h mean active_domains of processes 03 and 07 every 12 h over 8 days.

Run on Host2: ssh ntu-sa2 python3 - < prod3_heartbeat.py
"""
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

L = "http://127.0.0.1:3100/loki/api/v1/"
def q(expr, t):
    u = L + "query?" + urllib.parse.urlencode({"query": expr, "time": str(t)})
    try:
        return json.load(urllib.request.urlopen(u, timeout=300))["data"]["result"]
    except urllib.error.HTTPError as e:
        return "ERR " + e.read().decode()[:120]
end = dt.datetime(2026, 10, 30, 0, 0, tzinfo=dt.UTC)
t = end - dt.timedelta(days=8)
while t <= end:
    r = q('avg by (worker_id) (avg_over_time({service="crawler",event="spider.heartbeat",worker_id=~"7|3"} | json | unwrap active_domains [6h]))', int(t.timestamp()))
    if isinstance(r, str):
        print(t.isoformat()[:16], r)
    else:
        print(t.isoformat()[:16], {x["metric"]["worker_id"]: round(float(x["value"][1]), 1) for x in r})
    t += dt.timedelta(hours=12)
