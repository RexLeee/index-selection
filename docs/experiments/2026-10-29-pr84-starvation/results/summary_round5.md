Round 5, 2026-10-31, Host3. Raw output: `results/analysis_round5.md`. Lanes: `scripts/main5.lanes`;
driver: `scripts/main5.sh`. Stopped and aborted attempts: `results/round5a_stopped.txt` (5a), and
5b below. Local smoke of the pacing variants: `results/smoke5_local.txt`.

Why

Round 4 showed that C2 and C3 pull against each other. A host sends its whole batch at slot pace,
so 120 docs per job burst past 67 per minute on a fast host. A host also gets one batch per job
cycle, so big hosts get far less than 0.2 req/s. Direction A moves the burst limit to the crawler:
it hands a host's recrawl requests to the scheduler at a fixed interval, so a batch can be large
without bursting. isel then runs fewer, larger jobs, so big hosts spend less of each cycle waiting.

Changes

- WebCrawler `381c08d` (branch `pr84-weighted-dispatch`, local):
  `CRAWLER_MANUAL_BULK_CRAWL_HOST_INTERVAL` (seconds, default 0 = off). A host's manual hand-offs
  follow a fixed grid: a late timer shortens the next gap, to no less than half an interval. A
  host behind by half an interval or more restarts the grid from now. Outstanding requests per
  host keep the cap of round 3: ceil(latency / delay) + 1. 136 tests pass, locally and on Host3.
- `iselsim.py` 1399a297c637: a host gets HOST_DOCS_PER_JOB docs per job (or all it has left).
  Before, the job's remainder cut every other host of a 188-doc slice; isel cuts only the last
  host of a 3,000-URL job.

How the crawler change was chosen

| WebCrawler | interval counts from | outstanding per host | where | recrawl per host (target 0.208) |
|---|---|---|---|---|
| `2436f4b` | the actual hand-off | ceil(latency / delay) + 1 | local / Host3 5a | 0.204 / 0.178 |
| `804632f` | the estimated send (response time - download_latency) | 1 | local | 0.166 |
| `d61e4bf` | the actual hand-off | 1 | local / Host3 5a | 0.192 / 0.12 |
| `381c08d` | a fixed grid | ceil(latency / delay) + 1 | Host3 5c, A3 | 0.198 |

- On Host3 the crawler runs at 100 % CPU, as on Host2. Responses queue for the scraper
  (engine_slot_inprogress 340-410 against 253-256 in flight), so a save comes about 5 s after its
  response. With one request outstanding, the next one waits for that save: 0.12 per host.
- Without that cap, timers that fire about 1 s late on the busy reactor still cut the rate to
  0.178. The grid removes that loss.

Setup (5c)

- 6 lanes from 04:08:50Z, jobs from 04:23:50Z, end 05:23:50Z. Crawlers on CPUs 36-41, helpers on
  58-65, 67-69, 71. AutoThrottle as on Host2, weighted, share 20, headroom 1.
- Page weight w = 0.42 (rounds 3-4: 0.38). 5b ran w = 0.38 from 03:53Z and aborted at its warm-up
  check: R0 38.2 req/s, above the 31-38 range, with all 6 lanes at 37.7-38.2 and 100 % CPU. Host3's
  Voyager load had ended. 5c's warm-up: 34.4-35.0 req/s on all lanes at 100 % CPU (Host2 after the
  10-29 deploy: 33-35).
- Recrawl list: process 03's part of the capped 64M list, largest host first.

| lane | interval s | isel min_gap_s | docs per host per job | jobs open (sim) | status poll / archive s |
|---|---|---|---|---|---|
| R0 | - (normal only) | | | | |
| A1 | 5 | 5 | 120 | 16 | 60 / 60 |
| A2 | 5 | 5 | 240 | 32 | 15 / 15 |
| A3 | 4.8 | 4.8 | 240 | 32 | 15 / 15 |
| A4 | 4.8 | 4.8 | 240 | 38 | 15 / 15 |
| A5 | 5 | 5 | 120 | 48 | 60 / 60 |

A1-A4 put 32-38 hosts in jobs at a time. A5 keeps isel's current 48 jobs (96 hosts).

Verdicts (59 min, against R0)

