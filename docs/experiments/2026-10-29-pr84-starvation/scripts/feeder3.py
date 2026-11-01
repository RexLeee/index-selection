"""Round-3 offerer: ~180 domain files of <= 100 URLs, plus hot hosts the recrawl shares.

Usage: feeder3.py <queue dir> <port> <normal hosts> <target files> <hot hosts> <hot period s>
Normal hosts are n00000.normal-sim.com ...; hot host i is r{i:06d}.recrawl-sim.com, the
i-th host of the isel emulator. A hot host gets a new 100-URL file at most every
<hot period s>, so its normal rate is about 100 / period (production: 0.17-0.31 req/s).
"""
import json
import os
import random
import sys
import time
from pathlib import Path

HOT_ID = 1_000_000

queue, port = Path(sys.argv[1]), int(sys.argv[2])
hosts, target = int(sys.argv[3]), int(sys.argv[4])
hot, hot_period = int(sys.argv[5]), float(sys.argv[6])
queue.mkdir(parents=True, exist_ok=True)
rng = random.Random(11)
seq = 0
hot_written = [0.0] * hot


def write(domain_id: int, host: str) -> None:
    global seq
    seq += 1
    urls = [f"http://{host}:{port}/n/{seq}/{i}" for i in range(100)]
    tmp = queue / f".domain_{domain_id}.json.tmp"
    tmp.write_text(json.dumps({"generated_at": time.time(), "domain_id": domain_id, "urls": urls}))
    os.replace(tmp, queue / f"domain_{domain_id}.json")


while True:
    on_disk = {int(p.stem.split("_")[1]) for p in queue.glob("domain_*.json")}
    now = time.time()
    for i in range(hot):
        if HOT_ID + i not in on_disk and now - hot_written[i] >= hot_period:
            write(HOT_ID + i, f"r{i:06d}.recrawl-sim.com")
            hot_written[i] = now
    normal_on_disk = sum(1 for d in on_disk if d < HOT_ID)
    for _ in range(max(0, target - normal_on_disk) if hosts else 0):
        domain_id = rng.randrange(1, hosts + 1)
        if domain_id in on_disk:
            continue
        write(domain_id, f"n{domain_id - 1:05d}.normal-sim.com")
        on_disk.add(domain_id)
    time.sleep(1)
