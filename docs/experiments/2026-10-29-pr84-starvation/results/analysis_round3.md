# Round-3 main batch, 2026-10-30T06:39:52Z

lanes3 exit status: 0

## Warm-up check (minutes 5-14)

    F0a 34.8 req/s, cpu 100 %
    F0b 34.8 req/s, cpu 100 %
    F1 35.2 req/s, cpu 100 %
    F2 35.0 req/s, cpu 100 %
    F3 35.1 req/s, cpu 100 %
    F4 34.9 req/s, cpu 100 %
    F5 39.0 req/s, cpu 100 %
    F5_0 39.2 req/s, cpu 100 %
    F6 20.7 req/s, cpu 100 %
    F6_0 20.9 req/s, cpu 100 %

## C1-C4, first 59 min after submit + 60 s, against F0a

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| F0a | 59 | 35.2 | 0.00 | 0.0% / 0.0% | 21 / 19 | 144 | 254 | 110 | 100 | 3182/5646/8217 |
| F0b | 59 | 35.1 | 0.00 | 0.0% / 0.0% | 21 / 18 | 144 | 254 | 112 | 100 | 3208/5747/8425 |
| F1 | 59 | 3.0 | 18.65 | 86.6% / 90.1% | 86 / 56 | 144 | 256 | 0 | 25 | 1947/4161/6659 |
| F2 | 59 | 33.8 | 8.44 | 20.0% / 20.0% | 23 / 20 | 144 | 253 | 132 | 100 | 3224/5153/7470 |
| F3 | 59 | 33.9 | 8.46 | 20.0% / 20.0% | 24 / 20 | 144 | 253 | 139 | 100 | 3258/5141/7486 |
| F4 | 59 | 34.6 | 3.96 | 10.3% / 10.4% | 24 / 22 | 144 | 254 | 108 | 100 | 3016/5307/7777 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| F0a | 0 / 0 | - / - / - | - | - | - | 269 / 269 | - |
| F0b | 0 / 0 | - / - / - | - | - | - | 275 / 274 | - |
| F1 | 320 / 48 | 381 / 454 / 500 | 238 | 407 | - | 269 / 269 | - |
| F2 | 130 / 40 | 747 / 1393 / 2695 | 2099 | 533 | - | 288 / 296 | 0.89 |
| F3 | 130 / 39 | 739 / 1841 / 2451 | 2399 | 870 | - | 289 / 294 | 0.89 |
| F4 | 95 / 4 | 120 / 145 / 161 | 148 | 89 | - | 279 / 274 | 0.91 |

### F0a
- PASS C1 normal >= 80 % of F0: 35.2 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 33.1 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 21 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 19
- hot hosts, normal req/s: r000000 0.18, r000001 0.18, r000002 0.17, r000003 0.18, r000004 0.18; total 0.21 mean peak 10 min

### F0b
- PASS C1 normal >= 80 % of F0: 35.1 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 32.8 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 98%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 21 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 19
- hot hosts, normal req/s: r000000 0.18, r000001 0.18, r000002 0.17, r000003 0.18, r000004 0.18; total 0.21 mean peak 10 min

### F1
- FAIL C1 normal >= 80 % of F0: 3.0 vs 35.2
- FAIL C1 no 60 s below 50 % of F0 median: min 0.6 vs 17.6
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 8%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 65 hosts over
- FAIL C2 any host's 60 s max <= 1.2 x F0: 86 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 407 s
- PASS C3 recrawl >= 4.0 req/s: 18.65
- PASS C3 job lifetime p90 <= 3600 s: 454
- FAIL C4 recrawl share <= 20.5 % in every 10 min: max 90.1%
- FAIL C2 recrawl hosts' 60 s max <= 67: 86
- hot hosts, normal req/s: r000000 0.02, r000001 0.02, r000002 0.01, r000003 0.02, r000004 0.02; total 0.24 mean peak 10 min

### F2
- PASS C1 normal >= 80 % of F0: 33.8 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 31.8 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 74%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 23 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 533 s
- PASS C3 recrawl >= 4.0 req/s: 8.44
- PASS C3 job lifetime p90 <= 3600 s: 1393
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.16, r000001 0.16, r000002 0.13, r000003 0.14, r000004 0.16; total 0.26 mean peak 10 min

### F3
- PASS C1 normal >= 80 % of F0: 33.9 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 32.7 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 76%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 2 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 870 s
- PASS C3 recrawl >= 4.0 req/s: 8.46
- PASS C3 job lifetime p90 <= 3600 s: 1841
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.16, r000001 0.16, r000002 0.13, r000003 0.14, r000004 0.17; total 0.27 mean peak 10 min

