"""Statistics of one or more runs, from the mock web's access log and the crawler's heartbeats.

Usage: analyze.py <run dir> [<run dir> ...]

Windows (seconds): `before` is the last 90 s of normal-only crawling (after the ramp),
`after` is submit + 60 to the end, split into `early` and `late` halves. Rates are
means of 10 s bins; the 95 % interval assumes independent bins, so it is optimistic.
"""
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import pairwise
from pathlib import Path

BIN = 10
T95 = 2.0  # ~t(0.975) for 9-30 bins


def load(run: Path):
    t0, ts, te = (int((run / n).read_text()) for n in ("t0", "t_submit", "t_end"))
    hits = []
    for line in (run / "access.log").read_text().splitlines():
        t, host, kind = line.split()
        hits.append((float(t), host, kind))
    beats = []
    for line in (run / "crawler.log").read_text().splitlines():
        if '"spider.heartbeat"' in line:
            r = json.loads(line)
            beats.append((datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S,%f").timestamp(), r))
    return t0, ts, te, hits, beats


def rate(hits, kind, a, b):
    bins = Counter(int(t - a) // BIN for t, _, k in hits if k == kind and a <= t < b)
    xs = [bins[i] / BIN for i in range(int(b - a) // BIN)]
    if not xs:
        return 0.0, 0.0
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return statistics.mean(xs), T95 * sd / math.sqrt(len(xs))


def beat_medians(beats, a, b):
    rows = [r for t, r in beats if a <= t < b]
    keys = ("inflight", "transferring", "slot_queue", "slots", "engine_slot_inprogress")
    return {k: statistics.median(r.get(k, 0) for r in rows) for k in keys} if rows else {}


def host_stats(hits, a, b):
    """Manual hosts served per minute, their request intervals, and the busiest host-minute."""
    manual = [(t, h) for t, h, k in hits if k == "m" and a <= t < b]
    per_minute = defaultdict(set)
    by_host = defaultdict(list)
    for t, h in manual:
        per_minute[int(t - a) // 60].add(h)
        by_host[h].append(t)
    gaps = [y - x for ts in by_host.values() for x, y in pairwise(ts)]
    busiest = Counter((h, int(t - a) // 60) for t, h, k in hits if k in "nm" and a <= t < b)
    return {
        "hosts_per_min": statistics.mean(len(s) for s in per_minute.values()) if per_minute else 0,
        "hosts_total": len(by_host),
        "gap_median": statistics.median(gaps) if gaps else None,
        "busiest_host_min": max(busiest.values()) if busiest else 0,
    }


def jobs(run: Path, ts: int):
    done, first = 0, None
    for p in (run / "mb" / "results").glob("*/crawler_00/progress.json"):
        r = json.loads(p.read_text())
        if r.get("completed_at"):
            done += 1
            at = datetime.fromisoformat(r["completed_at"]).timestamp() - ts
            first = at if first is None else min(first, at)
    saved = sum(1 for _ in (run / "mb" / "results").glob("*/crawler_00/[0-9]*.json"))
    total = len(list((run / "mb" / "pending").glob("*.json")))
    return done, total, first, saved


def fairness(hits, a, b):
    """Manual share of the requests, and each source's lowest 30 s rate."""
    counts = Counter(k for t, _, k in hits if k in "nm" and a <= t < b)
    bins = defaultdict(Counter)
    for t, _, k in hits:
        if k in "nm" and a <= t < b:
            bins[int(t - a) // 30][k] += 1
    full = [bins[i] for i in range(int(b - a) // 30)]
    total = counts["n"] + counts["m"]
    return {"manual_share": counts["m"] / total if total else 0.0,
            "n_min30": min(c["n"] for c in full) / 30 if full else 0.0,
            "m_min30": min(c["m"] for c in full) / 30 if full else 0.0}


def job_ages(run: Path, te: int):
    """Seconds from submit to completion, and the age of the oldest job still open at the end."""
    done, open_ages = [], []
    for manifest in (run / "mb" / "pending").glob("*.json"):
        created = datetime.fromisoformat(json.loads(manifest.read_text())["created_at"]).timestamp()
        progress = run / "mb" / "results" / manifest.stem / "crawler_00" / "progress.json"
        completed = json.loads(progress.read_text()).get("completed_at") if progress.exists() else None
        if completed:
            done.append(datetime.fromisoformat(completed).timestamp() - created)
        else:
            open_ages.append(te - created)
    return {"done_p50": statistics.median(done) if done else None,
            "done_max": max(done) if done else None,
            "open_max": max(open_ages) if open_ages else None}


def cpu(run: Path, a, b):
    xs = [float(v) for t, v in (x.split() for x in (run / "cpu.log").read_text().splitlines())
          if v != "NA" and a <= int(t) < b]
    return statistics.mean(xs) if xs else None


def summarize(run: Path) -> dict:
    _, ts, te, hits, beats = load(run)
    before, after, mid = (ts - 90, ts), (ts + 60, te), (ts + 60 + te) / 2
    s = {"run": run.name}
    for name, (a, b) in {"before": before, "after": after, "early": (ts + 60, mid),
                         "late": (mid, te)}.items():
        for kind in ("n", "m"):
            s[f"{kind}_{name}"] = rate(hits, kind, a, b)
    s["beats_before"], s["beats_after"] = beat_medians(beats, *before), beat_medians(beats, *after)
    s["hosts"] = host_stats(hits, *after)
    s["jobs_done"], s["jobs_total"], s["first_job_s"], s["saved"] = jobs(run, ts)
    s["cpu_before"], s["cpu_after"] = cpu(run, *before), cpu(run, *after)
    s["minutes_after"] = (te - ts) / 60
    s["fair"], s["ages"] = fairness(hits, *after), job_ages(run, te)
    return s


def fmt(x):
    return f"{x[0]:.1f} ± {x[1]:.1f}"


runs = [summarize(Path(a)) for a in sys.argv[1:]]
print("| run | normal before | normal after | normal kept | manual after | total after/before"
      " | manual early → late |")
print("|---|---|---|---|---|---|---|")
for s in runs:
    nb, na, ma = s["n_before"][0], s["n_after"][0], s["m_after"][0]
    print(f"| {s['run']} | {fmt(s['n_before'])} | {fmt(s['n_after'])} | {na / nb:.0%} | "
          f"{fmt(s['m_after'])} | {(na + ma) / nb:.0%} | {s['m_early'][0]:.1f} → {s['m_late'][0]:.1f} |")
print()
print("| run | inflight | transferring | waiting in slots | waiting share | manual hosts/min"
      " | manual gap p50 s | busiest host-min | jobs done | first job s | results | cpu % |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for s in runs:
    b, h = s["beats_after"], s["hosts"]
    first = f"{s['first_job_s']:.0f}" if s["first_job_s"] is not None else "-"
    gap = f"{h['gap_median']:.2f}" if h["gap_median"] is not None else "-"
    print(f"| {s['run']} | {b['inflight']:.0f} | {b['transferring']:.0f} | {b['slot_queue']:.0f} | "
          f"{b['slot_queue'] / b['inflight']:.0%} | {h['hosts_per_min']:.1f} | {gap} | "
          f"{h['busiest_host_min']} | {s['jobs_done']}/{s['jobs_total']} | {first} | {s['saved']} | "
          f"{s['cpu_after']:.0f} |")
print()
print("| run | manual share of requests | lowest 30 s normal/s | lowest 30 s manual/s"
      " | job done p50 s | job done max s | oldest open job s |")
print("|---|---|---|---|---|---|---|")


def secs(x):
    return f"{x:.0f}" if x is not None else "-"


for s in runs:
    f, a = s["fair"], s["ages"]
    print(f"| {s['run']} | {f['manual_share']:.1%} | {f['n_min30']:.1f} | {f['m_min30']:.1f} | "
          f"{secs(a['done_p50'])} | {secs(a['done_max'])} | {secs(a['open_max'])} |")
print()
for s in runs:
    b = s["beats_before"]
    print(f"{s['run']}: before jobs inflight {b['inflight']:.0f}, transferring "
          f"{b['transferring']:.0f}, waiting {b['slot_queue']:.0f}, slots {b['slots']:.0f}, "
          f"cpu {s['cpu_before']:.0f} %; after: slots {s['beats_after']['slots']:.0f}, "
          f"manual hosts touched {s['hosts']['hosts_total']}")
