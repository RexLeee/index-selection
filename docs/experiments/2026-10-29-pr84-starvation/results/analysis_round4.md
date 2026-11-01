# Round-4 batch, 2026-10-30T08:26:23Z

lanes3 exit status: 0

## Warm-up check (minutes 5-14)

    H120 35.4 req/s, cpu 100 %
    H30 35.2 req/s, cpu 100 %
    R0 35.3 req/s, cpu 100 %
    R120 35.0 req/s, cpu 100 %
    R30 35.1 req/s, cpu 100 %

## C1-C4, 59 min after submit + 60 s, against R0

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| R0 | 59 | 37.8 | 0.00 | 0.0% / 0.0% | 22 / 20 | 144 | 254 | 114 | 100 | 3142/5321/7785 |
| R120 | 59 | 35.5 | 8.88 | 20.0% / 20.0% | 22 / 19 | 144 | 252 | 147 | 100 | 3315/4989/7352 |
| R30 | 59 | 35.4 | 8.85 | 20.0% / 20.0% | 22 / 18 | 144 | 252 | 155 | 100 | 3382/5034/7429 |
| H30 | 59 | 37.1 | 3.52 | 8.6% / 9.1% | 27 / 23 | 144 | 253 | 117 | 100 | 2966/4985/7255 |
| H120 | 59 | 37.2 | 3.94 | 9.6% / 10.3% | 25 / 23 | 144 | 253 | 114 | 100 | 2967/4916/7176 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| R0 | 0 / 0 | - / - / - | - | - | - | 276 / 284 | - |
| R120 | 124 / 45 | 863 / 966 / 1687 | 1736 | 509 | - | 293 / 300 | 0.94 |
| R30 | 139 / 48 | 947 / 1050 / 1649 | 1016 | 803 | - | 295 / 301 | 0.93 |
| H30 | 84 / 2 | 103 / 140 / 201 | 59 | 92 | - | 270 / 277 | 0.99 |
| H120 | 72 / 7 | 341 / 437 / 496 | 480 | 64 | - | 289 / 289 | 0.96 |

### R0
- PASS C1 normal >= 80 % of F0: 37.8 vs 37.8
- PASS C1 no 60 s below 50 % of F0 median: min 36.3 vs 18.9
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 22 vs F0 22
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 22
- hot hosts, normal req/s: r000000 0.19, r000001 0.19, r000002 0.18, r000003 0.19, r000004 0.19; total 0.22 mean peak 10 min

### R120
- PASS C1 normal >= 80 % of F0: 35.5 vs 37.8
- PASS C1 no 60 s below 50 % of F0 median: min 34.4 vs 18.9
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 86%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 22 vs F0 22
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 509 s
- PASS C3 recrawl >= 4.0 req/s: 8.88
- PASS C3 job lifetime p90 <= 3600 s: 966
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 22
- hot hosts, normal req/s: r000000 0.18, r000001 0.18, r000002 0.15, r000003 0.17, r000004 0.18; total 0.25 mean peak 10 min

### R30
- PASS C1 normal >= 80 % of F0: 35.4 vs 37.8
- PASS C1 no 60 s below 50 % of F0 median: min 33.7 vs 18.9
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 87%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 22 vs F0 22
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- FAIL C1 recrawl host wait <= 600 s: 803 s
- PASS C3 recrawl >= 4.0 req/s: 8.85
- PASS C3 job lifetime p90 <= 3600 s: 1050
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 19
- hot hosts, normal req/s: r000000 0.18, r000001 0.18, r000002 0.16, r000003 0.17, r000004 0.18; total 0.23 mean peak 10 min

### H30
- PASS C1 normal >= 80 % of F0: 37.1 vs 37.8
- PASS C1 no 60 s below 50 % of F0 median: min 35.4 vs 18.9
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 35%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- FAIL C2 any host's 60 s max <= 1.2 x F0: 27 vs F0 22
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 92 s
- FAIL C3 recrawl >= 4.0 req/s: 3.52
- PASS C3 job lifetime p90 <= 3600 s: 140
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 9.1%
- PASS C2 recrawl hosts' 60 s max <= 67: 27
- hot hosts, normal req/s: r000000 0.17, r000001 0.14, r000002 0.06, r000003 0.10, r000004 0.16; total 0.30 mean peak 10 min