### F4
- PASS C1 normal >= 80 % of F0: 34.6 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 32.0 vs 17.6
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 17%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 89 s
- FAIL C3 recrawl >= 4.0 req/s: 3.96
- PASS C3 job lifetime p90 <= 3600 s: 145
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 10.4%
- PASS C2 recrawl hosts' 60 s max <= 67: 24
- hot hosts, normal req/s: r000000 0.13, r000001 0.10, r000002 0.03, r000003 0.06, r000004 0.13; total 0.29 mean peak 10 min


## F5 against F5_0, 59 min

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| F5_0 | 59 | 43.4 | 0.00 | 0.0% / 0.0% | 74 / 53 | 144 | 244 | 41 | 100 | 1825/3482/5523 |
| F5 | 59 | 41.0 | 9.79 | 19.8% / 20.1% | 80 / 52 | 144 | 245 | 39 | 100 | 1789/3354/5455 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| F5_0 | 0 / 0 | - / - / - | - | - | 301: 4.8%, 429: 2.0% | 231 / 223 | - |
| F5 | 128 / 47 | 596 / 1952 / 2751 | 2639 | 3599 | 301: 4.8%, 429: 2.0% | 231 / 223 | 0.97 |

### F5_0
- PASS C1 normal >= 80 % of F0: 43.4 vs 43.4
- PASS C1 no 60 s below 50 % of F0 median: min 40.6 vs 21.7
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 74 vs F0 74
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 54
- hot hosts, normal req/s: r000000 0.22, r000001 0.00, r000002 0.22, r000003 0.22, r000004 0.22; total 0.21 mean peak 10 min

### F5
- PASS C1 normal >= 80 % of F0: 41.0 vs 43.4
- PASS C1 no 60 s below 50 % of F0 median: min 38.1 vs 21.7
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 85%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 11 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 80 vs F0 74
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 3599 s
- PASS C3 recrawl >= 4.0 req/s: 9.79
- PASS C3 job lifetime p90 <= 3600 s: 1952
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.1%
- PASS C2 recrawl hosts' 60 s max <= 67: 67
- hot hosts, normal req/s: r000000 0.21, r000001 0.00, r000002 0.18, r000003 0.19, r000004 0.21; total 0.35 mean peak 10 min


## F6 against F6_0, 59 min

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| F6_0 | 59 | 21.2 | 0.00 | 0.0% / 0.0% | 15 / 11 | 144 | 255 | 130 | 100 | 5863/8375/11163 |
| F6 | 59 | 20.4 | 5.09 | 20.0% / 20.1% | 17 / 11 | 144 | 254 | 162 | 100 | 6000/7760/11324 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| F6_0 | 0 / 0 | - / - / - | - | - | - | 409 / 426 | - |
| F6 | 71 / 47 | 1532 / 1888 / 2378 | 2698 | 1482 | - | 451 / 452 | 0.98 |

### F6_0
- PASS C1 normal >= 80 % of F0: 21.2 vs 21.2
- PASS C1 no 60 s below 50 % of F0 median: min 18.8 vs 10.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 15 vs F0 15
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 12
- hot hosts, normal req/s: r000000 0.10, r000001 0.10, r000002 0.10, r000003 0.10, r000004 0.10; total 0.14 mean peak 10 min

### F6
- PASS C1 normal >= 80 % of F0: 20.4 vs 21.2
- PASS C1 no 60 s below 50 % of F0 median: min 18.6 vs 10.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 84%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 17 vs F0 15
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 1482 s
- PASS C3 recrawl >= 4.0 req/s: 5.09
- PASS C3 job lifetime p90 <= 3600 s: 1888
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.1%
- PASS C2 recrawl hosts' 60 s max <= 67: 14
- hot hosts, normal req/s: r000000 0.09, r000001 0.09, r000002 0.09, r000003 0.08, r000004 0.09; total 0.15 mean peak 10 min


## F2, F3 full 104 min (F7)

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| F2 | 104 | 34.2 | 8.54 | 20.0% / 20.0% | 26 / 21 | 144 | 253 | 132 | 99 | 3224/5153/7470 |
| F3 | 104 | 34.3 | 8.51 | 19.9% / 20.0% | 24 / 21 | 144 | 253 | 137 | 99 | 3258/5141/7486 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| F2 | 250 / 43 | 708 / 1712 / 3244 | 3479 | 533 | - | 288 / 298 | 1.85 |
| F3 | 249 / 41 | 720 / 2162 / 3236 | 3299 | 870 | - | 289 / 298 | 1.84 |

### F2
- PASS C1 normal >= 80 % of F0: 34.2 vs 36.1
- PASS C1 no 60 s below 50 % of F0 median: min 31.8 vs 17.9
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 59%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 3 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 26 vs F0 23
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 533 s
- PASS C3 recrawl >= 4.0 req/s: 8.54
- PASS C3 job lifetime p90 <= 3600 s: 1712
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 26
- hot hosts, normal req/s: r000000 0.14, r000001 0.14, r000002 0.10, r000003 0.11, r000004 0.14; total 0.29 mean peak 10 min

