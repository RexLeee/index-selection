"""Cost of one manual result save, written as PR #84's ManualBulkCrawlBatch.save does.

Usage: fsync_bench.py <empty dir> <saves> [full]
One save: the page (40 KB) with fsync, the item JSON as .tmp + fsync + rename, then
progress.json the same way. With `full`, each fsync is F_FULLFSYNC (macOS), which flushes
the drive cache as Linux fsync does on most disks. The files are removed at the end.
"""
import fcntl
import json
import os
import statistics
import sys
import time
from pathlib import Path

root, saves = Path(sys.argv[1]), int(sys.argv[2])
full = len(sys.argv) > 3 and sys.argv[3] == "full"
body = os.urandom(40 * 1024)


def sync(handle):
    if full:
        fcntl.fcntl(handle.fileno(), fcntl.F_FULLFSYNC)
    else:
        os.fsync(handle.fileno())


def write_file(path, content):
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        sync(handle)


def write_json(path, value):
    temporary = path.with_suffix(".json.tmp")
    write_file(temporary, json.dumps(value).encode())
    os.replace(temporary, path)


times = []
for i in range(saves):
    started = time.perf_counter()
    write_file(root / f"{i:05d}.html", body)
    write_json(root / f"{i:05d}.json", {"url": f"https://example.com/{i}", "status_code": 200})
    write_json(root / "progress.json", {"completed_urls": i + 1})
    times.append((time.perf_counter() - started) * 1000)
for path in root.iterdir():
    path.unlink()
times.sort()
print(f"saves {saves} mode {'F_FULLFSYNC' if full else 'fsync'}: "
      f"p50 {statistics.median(times):.2f} ms, p90 {times[int(saves * 0.9)]:.2f} ms, "
      f"p99 {times[int(saves * 0.99)]:.2f} ms, mean {statistics.mean(times):.2f} ms")
