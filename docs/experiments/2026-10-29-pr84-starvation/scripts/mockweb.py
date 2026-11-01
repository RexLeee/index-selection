"""Mock web: one server, many hosts. Each host has a fixed median latency.

Usage: mockweb.py <port> <access log path> <median latency s> <sigma>
Logs one line per request: arrival time, host, kind (n = normal, m = manual, r = robots).
Env: MOCK_LINKS (80) and MOCK_TEXT_REPEAT (1500) set the page's links and text, and so the
crawler's parse cost. MOCK_DEAD_EVERY=N (0 = none) makes 1 host in N answer after 20 s,
past the crawler's 15 s DOWNLOAD_TIMEOUT.
"""
import asyncio
import hashlib
import math
import os
import random
import sys
import time

from aiohttp import web

port, log_path = int(sys.argv[1]), sys.argv[2]
median_s, sigma = float(sys.argv[3]), float(sys.argv[4])
links_n = int(os.getenv("MOCK_LINKS", "80"))
text_repeat = int(os.getenv("MOCK_TEXT_REPEAT", "1500"))
dead_every = int(os.getenv("MOCK_DEAD_EVERY", "0"))
log = open(log_path, "a", buffering=1)  # noqa: SIM115  (open for the server's life)
rng = random.Random(7)


def host_seed(host: str) -> int:
    return int(hashlib.md5(host.encode()).hexdigest()[:8], 16)


def host_median(host: str) -> float:
    if dead_every and host_seed(host) % dead_every == 0:
        return 20.0
    z = random.Random(host_seed(host)).gauss(0, 1)
    return min(12.0, max(0.1, median_s * math.exp(sigma * z)))


def page(host: str, kind: str) -> bytes:
    links = "".join(f'<li><a href="http://{host}:{port}/{kind}/x/{i}">link {i} text</a></li>'
                    for i in range(links_n))
    text = "<p>" + ("lorem ipsum dolor sit amet " * text_repeat) + "</p>"
    return f"<html><head><title>{host}</title></head><body><ul>{links}</ul>{text}</body></html>".encode()


BODY = {}


async def handle(request: web.Request) -> web.Response:
    host = (request.host or "").split(":")[0]
    path = request.path
    kind = "r" if path == "/robots.txt" else ("m" if path.startswith("/m/") else "n")
    log.write(f"{time.time():.3f} {host} {kind}\n")
    median = host_median(host)
    await asyncio.sleep(median if median > 14.0 else min(14.0, median * math.exp(0.3 * rng.gauss(0, 1))))
    if kind == "r":
        return web.Response(text="User-agent: *\nAllow: /\n", content_type="text/plain")
    key = (host, kind)
    if key not in BODY:
        BODY[key] = page(host, kind)
    return web.Response(body=BODY[key], content_type="text/html", charset="utf-8")


app = web.Application()
app.router.add_route("GET", "/{tail:.*}", handle)
web.run_app(app, host="127.0.0.1", port=port, access_log=None, backlog=4096)
