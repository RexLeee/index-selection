"""Round-3 statistics and the C1-C4 checks of the 2026-10-30 plan.

Usage: analyze3.py [--ref <F0 run dir>] [--from M] [--minutes N] <run dir> [<run dir> ...]
All rates come from the mock web's access log, so they are send times, not slot-queue
times. Window: minutes M to M + N after submit + 60 s (default: all of `after`); the
reference run uses the same offsets. Recrawl hosts are r*.recrawl-sim.com; the hot ones
also get normal URLs. Jobs count only when created inside the window.
"""
import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

LIMIT_60S = 67  # 1.2 x the 56 requests per (hostname, worker) minute on Host2, 2026-10-30
EXTRA_QPS = 0.22  # 1.1 x isel's 0.2 qps per hostname


def quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def load(run: Path):
    t0, ts, te = (int((run / n).read_text()) for n in ("t0", "t_submit", "t_end"))
    hits = []
    for line in (run / "access.log").read_text().splitlines():
        t, host, kind, status, path = line.split(maxsplit=4)
        hits.append((float(t), host, kind, int(status), path))
    beats, latency = [], []
    for line in (run / "crawler.log").read_text().splitlines():
        if '"spider.heartbeat"' in line:
            beats.append(json.loads(line))
        elif '"request.end"' in line:
            latency.append(json.loads(line).get("download_latency_ms"))
    return t0, ts, te, hits, beats, [x for x in latency if x is not None]


