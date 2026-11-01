"""Milliseconds per manual save over time, from the crawler's heartbeats.

Usage: diag3_save.py <run dir> [<steps>]
Splits the heartbeats that carry manual_saves into about <steps> (12) equal spans and prints,
per span, its end time, the saves in it and the mean ms per save.
"""
import json
import sys
from itertools import pairwise
from pathlib import Path

run = Path(sys.argv[1])
steps = int(sys.argv[2]) if len(sys.argv) > 2 else 12
rows = [json.loads(line) for line in (run / "crawler.log").read_text().splitlines()
        if '"spider.heartbeat"' in line and "manual_saves" in line]
step = max(1, len(rows) // steps)
for a, b in pairwise(rows[::step]):
    saves = b["manual_saves"] - a["manual_saves"]
    ms = (b["manual_save_ms"] - a["manual_save_ms"]) / saves if saves else None
    print(f"{b['ts'][11:16]}Z saves {saves:5d} ms/save {'-' if ms is None else f'{ms:.2f}'}")
