"""Host2 Loki: per big hostname, request.start against finished URLs (normal crawl).

extra = start - end - fail0 (no response, not robots.txt). A redirect or a retry starts a request that
never reaches response_received, so extra counts the requests RedirectMiddleware and
RetryMiddleware add. A redirect to another hostname counts on the source hostname.
Usage: ssh ntu-sa2 python3 - <start> <end> < script > out.json
"""
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

L = "http://127.0.0.1:3100/loki/api/v1/query?"
RX = "@RX@".replace("\\", "\\\\")
def ts(s): return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC).timestamp())
S, E = ts(sys.argv[1]), ts(sys.argv[2])
def q(expr, t):
    u = L + urllib.parse.urlencode({"query": expr, "time": str(t)})
    return json.load(urllib.request.urlopen(u, timeout=600))["data"]["result"]
sel = {
    "start": '{service="crawler",event="request.start"} | json domain',
    "end": '{service="crawler",event="request.end"} | json domain, status',
    # Robots.txt refusals never reach the downloader; timeouts and DNS errors do.
    "fail0": '{service="crawler",event="request.fail"} | json domain, status, fail_reason'
             ' | status="" | fail_reason!="IgnoreRequest Forbidden by robots.txt"',
    "robots": '{service="crawler",event="request.fail"} | json domain, fail_reason'
              ' | fail_reason="IgnoreRequest Forbidden by robots.txt"',
    "e3xx": '{service="crawler",event="request.end"} | json domain, status | status=~"3.."',
}
out = defaultdict(lambda: defaultdict(float))
for h in range(S, E, 3600):
    for k, s in sel.items():
        for x in q(f'sum by (domain) (count_over_time({s} | domain=~"{RX}" [1h]))', h + 3600):
            out[x["metric"]["domain"]][k] += float(x["value"][1])
    print(h, file=sys.stderr, flush=True)
json.dump(out, sys.stdout)