def binned(hits, kinds, a, b, width):
    bins = Counter(int(t - a) // width for t, _, k, _, _ in hits if k in kinds and a <= t < b)
    return [bins[i] / width for i in range(int(b - a) // width)]


def host_minutes(hits, a, b, kinds="nmr"):
    return Counter((h, int(t - a) // 60) for t, h, k, _, _ in hits if k in kinds and a <= t < b)


def host_rates(hits, a, b, kinds, width=600):
    """Per host, its highest rate over aligned `width`-second windows."""
    counts = Counter((h, int(t - a) // width) for t, h, k, _, _ in hits if k in kinds and a <= t < b)
    best = defaultdict(float)
    for (h, _), c in counts.items():
        best[h] = max(best[h], c / width)
    return best


def recrawl_waits(run: Path, hits, te):
    """Per host, the longest time it had submitted recrawl docs and none was sent."""
    first = {}
    for t, _, k, _, path in hits:
        if k == "m" and "/moved/" not in path and t < te:
            first.setdefault(path, t)
    by_host = defaultdict(list)
    for manifest in (run / "mb" / "pending").glob("*.json"):
        r = json.loads(manifest.read_text())
        created = datetime.fromisoformat(r["created_at"]).timestamp()
        if created >= te:
            continue
        for url in r["urls"]:
            path = "/" + url.split("/", 3)[3]
            by_host[url.split("/")[2].split(":")[0]].append((first.get(path), created))
    worst = 0.0
    for docs in by_host.values():
        last = None
        # Sent docs in send order, then the unsent ones, which wait until the end.
        for f, s in sorted(docs, key=lambda d: (d[0] is None, d[0] or 0)):
            start = max(s, last) if last is not None else s
            worst = max(worst, (f if f is not None else te) - start)
            if f is not None:
                last = f
    return worst


def job_ages(run: Path, ta, te):
    """Lifetimes of the jobs created in [ta, te): finished by te, or still open at te."""
    done, open_ages = [], []
    for manifest in (run / "mb" / "pending").glob("*.json"):
        created = datetime.fromisoformat(json.loads(manifest.read_text())["created_at"]).timestamp()
        if not ta <= created < te:
            continue
        progress = run / "mb" / "results" / manifest.stem / "crawler_00" / "progress.json"
        completed = json.loads(progress.read_text()).get("completed_at") if progress.exists() else None
        finished = datetime.fromisoformat(completed).timestamp() if completed else None
        if finished is not None and finished < te:
            done.append(finished - created)
        else:
            open_ages.append(te - created)
    return done, open_ages


def save_ms(rows):
    """Mean milliseconds per manual save between the first and last heartbeat that has them."""
    rows = [r for r in rows if "manual_saves" in r]
    if len(rows) < 2 or rows[-1]["manual_saves"] == rows[0]["manual_saves"]:
        return None
    return ((rows[-1]["manual_save_ms"] - rows[0]["manual_save_ms"])
            / (rows[-1]["manual_saves"] - rows[0]["manual_saves"]))


def summarize(run: Path, start_min: float = 0, minutes: float | None = None) -> dict:
    _, ts, te, hits, beats, latency = load(run)
    a = ts + 60 + start_min * 60
    b = min(te, a + minutes * 60) if minutes else te
    n10, m10 = binned(hits, "n", a, b, 10), binned(hits, "m", a, b, 10)
    n60 = binned(hits, "n", a, b, 60)
    m300 = binned(hits, "m", a, b, 300)
    # 10 min windows; a run shorter than that is one window.
    width = min(600, int(b - a))
    shares = [m / (n + m) if n + m else 0.0
              for n, m in zip(binned(hits, "n", a, b, width), binned(hits, "m", a, b, width), strict=True)]
    minutes = host_minutes(hits, a, b)
    rows = [r for r in beats if a <= datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S,%f").timestamp() < b]
    # cpu.log lines: time, crawler %CPU, RSS KiB, cumulative CPU s (older runs lack the last two).
    samples = [x.split() for x in (run / "cpu.log").read_text().splitlines()]
    samples = [x for x in samples if x[1] != "NA" and a <= int(x[0]) < b]
    secs = [(int(x[0]), int(x[3])) for x in samples if len(x) > 3]
    if len(secs) > 1 and secs[-1][0] > secs[0][0]:
        cpu = [100 * (secs[-1][1] - secs[0][1]) / (secs[-1][0] - secs[0][0])]
    else:
        cpu = [float(x[1]) for x in samples]
    rss = [(int(x[0]), int(x[2]) / 1024) for x in samples if len(x) > 2]
    done, open_ages = job_ages(run, a, b)
    statuses = Counter(s for t, _, k, s, _ in hits if k in "nm" and a <= t < b)
    total = sum(statuses.values())
    return {
        "run": run.name, "a": a - ts, "b": b - ts, "minutes": (b - a) / 60,
        "n": statistics.mean(n10), "m": statistics.mean(m10) if m10 else 0.0,
        "n_min60": min(n60), "n60": n60, "m_zero_5min": sum(1 for x in m300 if x == 0) if any(m300) else None,
        "share_max": max(shares), "share_mean": statistics.mean(shares),
        "host60_max": max(minutes.values()), "host60_p99": quantile(list(minutes.values()), 0.99),
        "r_host60_max": max((c for (h, _), c in minutes.items() if h.startswith("r")), default=0),
        "normal_host": host_rates(hits, a, b, "n"), "total_host": host_rates(hits, a, b, "nm"),
        "hot_normal": {h: sum(1 for t, hh, k, _, _ in hits if hh == h and k == "n" and a <= t < b) / (b - a)
                       for h in {h for _, h, k, _, _ in hits if k == "n" and h.startswith("r")}},
        "active_domains": statistics.median(r.get("active_domains", 0) for r in rows) if rows else None,
        "backlog": statistics.median(r["engine_slot_inprogress"] - r["inflight"] for r in rows) if rows else None,
        "inflight": statistics.median(r["inflight"] for r in rows) if rows else None,
        "cpu": statistics.mean(cpu) if cpu else None,
        "rss": [statistics.mean(edge) if edge else None
                for edge in ([m for t, m in rss if t < a + 300], [m for t, m in rss if t >= b - 300])],
        "save_ms": save_ms(rows),
        "lat": [quantile(latency, q) for q in (0.5, 0.9, 0.99)],
        "jobs_done": len(done), "jobs_open": len(open_ages),
        "life": [quantile(done, q) for q in (0.5, 0.9)] + [max(done) if done else None],
        "oldest_open": max(open_ages) if open_ages else None,
        "wait_max": recrawl_waits(run, hits, b) if (run / "jobs.txt").exists() else None,
        "non200": {s: c / total for s, c in statuses.most_common() if s != 200} if total else {},
    }


def num(x, f="{:.0f}"):
    return "-" if x is None else f.format(x)


def checks(s, ref):
    """(name, passed, detail) for the plan's pass conditions; ref is the F0 summary."""
    out = []
    if ref:
        out.append(("C1 normal >= 80 % of F0", s["n"] >= 0.8 * ref["n"], f"{s['n']:.1f} vs {ref['n']:.1f}"))
        floor = 0.5 * statistics.median(ref["n60"])
        out.append(("C1 no 60 s below 50 % of F0 median", s["n_min60"] >= floor,
                    f"min {s['n_min60']:.1f} vs {floor:.1f}"))
        kept = [s["hot_normal"].get(h, 0) / u for h, u in ref["hot_normal"].items() if u > 0]
        if kept:
            out.append(("C1 each hot host keeps >= 40 % of its F0 normal rate", min(kept) >= 0.4,
                        f"lowest {min(kept):.0%}"))
        over = [h for h, r in s["total_host"].items()
                if r > ref["normal_host"].get(h, 0) + EXTRA_QPS and h.startswith("r")]
        out.append(("C2 10 min rate <= F0 normal + 0.22 req/s", not over, f"{len(over)} hosts over"))
        out.append(("C2 any host's 60 s max <= 1.2 x F0", s["host60_max"] <= 1.2 * ref["host60_max"],
                    f"{s['host60_max']} vs F0 {ref['host60_max']}"))
    if s["active_domains"] is not None:
        out.append(("C1 active_domains median >= 140", s["active_domains"] >= 140, num(s["active_domains"])))
    if s["m_zero_5min"] is not None:
        out.append(("C1 recrawl never 0 for 5 min", s["m_zero_5min"] == 0, f"{s['m_zero_5min']} windows"))
        out.append(("C1 recrawl host wait <= 600 s", s["wait_max"] <= 600, f"{s['wait_max']:.0f} s"))
        out.append(("C3 recrawl >= 4.0 req/s", s["m"] >= 4.0, f"{s['m']:.2f}"))
        p90 = s["life"][1]
        out.append(("C3 job lifetime p90 <= 3600 s", p90 is not None and p90 <= 3600, num(p90)))
        # Server-side share: robots.txt, retries and redirects of API requests add a little.
        out.append(("C4 recrawl share <= 20.5 % in every 10 min", s["share_max"] <= 0.205,
                    f"max {s['share_max']:.1%}"))
    out.append(("C2 recrawl hosts' 60 s max <= 67", s["r_host60_max"] <= LIMIT_60S, str(s["r_host60_max"])))
    return out


parser = argparse.ArgumentParser()
parser.add_argument("--ref")
parser.add_argument("--from", dest="start", type=float, default=0, help="minutes after submit + 60 s")
parser.add_argument("--minutes", type=float, help="window length; default: to the end")
parser.add_argument("runs", nargs="+")
args = parser.parse_args()
ref = summarize(Path(args.ref), args.start, args.minutes) if args.ref else None
runs = [summarize(Path(r), args.start, args.minutes) for r in args.runs]
print("| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains"
      " | inflight | backlog | cpu % | latency p50/p90/p99 ms |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for s in runs:
    lat = "/".join(num(x) for x in s["lat"])
    print(f"| {s['run']} | {s['minutes']:.0f} | {s['n']:.1f} | {s['m']:.2f} | {s['share_mean']:.1%} / "
          f"{s['share_max']:.1%} | {s['host60_max']} / {s['host60_p99']} | {num(s['active_domains'])} | "
          f"{num(s['inflight'])} | {num(s['backlog'])} | {num(s['cpu'])} | {lat} |")
print()
print("| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s"
      " | non-200 share | RSS MB first / last 5 min | ms per manual save |")
print("|---|---|---|---|---|---|---|---|")
for s in runs:
    life = " / ".join(num(x) for x in s["life"])
    non200 = ", ".join(f"{k}: {v:.1%}" for k, v in list(s["non200"].items())[:4]) or "-"
    rss = " / ".join(num(x) for x in s["rss"])
    print(f"| {s['run']} | {s['jobs_done']} / {s['jobs_open']} | {life} | {num(s['oldest_open'])} | "
          f"{num(s['wait_max'])} | {non200} | {rss} | {num(s['save_ms'], '{:.2f}')} |")
print()
for s in runs:
    print(f"### {s['run']}")
    for name, passed, detail in checks(s, ref):
        print(f"- {'PASS' if passed else 'FAIL'} {name}: {detail}")
    hot = sorted(s["hot_normal"].items())[:5]
    if hot:
        print("- hot hosts, normal req/s: " + ", ".join(f"{h.split('.')[0]} {v:.2f}" for h, v in hot)
              + (f"; total {math.fsum(s['total_host'].get(h, 0) for h, _ in hot) / len(hot):.2f} mean peak 10 min"
                 if hot else ""))
    print()