### F3
- PASS C1 normal >= 80 % of F0: 34.3 vs 36.1
- PASS C1 no 60 s below 50 % of F0 median: min 31.8 vs 17.9
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 66%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 2 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 23
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 870 s
- PASS C3 recrawl >= 4.0 req/s: 8.51
- PASS C3 job lifetime p90 <= 3600 s: 2162
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.15, r000001 0.15, r000002 0.12, r000003 0.13, r000004 0.16; total 0.29 mean peak 10 min


## Drift: minutes 0-52

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| F0a | 52 | 35.2 | 0.00 | 0.0% / 0.0% | 21 / 19 | 144 | 254 | 110 | 100 | 3182/5646/8217 |
| F2 | 52 | 33.7 | 8.44 | 20.0% / 20.0% | 23 / 19 | 144 | 253 | 132 | 100 | 3224/5153/7470 |
| F3 | 52 | 33.9 | 8.49 | 20.0% / 20.0% | 24 / 19 | 144 | 253 | 140 | 100 | 3258/5141/7486 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| F0a | 0 / 0 | - / - / - | - | - | - | 269 / 269 | - |
| F2 | 110 / 48 | 776 / 1360 / 1803 | 2279 | 533 | - | 288 / 300 | 0.88 |
| F3 | 110 / 48 | 791 / 1709 / 2257 | 2159 | 870 | - | 289 / 294 | 0.89 |

### F0a
- PASS C1 normal >= 80 % of F0: 35.2 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 33.1 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 21 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 19
- hot hosts, normal req/s: r000000 0.18, r000001 0.18, r000002 0.17, r000003 0.18, r000004 0.18; total 0.21 mean peak 10 min

### F2
- PASS C1 normal >= 80 % of F0: 33.7 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 31.8 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 75%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 23 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 533 s
- PASS C3 recrawl >= 4.0 req/s: 8.44
- PASS C3 job lifetime p90 <= 3600 s: 1360
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.17, r000001 0.16, r000002 0.13, r000003 0.14, r000004 0.16; total 0.26 mean peak 10 min

### F3
- PASS C1 normal >= 80 % of F0: 33.9 vs 35.2
- PASS C1 no 60 s below 50 % of F0 median: min 32.7 vs 17.6
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 75%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 2 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 870 s
- PASS C3 recrawl >= 4.0 req/s: 8.49
- PASS C3 job lifetime p90 <= 3600 s: 1709
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.17, r000001 0.16, r000002 0.13, r000003 0.15, r000004 0.17; total 0.27 mean peak 10 min


## Drift: minutes 52-104

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| F0a | 52 | 36.9 | 0.00 | 0.0% / 0.0% | 23 / 20 | 144 | 254 | 109 | 100 | 3182/5646/8217 |
| F2 | 52 | 34.6 | 8.63 | 20.0% / 20.0% | 26 / 22 | 144 | 254 | 130 | 98 | 3224/5153/7470 |
| F3 | 52 | 34.7 | 8.54 | 19.8% / 19.9% | 23 / 21 | 144 | 253 | 135 | 98 | 3258/5141/7486 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| F0a | 0 / 0 | - / - / - | - | - | - | 270 / 274 | - |
| F2 | 93 / 42 | 490 / 1400 / 2655 | 2878 | 533 | - | 298 / 298 | 2.80 |
| F3 | 92 / 40 | 173 / 1856 / 2336 | 2938 | 870 | - | 294 / 298 | 2.78 |

### F0a
- PASS C1 normal >= 80 % of F0: 36.9 vs 36.9
- PASS C1 no 60 s below 50 % of F0 median: min 33.5 vs 18.4
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 23 vs F0 23
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 20
- hot hosts, normal req/s: r000000 0.19, r000001 0.19, r000002 0.18, r000003 0.18, r000004 0.19; total 0.20 mean peak 10 min

### F2
- PASS C1 normal >= 80 % of F0: 34.6 vs 36.9
- PASS C1 no 60 s below 50 % of F0 median: min 32.5 vs 18.4
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 43%
- FAIL C2 10 min rate <= F0 normal + 0.22 req/s: 4 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 26 vs F0 23
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 533 s
- PASS C3 recrawl >= 4.0 req/s: 8.63
- PASS C3 job lifetime p90 <= 3600 s: 1400
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 26
- hot hosts, normal req/s: r000000 0.12, r000001 0.11, r000002 0.08, r000003 0.09, r000004 0.12; total 0.29 mean peak 10 min

### F3
- PASS C1 normal >= 80 % of F0: 34.7 vs 36.9
- PASS C1 no 60 s below 50 % of F0 median: min 31.8 vs 18.4
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 56%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 23 vs F0 23
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 870 s
- PASS C3 recrawl >= 4.0 req/s: 8.54
- PASS C3 job lifetime p90 <= 3600 s: 1856
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 19.9%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.14, r000001 0.14, r000002 0.10, r000003 0.11, r000004 0.15; total 0.28 mean peak 10 min

