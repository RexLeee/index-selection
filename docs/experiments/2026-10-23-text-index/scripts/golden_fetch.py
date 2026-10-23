"""Fetches the pages of one golden batch's URLs into TEXT_SCHEMA Parquet.

Usage: golden_fetch.py GOLDEN.parquet BATCH OUT_DIR [--limit N] [--seed S] [--workers W]

Politeness follows the recrawl rule: at most 0.2 requests per second per host
(robots.txt included), and robots.txt is obeyed for the `*` group. Each URL is
fetched once, without retries. `url` and `doc_id` come from the golden
`canonical_url`, so a hit matches the golden URL the way `text-index-eval` does.
A 2xx HTML page becomes one row, as in the recrawl's `_record`; every URL also gets a
line in OUT_DIR/log.jsonl. Rows and their log lines are written together, so a rerun
skips the URLs already logged and fetches the rest.
"""
import argparse
import hashlib
import heapq
import json
import random
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from isel.extract import extract
from isel.io import write_parquet_atomic
from isel.recrawl import TEXT_SCHEMA
from isel.urlnorm import md5_number_lower

USER_AGENT = "isel-golden-eval/0.1 (NTU CSIE5376 course research, one fetch per URL)"
HOST_GAP_S = 5.0          # 0.2 qps per host
TIMEOUT_S = 20.0
MAX_BYTES = 10 * 2**20
MAX_BODY_CHARS = 200_000  # recrawl.max_body_chars
FLUSH_ROWS = 200


def get(url: str) -> tuple[int | None, str, str | None, bytes, str | None]:
    """(status, final URL, content type, body, error) of one GET."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "text/html,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
            return (resp.status, resp.geturl(), resp.headers.get("Content-Type"),
                    resp.read(MAX_BYTES), None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.geturl() or url, exc.headers.get("Content-Type"), b"", None
    except Exception as exc:  # network, TLS and decoding errors are outcomes here
        return None, url, None, b"", type(exc).__name__


class Hosts:
    """Hands out one URL at a time per host, HOST_GAP_S after the host's last request."""

    def __init__(self, urls: list[dict]) -> None:
        self.queue: dict[str, list[dict]] = {}
        for u in urls:
            self.queue.setdefault(u["host"], []).append(u)
        self.ready = [(0.0, h) for h in self.queue]
        heapq.heapify(self.ready)
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.cv = threading.Condition()

    def take(self) -> tuple[str, float] | None:
        with self.cv:
            while True:
                if not self.ready:
                    if not any(self.queue.values()):
                        return None
                    self.cv.wait(1.0)   # every host is busy
                    continue
                at, host = heapq.heappop(self.ready)
                return host, at

    def release(self, host: str, next_at: float) -> None:
        with self.cv:
            if self.queue[host]:
                heapq.heappush(self.ready, (next_at, host))
            self.cv.notify_all()


def allowed(hosts: Hosts, host: str, url: str) -> tuple[bool, bool]:
    """(allowed, a request was made); the first call per host fetches robots.txt."""
    if host not in hosts.robots:
        parts = urlsplit(url)
        status, _, _, body, _ = get(f"{parts.scheme}://{parts.netloc}/robots.txt")
        parser = urllib.robotparser.RobotFileParser()
        if status in (401, 403):
            parser.disallow_all = True
        elif status is not None and 200 <= status < 300:
            parser.parse(body.decode("utf-8", "replace").splitlines())
        else:
            parser.allow_all = True
        hosts.robots[host] = parser
        return parser.can_fetch(USER_AGENT, url), True
    return hosts.robots[host].can_fetch(USER_AGENT, url), False


def fetch_one(u: dict) -> tuple[dict, dict | None]:
    started = time.monotonic()
    fetched_at = datetime.now(UTC)
    status, final_url, ctype, body, error = get(u["url"])
    log = {"url": u["url"], "host": u["host"], "doc_id": str(u["doc_id"]), "status": status,
           "final_url": final_url, "error": error, "bytes": len(body),
           "content_type": ctype, "fetched_at": fetched_at.isoformat()}
    row = None
    if status is None:
        log["outcome"] = "error"
    elif not 200 <= status < 300:
        log["outcome"] = f"http_{status}"
    elif ctype and "html" not in ctype.lower():
        log["outcome"] = "non_html"
    else:
        charset = None
        if ctype and "charset=" in ctype.lower():
            charset = ctype.lower().split("charset=")[1].split(";")[0].strip(" \"'")
        try:
            doc = extract(body, charset, MAX_BODY_CHARS)
        except Exception:  # hostile HTML must not stop the run
            log["outcome"] = "extract_error"
        else:
            log.update(outcome="fetched", text_len=len(doc.body), lang=doc.lang,
                       has_title=doc.title is not None)
            row = {"doc_id": u["doc_id"], "batch_id": u["batch_id"], "url": u["url"],
                   "final_url": final_url, "fetched_at": fetched_at, "http_status": status,
                   "lang": doc.lang, "title": doc.title, "description": doc.description,
                   "body": doc.body, "text_len": len(doc.body),
                   "payload_sha1": hashlib.sha1(body).hexdigest()}
    log["ms"] = round((time.monotonic() - started) * 1000)
    return log, row


