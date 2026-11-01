"""Submits isel-shaped jobs through PR #84's own ManualBulkCrawlService, all to crawler 0.

Usage: submit.py <wc root> <pending dir> <result dir> <port> <jobs> <hosts per job>
                 <docs per host> [<keep s>]
One crawler's view of isel: 48 jobs of ~29 hosts over 16 crawlers is ~1.8 hosts per job
per crawler, 120 docs each. Host i of the list is n{i}.normal-sim.com, shared with normal.
With <keep s> > 0 it then plays isel's planner for that long: whenever a job completes,
it submits a new one on fresh hosts, so `jobs` stay open.
"""
import json
import sys
import time
from pathlib import Path

wc, pending, results, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
jobs, hosts_per_job, docs = int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
keep_s = float(sys.argv[8]) if len(sys.argv) > 8 else 0.0
sys.path.insert(0, wc)
from containers.api.service import ManualBulkCrawlService

service = ManualBulkCrawlService({"pending_dir": pending, "result_dir": results},
                                 route_urls=lambda urls: [0] * len(urls))
host = 0
submitted = []


def submit(j: int) -> None:
    global host
    urls = []
    for _ in range(hosts_per_job):
        urls += [f"http://n{host:05d}.normal-sim.com:{port}/m/{j}/{i}" for i in range(docs)]
        host += 1
    submitted.append(service.submit(urls, ttl_days=3))
    print(submitted[-1], flush=True)


def is_open(job_id: str) -> bool:
    progress = Path(results, job_id, "crawler_00", "progress.json")
    try:
        return not json.loads(progress.read_text()).get("completed_at")
    except (FileNotFoundError, json.JSONDecodeError):
        return True


for j in range(jobs):
    submit(j)
deadline = time.monotonic() + keep_s
while time.monotonic() < deadline:
    for _ in range(jobs - sum(map(is_open, submitted))):
        submit(len(submitted))
    time.sleep(2)
