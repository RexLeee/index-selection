"""Round-3 mock web: one server, many hosts, production-like failures.

Usage: mockweb3.py <port> <access log path> <median latency s> <host sigma>
Logs one line per request: arrival time, host, kind (n = normal, m = manual, r = robots),
status, path. Each host has a fixed lognormal median latency; each request varies around
it by MOCK_REQ_SIGMA (0.3). Pages: MOCK_LINKS (80) links, MOCK_TEXT_REPEAT (1500) text.
Failure env, all 0 by default:
  per host, 1 in N:  MOCK_DEAD_EVERY (answers after 20 s, past the 15 s timeout),
                     MOCK_ROBOTS_DENY_EVERY (robots.txt disallows all), MOCK_403_EVERY,
                     MOCK_429_EVERY (every page answers 429)
  per request:       MOCK_REDIRECT_FRAC (301 to another path on the host),
                     MOCK_404_FRAC, MOCK_429_FRAC
MOCK_DEPART_LOG (unset = off): one more line per answered request, "arrived departed host
kind status", for per-host concurrency at the server.
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
req_sigma = float(os.getenv("MOCK_REQ_SIGMA", "0.3"))
links_n = int(os.getenv("MOCK_LINKS", "80"))
text_repeat = int(os.getenv("MOCK_TEXT_REPEAT", "1500"))
every = {name: int(os.getenv(f"MOCK_{name}_EVERY", "0")) for name in ("DEAD", "ROBOTS_DENY", "403", "429")}
frac = {name: float(os.getenv(f"MOCK_{name}_FRAC", "0")) for name in ("REDIRECT", "404", "429")}
log = open(log_path, "a", buffering=1)  # noqa: SIM115  (open for the server's life)
rng = random.Random(7)
depart_path = os.getenv("MOCK_DEPART_LOG")
depart = open(depart_path, "a", buffering=1) if depart_path else None  # noqa: SIM115


def host_seed(host: str, salt: str = "") -> int:
    return int(hashlib.md5((salt + host).encode()).hexdigest()[:8], 16)


def host_has(host: str, name: str) -> bool:
    return every[name] > 0 and host_seed(host, name) % every[name] == 0


def host_median(host: str) -> float:
    if host_has(host, "DEAD"):
        return 20.0
    z = random.Random(host_seed(host)).gauss(0, 1)
    return min(12.0, max(0.1, median_s * math.exp(sigma * z)))


def page(host: str, kind: str) -> bytes:
    links = "".join(f'<li><a href="http://{host}:{port}/{kind}/x/{i}">link {i} text</a></li>'
                    for i in range(links_n))
    text = "<p>" + ("lorem ipsum dolor sit amet " * text_repeat) + "</p>"
    return f"<html><head><title>{host}</title></head><body><ul>{links}</ul>{text}</body></html>".encode()


BODY = {}


def answer(host: str, kind: str, path: str) -> web.Response:
    if kind == "r":
        rule = "Disallow: /" if host_has(host, "ROBOTS_DENY") else "Allow: /"
        return web.Response(text=f"User-agent: *\n{rule}\n", content_type="text/plain")
    if host_has(host, "403"):
        return web.Response(status=403, text="forbidden")
    if host_has(host, "429"):
        return web.Response(status=429, text="slow down")
    draw = rng.random()
    if draw < frac["429"]:
        return web.Response(status=429, text="slow down")
    draw -= frac["429"]
    if draw < frac["404"]:
        return web.Response(status=404, text="not found")
    draw -= frac["404"]
    if draw < frac["REDIRECT"] and "/moved/" not in path:
        prefix, _, rest = path.lstrip("/").partition("/")
        raise web.HTTPMovedPermanently(location=f"http://{host}:{port}/{prefix}/moved/{rest}")
    key = (host, kind)
    if key not in BODY:
        BODY[key] = page(host, kind)
    return web.Response(body=BODY[key], content_type="text/html", charset="utf-8")


async def handle(request: web.Request) -> web.Response:
    host = (request.host or "").split(":")[0]
    path = request.path
    kind = "r" if path == "/robots.txt" else ("m" if path.startswith("/m/") else "n")
    arrived = time.time()
    median = host_median(host)
    await asyncio.sleep(median if median > 14.0
                        else min(14.0, median * math.exp(req_sigma * rng.gauss(0, 1))))
    try:
        response = answer(host, kind, path)
    except web.HTTPException as redirect:
        log.write(f"{arrived:.3f} {host} {kind} {redirect.status} {path}\n")
        if depart:
            depart.write(f"{arrived:.3f} {time.time():.3f} {host} {kind} {redirect.status}\n")
        raise
    log.write(f"{arrived:.3f} {host} {kind} {response.status} {path}\n")
    if depart:
        depart.write(f"{arrived:.3f} {time.time():.3f} {host} {kind} {response.status}\n")
    return response


app = web.Application()
app.router.add_route("GET", "/{tail:.*}", handle)
web.run_app(app, host="127.0.0.1", port=port, access_log=None, backlog=4096)