### H120
- PASS C1 normal >= 80 % of F0: 37.2 vs 37.8
- PASS C1 no 60 s below 50 % of F0 median: min 36.0 vs 18.9
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 22%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 25 vs F0 22
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 64 s
- FAIL C3 recrawl >= 4.0 req/s: 3.94
- PASS C3 job lifetime p90 <= 3600 s: 437
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 10.3%
- PASS C2 recrawl hosts' 60 s max <= 67: 25
- hot hosts, normal req/s: r000000 0.15, r000001 0.11, r000002 0.04, r000003 0.08, r000004 0.14; total 0.31 mean peak 10 min


## Big hosts (C3)

```
R120: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    2       1620   0.074           37.9           22      33.7
r000001  241,920    2       1620   0.045           62.3           20      29.5
r000002  241,920    0        nan   0.030           92.6           16      19.6
r000003  136,817    0        nan   0.015          105.7           16      24.9
r000004  132,118    2       1620   0.070           21.8           21      30.9
r000005  123,268    2       1620   0.040           36.0           22      32.8
r000006  118,294    2       1620   0.074           18.6           23      32.9
r000007  108,059    2       1620   0.043           28.9           21      31.7
r000008  105,959    2       1620   0.074           16.6           24      31.5
r000009  103,050    2       1620   0.046           25.9           23      32.1
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.045, min 0.030; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.046, min 0.015; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.044, min 0.015; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 74, recrawl docs/s p50 0.077, min 0.015; 14 days need 0.008 for the smallest

R30: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    2       1620   0.019          147.8           20      32.1
r000001  241,920    2       1620   0.018          152.4           19      29.0
r000002  241,920    2       1620   0.018          154.7           14      20.3
r000003  136,817    2       1620   0.017           93.3           18      25.3
r000004  132,118    2       1620   0.019           80.7           20      29.6
r000005  123,268    2       1620   0.019           74.2           21      30.5
r000006  118,294    2       1620   0.005          254.9           20      30.7
r000007  108,059    3       1230   0.024           52.0           20      29.9
r000008  105,959    3       1230   0.024           51.0           20      30.1
r000009  103,050    3       1230   0.024           49.1           19      30.8
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.018, min 0.018; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.019, min 0.005; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.019, min 0.005; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 74, recrawl docs/s p50 0.031, min 0.005; 14 days need 0.008 for the smallest

H30: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000   10,000   19        180   0.164            0.7           28      38.0
r000001   10,000   19        180   0.164            0.7           24      33.3
r000002   10,000   19        180   0.166            0.7           20      20.4
r000003   10,000   19        180   0.165            0.7           22      26.5
r000004   10,000   19        180   0.164            0.7           25      35.0
r000005   10,000   19        180   0.164            0.7           26      36.7
r000006   10,000   20        171   0.082            1.4           25      36.0
r000007   10,000   23        155   0.161            0.7           26      35.4
r000008   10,000   23        150   0.198            0.6           26      35.8
r000009   10,000   23        150   0.197            0.6           31      36.4
r000010   10,000   23        150   0.198            0.6           25      33.1
r000011   10,000   23        150   0.198            0.6           26      35.7
r000012   10,000   23        150   0.198            0.6           27      36.3
r000013   10,000   24        150   0.191            0.6           28      36.6
r000014   10,000   23        154   0.182            0.6           24      33.9
r000015   10,000   23        154   0.183            0.6           25      35.8
r000016   10,000   22        161   0.187            0.6           28      36.2
r000017   10,000   22        161   0.186            0.6           23      30.6
r000018   10,000   22        161   0.187            0.6           21      23.1
r000019   10,000   22        161   0.180            0.6           31      36.8
hosts with >= 10,000 URLs: 20, recrawl docs/s p50 0.182, min 0.082; 14 days need 0.008 for the smallest

H120: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000   10,000    5        600   0.198            0.6           27      38.3
r000001   10,000    6        600   0.206            0.6           25      33.9
r000002   10,000    6        504   0.202            0.6           20      20.3
r000003   10,000    6        600   0.189            0.6           22      25.2
r000004   10,000    6        492   0.189            0.6           25      35.8
r000005   10,000    7        510   0.190            0.6           26      37.5
r000006   10,000    6        516   0.203            0.6           26      37.3
r000007   10,000    7        500   0.193            0.6           26      36.7
r000008   10,000    6        504   0.188            0.6           25      36.6
r000009   10,000    7        520   0.199            0.6           26      37.3
r000010   10,000    7        440   0.207            0.6           25      33.3
r000011   10,000    7        440   0.192            0.6           25      36.5
r000012   10,000    6        504   0.203            0.6           26      37.0
r000013   10,000    7        480   0.193            0.6           27      37.3
r000014   10,000    7        480   0.198            0.6           25      32.9
r000015   10,000    7        500   0.197            0.6           25      36.6
r000016   10,000    5        600   0.201            0.6           26      36.9
r000017   10,000    6        600   0.206            0.6           24      31.2
r000018   10,000    7        455   0.190            0.6           23      23.5
r000019   10,000    7        460   0.196            0.6           25      36.9
hosts with >= 10,000 URLs: 20, recrawl docs/s p50 0.197, min 0.188; 14 days need 0.008 for the smallest
```

