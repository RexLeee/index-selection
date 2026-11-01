Round 3, 2026-10-30, Host3 (ntu-sa3: Xeon Platinum 8352V and PM9A3 NVMe ext4 /data, as on Host2).
Raw `scripts/analyze3.py` output: `results/analysis_round3.md`. Calibration and P0:
`results/calibration_host3.txt`. Lane table and driver: `scripts/main3.lanes`, `scripts/main3.sh`.

Setup

- One crawler process per lane, all 10 lanes at once from 04:39:40Z. 15 min normal only, jobs
  from 04:54:41Z, then 60 min (ends 05:54:40Z) or 105 min (F0a, F2, F3; ends 06:39:43Z).
- Crawler pinned to its own physical core (CPUs 37-46); mock web, feeder and isel emulator on
  58-65, 67-71. Python 3.12.14 (uv's newest 3.12; production 3.12.16), Scrapy 2.19.0, Twisted 26.4.0.
- AutoThrottle as on Host2: 256 concurrent, 16 per domain, DOWNLOAD_DELAY 0.25, target 2.0.
- Code: f5a7b93 (pr84-weighted-dispatch + Phase A tests), policy weighted, share 20, headroom 1.
  F1: 39e1363 (production since 10-29 17:29Z), policy manual_first.
- Jobs: `scripts/iselsim.py` plays isel's planner on process 03's part of the capped 64M list
  (w03 sizes): 188 docs per job, up to 48 jobs open, a host ready again 5 s per doc after a job
  took its docs (min_gap_s 5 = 0.2 req/s per host). F4: 20 hot hosts x 10,000 docs instead.
- Page weight w 0.38 (34.8-35.2 req/s normal only at 100 % CPU, production after the deploy
  33-35). F6/F6_0: w 0.69 (20.7-20.9 req/s, production before the deploy 20-22).
- Window: minutes 0-59 after submit + 60 s unless stated; each lane against its own control:
  F5 against F5_0 (same failures), F6 against F6_0 (same pages), the rest against F0a.

Verdicts (59 min)

| check | F1 39e1363 manual_first 120 | F2 120 docs | F3 30 docs | F4 hot 20 x 10k | F5 failures | F6 heavy pages |
|---|---|---|---|---|---|---|
| C1 normal >= 80 % of control | FAIL 3.0 / 35.2 = 9 % | 33.8 = 96 % | 33.9 = 96 % | 34.6 = 98 % | 41.0 / 43.4 = 94 % | 20.4 / 21.2 = 96 % |
| C1 no 60 s below 50 % of control median | FAIL min 0.6 | 31.8 | 32.7 | 32.0 | 38.1 | 18.6 |
| C1 hot hosts keep >= 40 % of normal | FAIL 8 % | 74 % | 76 % | FAIL 17 % | 85 % | 84 % |
| C1 active_domains median >= 140 | 144 | 144 | 144 | 144 | 144 | 144 |
| C1 recrawl never 0 for 5 min | 0 windows | 0 | 0 | 0 | 0 | 0 |
| C1 per-host recrawl wait <= 600 s | 407 | 533 | FAIL 870 | 89 | FAIL 3599 (healthy hosts 695) | FAIL 1482 |
| C2 recrawl host 10 min rate <= control u + 0.22 | FAIL 65 hosts | 0 | FAIL 2 | 0 | FAIL 11 | 0 |
| C2 any host 60 s max <= 1.2 x control | FAIL 86 vs 21 | 23 vs 21 | 24 vs 21 | 24 vs 21 | 80 vs 74 | 17 vs 15 |
| C2 recrawl host 60 s max <= 67 | FAIL 86 | 23 | 23 | 24 | 67 | 14 |
| C3 recrawl >= 4.0 req/s | 18.65 | 8.44 | 8.46 | FAIL 3.96 (20 hosts x 0.2 = 4.0 by design) | 9.79 | 5.09 |
| C3 job lifetime p90 <= 3600 s | 454 | 1393 | 1841 | 145 | 1952 | 1888 |
| C4 recrawl share <= 20.5 % per 10 min | FAIL 90.1 % | 20.0 % | 20.0 % | 10.4 % | 20.1 % | 20.1 % |

- F0b against F0a (same settings twice): normal 35.1 vs 35.2, every check passes. Run-to-run noise
  is well under the C1 margins.
- F1's crawler fell to 25 % CPU: normal starves, and manual (18.65 req/s) cannot use the freed CPU.

F2 and F3 over 104 min, and drift

| run | window | normal (F0a) | recrawl | share max | lifetime p50 / p90 / max s | oldest open s | hot host kept, lowest | C2 10 min hosts over | RSS MB first / last |
|---|---|---|---|---|---|---|---|---|---|
| F2 | 0-104 | 34.2 (36.1) | 8.54 | 20.0 % | 708 / 1712 / 3244 | 3479 | 59 % | 3 | 288 / 298 |
| F3 | 0-104 | 34.3 (36.1) | 8.51 | 20.0 % | 720 / 2162 / 3236 | 3299 | 66 % | 2 | 289 / 298 |
| F2 | 0-52 | 33.7 (35.2) | 8.44 | 20.0 % | 776 / 1360 / 1803 | 2279 | 75 % | 0 | 288 / 300 |
| F2 | 52-104 | 34.6 (36.9) | 8.63 | 20.0 % | 490 / 1400 / 2655 | 2878 | 43 % | 4 | 298 / 298 |
| F3 | 0-52 | 33.9 (35.2) | 8.49 | 20.0 % | 791 / 1709 / 2257 | 2159 | 75 % | 2 | 289 / 294 |
| F3 | 52-104 | 34.7 (36.9) | 8.54 | 19.9 % | 173 / 1856 / 2336 | 2938 | 56 % | 0 | 294 / 298 |

- Normal keeps 94-96 % of F0a in both halves; recrawl and share do not drift; RSS stays flat.
- The lowest hot host's kept normal falls from 75 % to 43-56 % in the second half (see F4 below).
- The per-host wait column of analyze3.py counts from submit, so it is the same in every window.
- Latency quantiles in analysis_round3.md are over the whole lane, not the window.

Diagnostics

1. F5's 3599 s wait is a measurement artifact (`scripts/diag3_failures.py`). The 12 hosts whose
   robots.txt denies all never get a recrawl request. Dead hosts' requests time out before the
   mock web logs them. By host class: robots deny 12 hosts, max 3600 s; dead 17 hosts, max 609 s;
   healthy 227 hosts, max 695 s, 14 over 600 s.

2. CORRECTED by round 4 (`results/summary_round4.md`). `scripts/iselsim.py` lacked one isel rule:
   isel claims only hosts with no open job (`recrawl.py`, `batch_id IS NULL`), so a host is in one
   job at a time. Here a host could join new jobs while its old ones were open, so its releases
   piled up. With the rule, no host gets a second release early, and a host's busiest recrawl
   minute is at most its docs per job. The text below describes the round-3 emulator only.

   A recrawl host's busiest minute is set by its slot rate, not by docs per job
   (`scripts/diag3_burst.py`; slot rate = 60 / (1.218 x max(0.25, latency p50 / 2)) per minute).

   | run | recrawl hosts | busiest minute / slot rate p50 / p90 / max | hosts with more than one release in one release period | worst |
   |---|---|---|---|---|
   | F2 (120, period 600 s) | 96 | 0.71 / 0.79 / 0.87 | 46 | 147 |
   | F3 (30, period 150 s) | 256 | 0.35 / 0.73 / 0.96 | 53 | 44 |
   | F5 (30) | 227 | 0.25 / 0.75 / 1.01 | 60 | 99 |
   | F6 (30) | 256 | 0.38 / 0.66 / 1.05 | 0 | 25 |
   | F1 (120, manual_first) | 128 | 0.95 / 1.07 / 1.68 | 119 | 188 |

   - Recrawl is saturated in every lane: the planner keeps 40-48 jobs open, so hosts queue behind
     each other (waits up to 533-1482 s). A host that waited gets several releases at once, and the
     consumer then sends them at the slot's pace. 30 docs per job only lowers how many hosts do this.
   - The simulated peaks stay low because CPU queueing inflates latency (p50 3.2 s against 2.1 s
     on Host2), so slots are slow. F5 has lower latency (p50 1.8 s): its host r000006 (1.26 s,
     slot 78/min) reached 65 in one minute and 82 docs in one 150 s period, with 30 docs per job.
   - Host2 estimate for the 23 capped hostnames (slot r from `results/production_2026-10-30.txt`):
     www.espn.com 1.39 req/s = 83/min, 0.87-0.96 x that = 72-80, over 67. Next: rottentomatoes 65,
     ameblo 63, open.spotify 61, foxsports 56. Uncapped hostnames with latency p50 under 1.47 s and
     a backlog over 67 docs can also pass 67; their number is not measured.

3. Shared slot, normal on hot hosts (`scripts/diag3_hot.py`, F4 against F0a). Manual holds its
   3 requests in the host's slot, so the scheduler sends that host's normal requests less often,
   even when the slot has room.

   | slot headroom r - u (req/s) | hosts | normal kept | total / slot rate |
   |---|---|---|---|
   | 0.42-0.45 | 13 | 66-72 % | 52-54 % |
   | 0.34-0.39 | 4 | 47-56 % | 52-54 % |
   | 0.15-0.20 | 3 | 17-36 % | 69-71 % |

   On Host2 every capped hostname has r - u >= 0.34 (0.34-1.28), the middle row or better.

4. Milliseconds per manual save (heartbeat `manual_save_ms / manual_saves`, `scripts/diag3_save.py`).
   0.86-0.92 ms in F2 and F3 until 05:49Z, then 1.35-1.46 (05:59Z), 2.6-2.9 (06:09Z) and 3.2-3.6
   (06:19-06:29Z). F6 stays at 0.95-1.02 ms until it ends at 05:54Z. A `yb-voyager` process (root,
   container YugabyteDB-Voyager) started on Host3 at ~05:54Z; at 06:45Z it used ~6 cores and wrote
   606 MB/s to nvme2n1 (/data), 1.94 TB block I/O in total. The rise coincides with that disk load,
   but round 4 ran while Voyager still ran and saw 0.90-0.98 ms, so the cause is not confirmed.
   Even the worst span costs 3.6 ms per save, under the 20 ms abort limit of the canary plan.

Not covered

- Python 3.12.14, not 3.12.16. Simulated latency is CPU queueing, so per-host 60 s peaks are low (2).
- F5 and F6 CPU columns may show F5_0's or F6_0's process: run3.sh's pgrep pattern of that version
  matched the sibling lane by prefix. F5's stop also stopped F5_0's crawler 4 s early (F6 / F6_0
  the same), so the controls lack their last 4 s. The pattern is fixed in run3.sh (`/queue/` anchor).
- 14-day completion, real sites, and the other 15 processes need the canary (plan stage D).
