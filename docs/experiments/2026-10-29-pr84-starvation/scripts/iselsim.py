"""Plays isel's recrawl planner against one crawler, through PR #84's ManualBulkCrawlService.

Usage: iselsim.py <wc root> <pending dir> <result dir> <port> <sizes file> <run s>
The sizes file holds one URL count per line, largest first: the hostnames of the capped
64M list that route to one production process. Host i is r{i:06d}.recrawl-sim.com.
Each plan cycle (as recrawl-plan does, on this crawler's part of each job only):
  - opens up to PLAN_MAX_JOBS new jobs while fewer than MAX_JOBS_INFLIGHT are open;
  - adds ready hosts in list order, HOST_DOCS_PER_JOB docs each (or all a host has left),
    until the job holds at least JOB_URLS docs. isel cuts only the last host of a
    3,000-URL job; cutting here would cut every other host of a 188-doc slice;
  - a host is ready when it is in no job and MIN_GAP_S seconds per doc have passed
    since its last job took them (recrawl.py: batch_id IS NULL and next_at <= now).
A job leaves the open set when a status poll sees it completed; its hosts are freed at
the next archive pass, as recrawl-archive frees them when it applies the job.
isel orders ready hosts by best rank; list order (largest first) favours big hosts, so
their measured rate is an upper bound.
Env defaults follow config/isel.toml divided by the 16 crawlers: JOB_URLS 188,
HOST_DOCS_PER_JOB 120, MAX_JOBS_INFLIGHT 48, PLAN_MAX_JOBS 8, PLAN_INTERVAL_S 30,
MIN_GAP_S 5, STATUS_POLL_S 60, ARCHIVE_INTERVAL_S 60. Prints one line per job: submit
time, id, docs, hosts.
"""
import json
import os
import sys
import time
from pathlib import Path

wc, pending, results, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
sizes = [int(x) for x in Path(sys.argv[5]).read_text().split()]
run_s = float(sys.argv[6])
env = {k: float(os.getenv(k, d)) for k, d in (
    ("JOB_URLS", 188), ("HOST_DOCS_PER_JOB", 120), ("MAX_JOBS_INFLIGHT", 48), ("PLAN_MAX_JOBS", 8),
    ("PLAN_INTERVAL_S", 30), ("MIN_GAP_S", 5), ("STATUS_POLL_S", 60), ("ARCHIVE_INTERVAL_S", 60))}
sys.path.insert(0, wc)
from containers.api.service import ManualBulkCrawlService

service = ManualBulkCrawlService({"pending_dir": pending, "result_dir": results},
                                 route_urls=lambda urls: [0] * len(urls))
sent = [0] * len(sizes)
next_at = [0.0] * len(sizes)
in_job = [False] * len(sizes)
open_jobs: dict[str, list[int]] = {}  # job id -> its hosts
to_apply: list[list[int]] = []  # hosts of completed jobs the archive has not applied yet
last_poll = 0.0
next_archive = time.time() + env["ARCHIVE_INTERVAL_S"]
cursor = 0  # hosts before it are drained


def completed(job_id: str) -> bool:
    progress = Path(results, job_id, "crawler_00", "progress.json")
    try:
        return bool(json.loads(progress.read_text()).get("completed_at"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def plan_job(now: float) -> tuple[list[str], list[int]]:
    global cursor
    urls, hosts = [], []
    i = cursor
    while i < len(sizes) and len(urls) < env["JOB_URLS"]:
        left = sizes[i] - sent[i]
        if left <= 0:
            if i == cursor:
                cursor += 1
        elif not in_job[i] and next_at[i] <= now:
            take = int(min(left, env["HOST_DOCS_PER_JOB"]))
            urls += [f"http://r{i:06d}.recrawl-sim.com:{port}/m/{i}/{sent[i] + d}" for d in range(take)]
            sent[i] += take
            next_at[i] = now + take * env["MIN_GAP_S"]
            in_job[i] = True
            hosts.append(i)
        i += 1
    return urls, hosts


deadline = time.monotonic() + run_s
while time.monotonic() < deadline:
    now = time.time()
    if now - last_poll >= env["STATUS_POLL_S"]:
        for job_id in [j for j in open_jobs if completed(j)]:
            to_apply.append(open_jobs.pop(job_id))
        last_poll = now
    if now >= next_archive:
        for i in (i for hosts in to_apply for i in hosts):
            in_job[i] = False
        to_apply.clear()
        next_archive += env["ARCHIVE_INTERVAL_S"]
    room = int(min(env["PLAN_MAX_JOBS"], env["MAX_JOBS_INFLIGHT"] - len(open_jobs)))
    for _ in range(room):
        urls, hosts = plan_job(now)
        if not urls:
            break
        job_id = service.submit(urls, ttl_days=3)
        open_jobs[job_id] = hosts
        print(f"{now:.3f} {job_id} {len(urls)} {len(hosts)}", flush=True)
    time.sleep(env["PLAN_INTERVAL_S"])