## Busiest minute against slot rate (C2)

```
R120: 96 recrawl hosts; release period 600 s (120 docs)
host                       lat s  slot/min  busiest min  ratio  most manual per period  manual total
r000002.recrawl-sim.com     5.04      19.6           16   0.82                      26           107
r000008.recrawl-sim.com     3.13      31.5           24   0.76                      60           261
r000014.recrawl-sim.com     3.38      29.1           21   0.72                      46           209
r000018.recrawl-sim.com     3.92      25.1           18   0.72                      31           132
r000009.recrawl-sim.com     3.07      32.1           23   0.72                      52           163
r000013.recrawl-sim.com     3.02      32.6           23   0.71                      44           137
r000015.recrawl-sim.com     3.16      31.2           22   0.70                      44           118
r000006.recrawl-sim.com     2.99      32.9           23   0.70                      60           260
busiest minute / slot rate: p50 0.48, p90 0.69, max 0.82
hosts that got more than one release (120 docs) inside one release period: 0 of 96; worst 98

R30: 336 recrawl hosts; release period 150 s (30 docs)
host                       lat s  slot/min  busiest min  ratio  most manual per period  manual total
r000017.recrawl-sim.com     3.59      27.5           20   0.73                      12            68
r000003.recrawl-sim.com     3.90      25.3           18   0.71                       8            60
r000011.recrawl-sim.com     3.29      29.9           21   0.70                      15            87
r000018.recrawl-sim.com     4.04      24.4           17   0.70                       8            66
r000002.recrawl-sim.com     4.86      20.3           14   0.69                       8            64
r000013.recrawl-sim.com     3.24      30.4           21   0.69                       8            19
r000005.recrawl-sim.com     3.23      30.5           21   0.69                      17            68
r000004.recrawl-sim.com     3.33      29.6           20   0.68                      16            67
busiest minute / slot rate: p50 0.28, p90 0.40, max 0.73
hosts that got more than one release (30 docs) inside one release period: 0 of 336; worst 20

H30: 20 recrawl hosts; release period 150 s (30 docs)
host                       lat s  slot/min  busiest min  ratio  most manual per period  manual total
r000002.recrawl-sim.com     4.84      20.4           20   0.98                      39           588
r000018.recrawl-sim.com     4.27      23.1           21   0.91                      36           662
r000009.recrawl-sim.com     2.71      36.4           31   0.85                      42           698
r000019.recrawl-sim.com     2.68      36.8           31   0.84                      40           638
r000003.recrawl-sim.com     3.72      26.5           22   0.83                      36           586
r000016.recrawl-sim.com     2.72      36.2           28   0.77                      36           663
r000013.recrawl-sim.com     2.69      36.6           28   0.76                      38           676
r000010.recrawl-sim.com     2.98      33.1           25   0.76                      36           702
busiest minute / slot rate: p50 0.74, p90 0.91, max 0.98
hosts that got more than one release (30 docs) inside one release period: 20 of 20; worst 43

H120: 20 recrawl hosts; release period 600 s (120 docs)
host                       lat s  slot/min  busiest min  ratio  most manual per period  manual total
r000002.recrawl-sim.com     4.84      20.3           20   0.98                     131           714
r000018.recrawl-sim.com     4.20      23.5           23   0.98                     136           674
r000003.recrawl-sim.com     3.90      25.2           22   0.87                     136           670
r000017.recrawl-sim.com     3.16      31.2           24   0.77                     145           731
r000014.recrawl-sim.com     3.00      32.9           25   0.76                     156           701
r000010.recrawl-sim.com     2.96      33.3           25   0.75                     155           733
r000001.recrawl-sim.com     2.90      33.9           25   0.74                     158           729
r000013.recrawl-sim.com     2.64      37.3           27   0.72                     161           684
busiest minute / slot rate: p50 0.71, p90 0.98, max 0.98
hosts that got more than one release (120 docs) inside one release period: 20 of 20; worst 165
```