| check | A1 | A2 | A3 | A4 | A5 |
|---|---|---|---|---|---|
| C1 normal >= 80 % of R0 (34.8) | 34.3 = 99 % | 34.3 = 99 % | 34.3 = 99 % | 33.7 = 97 % | 33.6 = 97 % |
| C1 no 60 s below 50 % of R0 median | 32.2 | 32.4 | 32.1 | 30.9 | 31.6 |
| C1 hot hosts keep >= 40 % of normal | FAIL 23 % | FAIL 21 % | FAIL 19 % | FAIL 19 % | 89 % |
| C1 recrawl never 0 for 5 min | 0 | 0 | 0 | 0 | 0 |
| C1 per-host recrawl wait <= 600 s | 28 | 26 | 21 | 24 | 202 |
| C2 recrawl host 10 min rate <= R0 u + 0.22 | 0 hosts over | 0 | 0 | 0 | 0 |
| C2 any host 60 s max <= 1.2 x R0 (21) | 24 | 23 | 24 | 24 | 21 |
| C2 recrawl host 60 s max <= 67 | 24 | 23 | 24 | 24 | 20 |
| C3 recrawl >= 4.0 req/s | 5.84 | 6.11 | 6.32 | 7.49 | 8.40 |
| C3 job lifetime p90 <= 3600 s | 643 | 1251 | 1214 | 1209 | 1226 |
| C3 big-host recrawl p50 (min), req/s | 0.183 (0.179) | 0.192 (0.186) | 0.198 (0.193) | 0.198 (0.190) | 0.047 (0.027) |
| C3 days for 241,920 | 15.3-15.7 | 14.6-15.1 | 14.0-14.5 | 14.1-14.7 | 52-103 |
| C4 recrawl share <= 20.5 % | 15.1 % | 15.6 % | 15.9 % | 18.5 % | 20.0 % |
| ms per manual save | 0.98 | 0.96 | 0.96 | 0.96 | 0.96 |

C3: the cycle

- A host's cycle is its batch at the interval plus the wait for the status poll, the archive and
  the next plan. Measured gaps between a host's jobs: A1 660 s (600 + 60), A2 1,260 s (1,200 + 60),
  A3 1,200-1,230 s (1,152 + 48-78).
- Larger batches and a 15 s poll shrink that overhead. A 4.8 s interval with min_gap_s 4.8 lets the
  rate reach 0.2 within C2's 10-minute bound (0.208 < 0.22).
- A5: 96 hosts x 0.2 = 19 req/s of demand against a 20 % share of about 42 req/s. The share holds at
  20.0 %, so each host gets 0.05-0.10 and big hosts need 52-103 days. Paced hosts in jobs must stay
  under share x total / per-host rate, here 8.4 / 0.208 = about 40 per process.
- Process 03 after 14 days (3 hosts at the 241,920 cap): A3's 0.198 leaves 7,258 URLs (0.16 %);
  A2's 0.192 leaves 0.63 %; A1's 0.183 leaves 1.35 %.
- r000002 is the slowest of A3's capped hosts (0.193): its simulated slot is 0.33 req/s, and normal
  uses 0.17 of it. Host2's capped hostnames have slots of 0.54-1.39 req/s.

C2: the interval bounds the burst

- A host's recrawl alone peaks at 15-16 requests in 60 s (interval 4.8-5 s, so 12-12.5 on
  average). With normal, the busiest host minute is 23-24 against R0's 21.
- Gaps at send time vary: p1 0.5-1.8 s, p50 4.8-5.2 s, and 29-44 % of gaps are under 0.9 x the
  interval (`scripts/diag5_gapsplit.py`). The grid shortens a gap after a late timer, and requests
  wait different times in the scheduler and the slot. The average holds.
- Host2 estimate: a host's minute is at most its normal peak + 16. www.espn.com 25 + 16 = 41,
  ameblo.jp 33 + 16 = 49, both under 67. The busiest big host's normal peak in 6 h was 61; if it
  were recrawled and both peaks met in one minute, it would reach 77.

C1: hot hosts against slot headroom (`scripts/diag3_hot.py`, A3)

| slot headroom r - u (req/s) | hosts | normal kept |
|---|---|---|
| 0.40-0.44 | 12 | 67-74 % |
| 0.36-0.38 | 3 | 53-59 % |
| 0.32 | 1 | 49 % |
| 0.16-0.24 | 3 | 19-33 % |

- At 0.2 req/s of recrawl, a host keeps less normal than in round 4, where big hosts got only
  0.05. The hosts that fail have simulated slots of 0.33-0.41 req/s (latency p50 4-5 s).
- Host2's capped hostnames have r - u >= 0.34, the 0.36-0.38 row or better, so about 50 % or more
  is expected there. Big uncapped hostnames' r - u on Host2 is not measured.

Not covered

- Host order is largest first, so big-host rates are an upper bound, and only the largest 32-40
  hosts got jobs in the hour. isel orders by best rank.
- On Host2 a 3,000-URL job spans 16 processes. With 240 docs per host, a job is about 12.5 hosts,
  0.78 per process, so 48 open jobs would be about 37 paced hosts per process, close to A4. Jobs
  with smaller hosts hold more hosts at once.
- Simulated latency (p50 3.1 s) is CPU queueing, above Host2's 2.1 s.
