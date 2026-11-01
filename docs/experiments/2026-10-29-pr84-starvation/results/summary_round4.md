Round 4, 2026-10-30, Host3. Raw output: `results/analysis_round4.md`. Lanes: `scripts/main4.lanes`;
driver: `scripts/main4.sh`; per-host rates: `scripts/diag4_bighost.py`.

Why

Round 3's `iselsim.py` let a host join new jobs while its old ones were open. isel does not:
`recrawl.py` claims only hosts with `batch_id IS NULL`, and frees a host when the archive
applies its job. Round 4 adds that rule (`iselsim.py` 3ae4d0e543f7): a host is ready when it
is in no job and 5 s per doc have passed; a status poll every 60 s moves a completed job out,
and an archive pass every 60 s frees its hosts. A local smoke showed no host in two open jobs;
a host got its next job 54-88 s after the previous one completed.

Setup

- Same as round 3 (f5a7b93, weighted, share 20, headroom 1, AutoThrottle as on Host2, w 0.38),
  5 lanes at once from 07:11:11Z, jobs from 07:26:14Z, end 08:26:14Z. Crawlers on CPUs 39-43,
  helpers on 58-65, 67-69, 71. A Voyager migration (yb-voyager and pg_dump) ran on Host3 the
  whole time.
- R0 normal only; R120 / R30: process 03's part of the capped 64M list, 120 / 30 docs per host
  per job; H30 / H120: 20 hot hosts x 10,000 docs, 30 / 120 docs.
- Warm-up (minutes 5-14): 35.0-35.4 req/s, CPU 100 % on all 5 lanes.

Verdicts (59 min, against R0)

| check | R120 | R30 | H30 | H120 |
|---|---|---|---|---|
| C1 normal >= 80 % of R0 | 35.5 / 37.8 = 94 % | 35.4 = 94 % | 37.1 = 98 % | 37.2 = 98 % |
| C1 no 60 s below 50 % of R0 median | 34.4 | 33.7 | 35.4 | 36.0 |
| C1 hot hosts keep >= 40 % of normal | 86 % | 87 % | FAIL 35 % | FAIL 22 % |
| C1 recrawl never 0 for 5 min | 0 | 0 | 0 | 0 |
| C1 per-host recrawl wait <= 600 s | 509 | FAIL 803 | 92 | 64 |
| C2 recrawl host 10 min rate <= R0 u + 0.22 | 0 hosts over | 0 | 0 | 0 |
| C2 any host 60 s max <= 1.2 x R0 (22) | 22 | 22 | FAIL 27 | 25 |
| C2 recrawl host 60 s max <= 67 | 22 | 19 | 27 | 25 |
| C3 recrawl >= 4.0 req/s | 8.88 | 8.85 | 3.52 (20 hosts) | 3.94 (20 hosts) |
| C3 job lifetime p90 <= 3600 s | 966 | 1050 | 140 | 437 |
| C4 recrawl share <= 20.5 % | 20.0 % | 20.0 % | 9.1 % | 10.3 % |
| ms per manual save | 0.94 | 0.93 | 0.99 | 0.96 |

C2: a host's burst is now bounded by docs per job (`scripts/diag3_burst.py`)

| run | recrawl hosts | busiest minute / slot rate p50 / p90 / max | hosts with more than one release in one release period |
|---|---|---|---|
| R120 | 96 | 0.48 / 0.69 / 0.82 | 0 of 96 |
| R30 | 336 | 0.28 / 0.40 / 0.73 | 0 of 336 |
| H30 | 20 | 0.74 / 0.91 / 0.98 | 20 of 20 (worst 43 docs in 150 s) |
| H120 | 20 | 0.71 / 0.98 / 0.98 | 20 of 20 (worst 165 docs in 600 s) |

- In R120 and R30 no host gets a second release before its first is sent; round 3 had 46 and 53.
- H30 / H120 hosts get their next job right after the last (jobs end in 100-340 s), so one
  sliding window can hold the end of one release and the start of the next. That is at most
  about two releases, not a pile-up.
- Host2 estimate: with 30 docs a host's recrawl minute is at most 30, so www.espn.com stays
  at about 30 + 25 (its normal peak) = 55 <= 67. With 120 docs a batch runs at slot pace for
  over a minute: espn's slot is 83/min, and the busiest minute reached 0.82-0.98 x slot, so 68-81.

C3: big hosts are too slow (`scripts/diag4_bighost.py`)

| run | hosts >= 100k URLs | recrawl docs/s per host p50 (min) | days for 241,920 | mean gap between a host's jobs |
|---|---|---|---|---|
| R120 | 11 | 0.046 (0.015) | 38-93 | 1620 s |
| R30 | 11 | 0.019 (0.005) | 148-155 | 1230-1620 s |
| H30 | (20 hosts x 10k) | 0.182 (0.082) | - | 150-180 s |
| H120 | (20 hosts x 10k) | 0.197 (0.188) | - | 440-600 s |

- A host gets one batch per job cycle: job lifetime plus the poll and archive delay (~75 s).
  With the crawler saturated, Little's law gives the lifetime: open jobs x URLs per job /
  recrawl rate = 48 x 188 / 8.85 = 1,020 s (measured p50 863-947 s). So a big host gets
  120 docs per ~1,000-1,600 s (0.07-0.12 docs/s), or 30 per the same (0.02-0.03).
- H30 / H120 have only 20 hosts, so jobs are short (p50 103 / 341 s) and hosts reach
  0.16-0.20 docs/s. This confirms that the job cycle, not crawler capacity, limits big hosts.
- Effect on process 03 (69,599 hosts, 4,575,213 URLs), URLs left after 14 days if every host
  is held to the measured rate:

  | per-host rate | docs per host in 14 days | hosts that cannot finish | URLs left |
  |---|---|---|---|
  | 0.2 (isel's limit) | 241,920 | 0 | 0 % |
  | 0.074 (R120, best big host) | 89,510 | 12 | 14.8 % |
  | 0.046 (R120 p50 of hosts >= 100k) | 55,642 | 19 | 25.9 % |
  | 0.019 (R30 p50 of hosts >= 100k) | 22,982 | 42 | 45.3 % |

- On Host2 a job's 3,000 URLs span 16 processes and the job completes with the slowest, so
  lifetimes and gaps there are likely longer than here.

Other

- Normal keeps 94 % of R0. Hot hosts in R lanes keep 86-87 % of their normal rate (round 3:
  74-76 %), as big hosts no longer pile up. H lanes put all recrawl on 20 hot hosts whose
  simulated slots are slow (r - u 0.15-0.45); Host2's capped hosts have r - u >= 0.34.
- H30's 60 s max of 27 against R0's 22 is a hot host's normal plus recrawl; it is far below 67.
- Manual saves cost 0.90-0.98 ms while the Voyager migration ran. Round 3's rise to 3.5 ms after
  05:54Z coincided with Voyager's start, but round 4 did not repeat it, so its cause is open.