class Sink:
    """Buffers rows and log lines; writes a Parquet part and its log lines together."""

    def __init__(self, out: Path) -> None:
        self.dir = out / "text" / f"fetch_date={datetime.now(UTC):%Y%m%d}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = out / "log.jsonl"
        self.part = len(list((out / "text").glob("**/*.parquet")))
        self.rows: list[dict] = []
        self.lines: list[str] = []
        self.lock = threading.Lock()

    def add(self, log: dict, row: dict | None) -> None:
        with self.lock:
            self.lines.append(json.dumps(log, ensure_ascii=False))
            if row is not None:
                self.rows.append(row)
            if len(self.rows) >= FLUSH_ROWS or len(self.lines) >= 5 * FLUSH_ROWS:
                self._flush()

    def close(self) -> None:
        with self.lock:
            self._flush()

    def _flush(self) -> None:
        if self.rows:
            write_parquet_atomic(pa.Table.from_pylist(self.rows, schema=TEXT_SCHEMA),
                                 self.dir / f"golden-{self.part:04d}.parquet")
            self.part += 1
        with self.log.open("a", encoding="utf-8") as f:
            f.writelines(line + "\n" for line in self.lines)
        self.rows, self.lines = [], []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("golden", type=Path)
    ap.add_argument("batch", type=int)
    ap.add_argument("out", type=Path)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--seed", type=int, default=20)
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    golden = pq.read_table(args.golden, columns=["batch_id", "canonical_url"])
    golden = golden.filter(pc.field("batch_id") == args.batch)
    urls = sorted(set(golden.column("canonical_url").to_pylist()))
    if args.limit:
        urls = sorted(random.Random(args.seed).sample(urls, args.limit))
    args.out.mkdir(parents=True, exist_ok=True)
    done = set()
    if (args.out / "log.jsonl").exists():
        done = {json.loads(line)["url"] for line in (args.out / "log.jsonl").open()}
    todo = [{"url": u, "host": (urlsplit(u).hostname or "").lower(),
             "doc_id": md5_number_lower(u), "batch_id": args.batch}
            for u in urls if u not in done]
    print(json.dumps({"selected": len(urls), "done": len(done), "todo": len(todo)}), flush=True)

    hosts, sink = Hosts(todo), Sink(args.out)
    counts: dict[str, int] = {}

    def step(host: str) -> tuple[float, dict | None, dict | None]:
        """Serves one turn of `host`: its robots.txt, or one URL. Returns (next_at, log, row)."""
        u = hosts.queue[host].pop()
        try:
            ok, requested = allowed(hosts, host, u["url"])
            if requested and ok:
                hosts.queue[host].append(u)  # the page waits for the host's next turn
                return time.monotonic() + HOST_GAP_S, None, None
            if not ok:
                log = {"url": u["url"], "host": host, "doc_id": str(u["doc_id"]),
                       "outcome": "robots"}
                return time.monotonic() + (HOST_GAP_S if requested else 0.0), log, None
            log, row = fetch_one(u)
        except Exception as exc:  # a bug must cost one URL, not stall its host
            log, row = {"url": u["url"], "host": host, "doc_id": str(u["doc_id"]),
                        "outcome": "script_error", "error": repr(exc)}, None
        return time.monotonic() + HOST_GAP_S, log, row

    def worker() -> None:
        while (taken := hosts.take()) is not None:
            host, at = taken
            time.sleep(max(0.0, at - time.monotonic()))
            next_at, log, row = time.monotonic() + HOST_GAP_S, None, None
            try:
                next_at, log, row = step(host)
            finally:
                hosts.release(host, next_at)
            if log is None:
                continue
            sink.add(log, row)
            with hosts.cv:
                counts[log["outcome"]] = counts.get(log["outcome"], 0) + 1
                n = sum(counts.values())
            if n % 500 == 0:
                print(json.dumps({"done": n, **counts}), flush=True)

    # The heap starts at time 0; shift it to now so the gaps are real seconds.
    now = time.monotonic()
    hosts.ready = [(now, h) for _, h in hosts.ready]
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(worker) for _ in range(args.workers)]
    sink.close()
    for f in futures:
        f.result()
    print(json.dumps({"finished": sum(counts.values()), **counts}), flush=True)


if __name__ == "__main__":
    main()
