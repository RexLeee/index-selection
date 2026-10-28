"""128M-document projection of the OpenSearch index and the S check of docs/TEXT_INDEX.md.

Usage: c50k_project.py SIZES.json OUT.json
Host2 numbers come from read-only SSH on 2026-10-23 06:06 UTC: `df -B1 /data` and
/data/ipc/logs/prune_crawl_result.log from 10-22 15:00 to 10-23 14:00 (+08:00).
"""
import json
import sys
from pathlib import Path

N = 128_000_000
WET = 2867                        # text bytes per page from Common Crawl WET (RECRAWL.md)
TIB = 1024 ** 4
FS = 7_619_779_362_816            # /data size in bytes (df -B1)
FREE = 1.2 * TIB                  # lowest pre-prune fs_avail in 24 h; df -h rounds up
TEXT = 0.4e12                     # text Parquet, RECRAWL.md "~0.35-0.4 TB"
STAGING = 288_000 * 6e12 / N      # <= 288k pages of raw HTML at once; 128M pages are 5-6 TB
HEADROOM = 0.10
# OpenSearch default. It applies on a single data node too, and it counts the crawler's
# files on the shared /data: above it every index on the node becomes read-only.
FLOOD_STAGE = 0.95

sizes = json.loads(Path(sys.argv[1]).read_text())
icu = sizes["icu"]
t_sample = icu["text_bytes"] / icu["docs"]
t = max(t_sample, WET)
proj = {}
for name, v in sizes.items():
    ratio = v["segment_bytes"] / v["text_bytes"]
    proj[name] = {"bytes_per_doc": v["bytes_per_doc"], "bytes_per_text_byte": ratio,
                  "by_ratio_tb": N * t * ratio / 1e12,
                  "by_doc_tb": N * v["bytes_per_doc"] / 1e12}
rest = FREE - TEXT - STAGING
budget = {"free_before_prune_tb": FREE / 1e12, "text_tb": TEXT / 1e12,
          "staging_tb": STAGING / 1e12, "B_tb": (1 - HEADROOM) * rest / 1e12,
          "B_keep_10pct_of_disk_tb": (rest - HEADROOM * FS) / 1e12,
          "room_below_flood_stage_tb": (rest - (1 - FLOOD_STAGE) * FS) / 1e12}
recovery = proj["icu_nowait"]["by_ratio_tb"] - proj["icu"]["by_ratio_tb"]
footprint = {}
for name in ("icu", "freqs", "zstd"):
    one = proj[name]["by_ratio_tb"]
    # A build holds the serving index and the new one. Upper bound: as at 44k docs, the
    # new index's unmerged segments still carry the recovery source while the merged
    # copy is written. Lower bound: over a long load the leases advance and natural
    # merges drop the recovery source, and shards merge one at a time.
    footprint[name] = {"one_index_tb": one, "keep_prev_steady_tb": 2 * one,
                       "build_peak_tb": [2 * one, 3 * one + recovery],
                       "build_peak_keep_prev_tb": [3 * one, 4 * one + recovery]}
out = {"t_sample": t_sample, "t_used": t, "projection": proj, "recovery_source_tb": recovery,
       "budget": budget, "footprint": footprint,
       "S_holds_icu": footprint["icu"]["one_index_tb"] <= budget["B_tb"]}
Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
print(f"T sample {t_sample:.0f} B/doc, used {t:.0f}")
for name, p in proj.items():
    print(f"{name:12} {p['bytes_per_doc']:8.0f} B/doc  r={p['bytes_per_text_byte']:.3f}  "
          f"128M: {p['by_ratio_tb']:.3f} TB (by doc {p['by_doc_tb']:.3f})")
print(json.dumps({"recovery_source_tb": recovery, **budget}, indent=1))
print(json.dumps(footprint, indent=1))
print("S holds (icu, one index <= B):", out["S_holds_icu"])
