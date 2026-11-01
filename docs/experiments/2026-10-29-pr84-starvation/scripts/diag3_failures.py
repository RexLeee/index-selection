"""F5 diagnostics: which recrawl hosts break C2's 10 min rate and C1's wait, and why.

Usage: diag3_failures.py <F5 run dir> <F5_0 run dir>
Window: the first 59 min after submit + 60 s, as in analyze3.py. Host classes follow
mockweb3.py with F5's MOCK_DEAD_EVERY=10 and MOCK_ROBOTS_DENY_EVERY=20.
"""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

run, ref = Path(sys.argv[1]), Path(sys.argv[2])
EVERY = {"DEAD": 10, "ROBOTS_DENY": 20}


def has(host, name):
    return int(hashlib.md5((name + host).encode()).hexdigest()[:8], 16) % EVERY[name] == 0


def load(r):
    ts, te = (int((r / n).read_text()) for n in ("t_submit", "t_end"))
    hits = [line.split(maxsplit=4) for line in (r / "access.log").read_text().splitlines()]
    return ts, te, [(float(t), h, k, int(s), p) for t, h, k, s, p in hits]


ts, te, hits = load(run)
a, b = ts + 60, ts + 60 + 59 * 60
_, _, rhits = load(ref)
win = Counter((h, int(t - a) // 600) for t, h, k, _, _ in hits if k in "nm" and a <= t < b)
mwin = Counter((h, int(t - a) // 600) for t, h, k, _, _ in hits if k == "m" and a <= t < b)
moved = Counter((h, int(t - a) // 600) for t, h, k, _, p in hits if k == "m" and "/moved/" in p and a <= t < b)
s429 = Counter((h, int(t - a) // 600) for t, h, k, s, _ in hits if k == "m" and s == 429 and a <= t < b)
rn = Counter((h, int(t - a) // 600) for t, h, k, _, _ in rhits if k == "n" and a <= t < b)
ref_best = defaultdict(float)
for (h, _), c in rn.items():
    ref_best[h] = max(ref_best[h], c / 600)
print("C2 offenders: host window total/600 manual moved 429 ref_normal")
for (h, w), c in sorted(win.items()):
    if h.startswith("r") and c / 600 > ref_best.get(h, 0) + 0.22:
        print(f"  {h} w{w} {c / 600:.3f} m={mwin[(h, w)]} moved={moved[(h, w)]} 429={s429[(h, w)]}"
              f" ref={ref_best.get(h, 0):.3f} dead={has(h, 'DEAD')} deny={has(h, 'ROBOTS_DENY')}")

first = {}
for t, _, k, _, p in hits:
    if k == "m" and "/moved/" not in p and t < b:
        first.setdefault(p, t)
by_host = defaultdict(list)
for manifest in (run / "mb" / "pending").glob("*.json"):
    r = json.loads(manifest.read_text())
    created = datetime.fromisoformat(r["created_at"]).timestamp()
    if created >= b:
        continue
    for url in r["urls"]:
        by_host[url.split("/")[2].split(":")[0]].append((first.get("/" + url.split("/", 3)[3]), created))
waits = {}
for h, docs in by_host.items():
    last, worst = None, 0.0
    for f, s in sorted(docs, key=lambda d: (d[0] is None, d[0] or 0)):
        start = max(s, last) if last is not None else s
        worst = max(worst, (f if f is not None else b) - start)
        if f is not None:
            last = f
    waits[h] = worst
for label, keep in (("all", lambda h: True), ("dead", lambda h: has(h, "DEAD")),
                    ("robots deny", lambda h: has(h, "ROBOTS_DENY") and not has(h, "DEAD")),
                    ("healthy", lambda h: not has(h, "DEAD") and not has(h, "ROBOTS_DENY"))):
    ws = sorted(w for h, w in waits.items() if keep(h))
    if ws:
        print(f"wait {label}: hosts {len(ws)}, max {ws[-1]:.0f} s, p90 {ws[int(0.9 * len(ws))]:.0f} s,"
              f" over 600 s: {sum(w > 600 for w in ws)}")
