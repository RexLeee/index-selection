"""Host2 Loki: per (hostname, process) 60 s peak and how many hostnames reach 12/24/36/48 a minute.

Usage: ssh ntu-sa2 python3 - <start> [<start> ...] < prod3_peak60.py (5 min from each start;
longer windows exceed Loki's 10,000-series limit).
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

L = "http://127.0.0.1:3100/loki/api/v1/"
def qr(expr, s, e, step=60):
    u = L + "query_range?" + urllib.parse.urlencode({"query": expr, "start": str(s), "end": str(e), "step": str(step)})
    try:
        return json.load(urllib.request.urlopen(u, timeout=600))["data"]["result"]
    except urllib.error.HTTPError as err:
        sys.exit(err.read().decode()[:300])
base = '{service="crawler",event="request.end"} | json domain'
for s0 in sys.argv[1:]:
    s = int(dt.datetime.fromisoformat(s0).replace(tzinfo=dt.UTC).timestamp()); e = s + 300
    top = qr(f"max(sum by (domain, worker_id) (count_over_time({base} [1m])))", s, e)
    mx = max(float(v[1]) for v in top[0]["values"]) if top else 0
    cnts = []
    for t in (12, 24, 36, 48):
        r = qr(f"count(sum by (domain, worker_id) (count_over_time({base} [1m])) >= {t})", s, e)
        cnts.append(f">={t}: max {max((float(v[1]) for v in r[0]['values']), default=0):.0f}" if r else f">={t}: 0")
    act = qr(f"count(sum by (domain) (count_over_time({base} [1m])))", s, e)
    a = sorted(float(v[1]) for v in act[0]["values"])
    print(f"{s0} 5 min: per (host, worker) 60-s max {mx:.0f}; hosts at one minute {'; '.join(cnts)}; active hosts/min median {a[len(a)//2]:.0f}")
