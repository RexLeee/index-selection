"""Shape of one crawler's normal queue: domain files, URLs per file, hosts per file.

Usage (inside the crawler container, read-only): qshape.py /data/ipc/url_queue/crawler_NN
Reads at most 400 files; a file the offerer replaces mid-read is skipped.
"""
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

queue = Path(sys.argv[1])
files = sorted(os.listdir(queue))
sizes, hosts_per_file, keys = [], [], Counter()
for name in files[:400]:
    try:
        data = json.loads((queue / name).read_text())
    except (OSError, json.JSONDecodeError):
        continue
    keys.update(data.keys())
    urls = data.get("urls", [])
    sizes.append(len(urls))
    hosts_per_file.append(len({urlsplit(u).hostname for u in urls}))
sizes.sort()
print("files_in_dir", len(files), "sampled", len(sizes), "keys", dict(keys))
if sizes:
    print(f"urls/file mean {statistics.mean(sizes):.1f} p50 {sizes[len(sizes) // 2]} "
          f"p90 {sizes[int(len(sizes) * 0.9)]} max {sizes[-1]}")
    print(f"hosts/file mean {statistics.mean(hosts_per_file):.2f} max {max(hosts_per_file)}")
