Round 6, 2026-11-01, Host3. Raw output: `results/analysis_round6.md`. Lanes: `scripts/main6.lanes`;
driver: `scripts/main6.sh`; per-host concurrency and 429 hosts: `scripts/diag6_concurrency.py`.

Why

The TA's review (Discord, 2026-10-31 18:56Z): fixed numbers (the cap "3", the 4.8 s interval) break
when conditions change; a host should send as much as its politeness limit allows. WebCrawler
`498a195` drops the per-host interval (`e1ae98d`, `f24189c`). AutoThrottle's slot delay is the only
per-host pacing, as for normal requests; isel's `min_gap_s` (next_at = claim + docs x 4.8 s) keeps
the 0.208 req/s average per host. Round 6 checks that the list still finishes, that normal keeps
its share, that the cap follows TARGET_CONCURRENCY, and that 429 hosts do not stall jobs.

Setup

- 7 lanes at once from 02:17Z, jobs from 02:32:13Z, 59 min window. Same list (process 03 of the
  capped 64M list), iselsim settings as round 5's A3 (240 docs per host per job, 32 jobs open,
  min_gap_s 4.8, poll and archive 15 s), weighted, share 20.
- Host3 now also serves a production YugabyteDB node: every core was about 28 % busy, so no core
  was free to pin and lanes ran unpinned. Round 6a (w = 0.42) aborted at its warm-up check: R0
  25.3 req/s at 100 % CPU. Round 6 used w = 0.31 (0.42 x 25.3 / 34.5): warm-up 34.3-34.5 req/s on
  R0, B1, B2, T0, T4 at 100 % CPU; F0 / F1 41.1-41.5 (429 answers are cheap).
- B2: headroom 0. T0 / T4: TARGET_CONCURRENCY 4.0. F0 / F1: 1 in 10 hosts answers 429 to every
  page, 5 % of requests redirect.

Results (59 min)

| | R0 | B1 | B2 (headroom 0) | T4 (target 4, vs T0) | F1 (429, vs F0) | round 5 A3 (interval 4.8) |
|---|---|---|---|---|---|---|
| normal req/s, % of base | 33.8 | 30.7, 91 % | 30.6, 91 % | 30.5, 90 % | 36.5, 90 % | 34.3, 99 % |
| recrawl req/s | 0 | 7.64 | 6.99 | 7.63 | 9.11 | 6.32 |
| recrawl share, mean / max 10 min | - | 19.9 / 20.0 % | 18.6 / 18.8 % | 20.0 / 20.0 % | 20.0 / 20.0 % | 15.9 % |
| job lifetime p50 / p90 / max s | - | 959 / 1148 / 1517 | 1038 / 1259 / 1560 | 965 / 1180 / 1498 | 813 / 1035 / 2329 | - / 1214 / - |
| longest host wait s | - | 41 | 71 | 101 | 31 | 21 |
| capped hosts docs/s (r000000, 1, 2) | - | 0.199, 0.196, 0.162 | 0.202, 0.200, 0.159 | 0.200, 0.192, 0.166 | 0.201, 0.198, 0.171 | 0.198 p50, 0.193 min |
| hot host normal kept, lowest | 100 % | 29 % | 32 % | 47 % | 16 % | 19 % |
| any host 60 s max (base) | 18 | 25 (18) | 25 (18) | 27 (23) | 28 (26) | 24 (21) |
| recrawl host 60 s max | - | 22 | 23 | 27 | 28 | 24 |
| manual in flight per host at the server, max | - | 3 | 3 (p90 2) | 3 | 3 | - |
| ms per manual save | - | 1.33 | 1.37 | 1.34 | 1.34 | 0.96 |

Findings

- The share binds, not the per-host cap or the interval. Without the interval, recrawl uses its
  whole 20 % (7.64 req/s against A3's 6.32), so normal keeps 91 % of R0 instead of 99 %. That is the
  share's guarantee (normal >= 80 % of dispatches); recrawl is cheaper to process, so normal keeps
  more than 80 % of its rate.
- Process 03 needs 3.78 req/s; B1 gives 7.64.
- Per host, the 20 % is spread over the 32-60 hosts in jobs: about 8-12 manual requests a minute
  per host on average (0.13-0.20 req/s). Bursts still go at the slot's rate: a big host's busiest
  minute is 0.60-0.91 x its slot rate in B1 (mean 0.70; T4 0.65-1.01). The absolute 22-28 a minute
  is small only because the simulated slots are slow (20-34 a minute). On Host2, www.espn.com's
  slot is about 83 a minute, so its busiest recrawl minute would be about 50-75, against a
  6-hour normal peak of 56 per host on 2026-10-30 (an estimate, not measured).
- Cap: manual in flight per host stays at or below 3 (B1) and mostly 2 with headroom 0 (B2). The
  +1 is worth 9 % of recrawl (7.64 vs 6.99). T4 also peaks at 3: the share, not the cap, limits a
  host here, so the cap of 5 is not reached. The local smoke (40 hosts, 200 s, share not yet full)
  reached 5 at target 4 (`results/smoke6_local.txt`); the unit test pins it
  (`test_host_cap_follows_the_slot_delay`, (2.0, 0.5) -> 4 + headroom).
- Target 4 adds no throughput (T4 7.63 vs B1 7.64, normal 90 % vs 91 %): the crawler is CPU-bound.
- 429: the 7 all-429 recrawl hosts got 11.8 manual requests a minute (p50; others 9.6), busiest 60 s
  22. AutoThrottle does not slow a 429 host; the share and isel's pacing keep it near 0.2 req/s.
  isel then pushes the host back when it applies the job (`recrawl.py:999-1003`). All F1 jobs
  completed or were moving: longest host wait 31 s, lifetime max 2329 s.
- Capped hosts: r000000 and r000001 got 0.196-0.202 docs/s (13.8-14.5 days for 241,920), as in A3.
  r000002 got 0.159-0.171 (16.4-17.6 days; A3 0.193). Its simulated slot is 0.33 req/s (19.8 a
  minute) with latency p50 4-5 s; Host2's 23 capped hostnames have slots of 0.54-1.39 req/s, so
  none is that slow. Per job, r000002 now needs about 1,500 s for 240 docs, longer than isel's
  1,152 s gap; with the interval its sends were spread over the 1,152 s grid at a higher priority.
- Process 03 after 14 days at B1's rates: 52,013 URLs left (1.14 %), 45,965 of them on r000002.
  Without that host: 6,048 (0.13 %); A3: 7,258 (0.16 %).
- Hot hosts keep more normal than with the interval (lowest 29 % vs 19 %), but still fail the
  40 % check on simulated slots of 0.33-0.41 req/s.

Checks that fail by design

- "10 min rate <= R0 u + 0.22" (29-39 hosts over) and "any host's 60 s max <= 1.2 x R0" were the
  user's C2: no host above today's rate. Without the interval a recrawl host may run above 0.22
  for 10 min while its batch goes out; AutoThrottle's slot delay is the bound now, which is the
  review's position.

Not covered

- Same as round 5: one process of 16, mock sites that never ban, largest host first.
- Host3's production YugabyteDB load (about 28 % per core) is extra noise; all lanes shared it.
