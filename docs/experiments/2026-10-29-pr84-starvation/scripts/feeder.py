"""Plays the offerer: keeps ~180 domain files of <= 100 URLs in one crawler's queue.

Usage: feeder.py <queue dir> <port> <normal hosts> <target files>
Normal hosts are n00000.normal-sim.com ...; the manual hosts are the first of them, so
the two sources share those hosts' downloader slots, as in production.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

queue, port = Path(sys.argv[1]), int(sys.argv[2])
hosts, target = int(sys.argv[3]), int(sys.argv[4])
queue.mkdir(parents=True, exist_ok=True)
rng = random.Random(11)
seq = 0
while True:
    on_disk = {int(p.stem.split("_")[1]) for p in queue.glob("domain_*.json")}
    for _ in range(max(0, target - len(on_disk))):
        domain_id = rng.randrange(1, hosts + 1)
        if domain_id in on_disk:
            continue
        host = f"n{domain_id - 1:05d}.normal-sim.com"
        seq += 1
        urls = [f"http://{host}:{port}/n/{seq}/{i}" for i in range(100)]
        tmp = queue / f".domain_{domain_id}.json.tmp"
        tmp.write_text(json.dumps({"generated_at": time.time(), "domain_id": domain_id, "urls": urls}))
        os.replace(tmp, queue / f"domain_{domain_id}.json")
        on_disk.add(domain_id)
    time.sleep(1)
