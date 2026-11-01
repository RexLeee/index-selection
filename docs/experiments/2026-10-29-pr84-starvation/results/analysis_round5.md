# Round-5c batch, 2026-10-31T05:24:08Z

lanes3 exit status: 0

## Warm-up check (minutes 5-14)

    A1 34.8 req/s, cpu 100 %
    A2 34.8 req/s, cpu 100 %
    A3 35.0 req/s, cpu 100 %
    A4 34.6 req/s, cpu 100 %
    A5 34.7 req/s, cpu 100 %
    R0 34.4 req/s, cpu 100 %

## C1-C4, 59 min after submit + 60 s, against R0

| run | min | normal/s | recrawl/s | share mean / max | host 60 s max / p99 | active domains | inflight | backlog | cpu % | latency p50/p90/p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| R0 | 59 | 34.8 | 0.00 | 0.0% / 0.0% | 21 / 18 | 144 | 254 | 110 | 100 | 3205/5837/8381 |
| A1 | 59 | 34.3 | 5.84 | 14.6% / 15.1% | 24 / 21 | 144 | 253 | 116 | 100 | 3098/5308/7860 |
| A2 | 59 | 34.3 | 6.11 | 15.1% / 15.6% | 23 / 21 | 144 | 254 | 114 | 100 | 3098/5261/7736 |
| A3 | 59 | 34.3 | 6.32 | 15.5% / 15.9% | 24 / 21 | 144 | 253 | 117 | 100 | 3087/5232/7686 |
| A4 | 59 | 33.7 | 7.49 | 18.2% / 18.5% | 24 / 21 | 144 | 254 | 120 | 100 | 3136/5327/7744 |
| A5 | 59 | 33.6 | 8.40 | 20.0% / 20.0% | 21 / 18 | 144 | 253 | 135 | 100 | 3566/5460/8072 |

| run | jobs done / open | lifetime p50 / p90 / max s | oldest open s | longest host wait s | non-200 share | RSS MB first / last 5 min | ms per manual save |
|---|---|---|---|---|---|---|---|
| R0 | 0 / 0 | - / - / - | - | - | - | 304 / 305 | - |
| A1 | 64 / 16 | 626 / 643 / 658 | 299 | 28 | - | 284 / 295 | 0.98 |
| A2 | 48 / 32 | 1238 / 1251 / 1299 | 1079 | 26 | - | 286 / 299 | 0.96 |
| A3 | 48 / 32 | 1193 / 1214 / 1235 | 1169 | 21 | - | 299 / 295 | 0.96 |
| A4 | 60 / 38 | 1193 / 1209 / 1260 | 1199 | 24 | - | 293 / 296 | 0.96 |
| A5 | 88 / 39 | 1174 / 1226 / 1243 | 1499 | 202 | - | 300 / 317 | 0.96 |

### R0
- PASS C1 normal >= 80 % of F0: 34.8 vs 34.8
- PASS C1 no 60 s below 50 % of F0 median: min 32.5 vs 17.4
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 100%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 21 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C2 recrawl hosts' 60 s max <= 67: 19
- hot hosts, normal req/s: r000000 0.18, r000001 0.18, r000002 0.17, r000003 0.18, r000004 0.18; total 0.20 mean peak 10 min

### A1
- PASS C1 normal >= 80 % of F0: 34.3 vs 34.8
- PASS C1 no 60 s below 50 % of F0 median: min 32.2 vs 17.4
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 23%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 28 s
- PASS C3 recrawl >= 4.0 req/s: 5.84
- PASS C3 job lifetime p90 <= 3600 s: 643
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 15.1%
- PASS C2 recrawl hosts' 60 s max <= 67: 24
- hot hosts, normal req/s: r000000 0.14, r000001 0.11, r000002 0.04, r000003 0.07, r000004 0.13; total 0.29 mean peak 10 min

### A2
- PASS C1 normal >= 80 % of F0: 34.3 vs 34.8
- PASS C1 no 60 s below 50 % of F0 median: min 32.4 vs 17.4
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 21%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 23 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 26 s
- PASS C3 recrawl >= 4.0 req/s: 6.11
- PASS C3 job lifetime p90 <= 3600 s: 1251
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 15.6%
- PASS C2 recrawl hosts' 60 s max <= 67: 23
- hot hosts, normal req/s: r000000 0.14, r000001 0.10, r000002 0.04, r000003 0.06, r000004 0.13; total 0.29 mean peak 10 min

### A3
- PASS C1 normal >= 80 % of F0: 34.3 vs 34.8
- PASS C1 no 60 s below 50 % of F0 median: min 32.1 vs 17.4
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 19%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 21 s
- PASS C3 recrawl >= 4.0 req/s: 6.32
- PASS C3 job lifetime p90 <= 3600 s: 1214
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 15.9%
- PASS C2 recrawl hosts' 60 s max <= 67: 24
- hot hosts, normal req/s: r000000 0.13, r000001 0.10, r000002 0.03, r000003 0.06, r000004 0.12; total 0.30 mean peak 10 min

### A4
- PASS C1 normal >= 80 % of F0: 33.7 vs 34.8
- PASS C1 no 60 s below 50 % of F0 median: min 30.9 vs 17.4
- FAIL C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 19%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 24 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 24 s
- PASS C3 recrawl >= 4.0 req/s: 7.49
- PASS C3 job lifetime p90 <= 3600 s: 1209
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 18.5%
- PASS C2 recrawl hosts' 60 s max <= 67: 24
- hot hosts, normal req/s: r000000 0.13, r000001 0.10, r000002 0.03, r000003 0.05, r000004 0.12; total 0.29 mean peak 10 min

### A5
- PASS C1 normal >= 80 % of F0: 33.6 vs 34.8
- PASS C1 no 60 s below 50 % of F0 median: min 31.6 vs 17.4
- PASS C1 each hot host keeps >= 40 % of its F0 normal rate: lowest 89%
- PASS C2 10 min rate <= F0 normal + 0.22 req/s: 0 hosts over
- PASS C2 any host's 60 s max <= 1.2 x F0: 21 vs F0 21
- PASS C1 active_domains median >= 140: 144
- PASS C1 recrawl never 0 for 5 min: 0 windows
- PASS C1 recrawl host wait <= 600 s: 202 s
- PASS C3 recrawl >= 4.0 req/s: 8.40
- PASS C3 job lifetime p90 <= 3600 s: 1226
- PASS C4 recrawl share <= 20.5 % in every 10 min: max 20.0%
- PASS C2 recrawl hosts' 60 s max <= 67: 20
- hot hosts, normal req/s: r000000 0.17, r000001 0.17, r000002 0.15, r000003 0.16, r000004 0.17; total 0.23 mean peak 10 min


## Big hosts (C3)

```
A1: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    5        660   0.183           15.3           25      37.0
r000001  241,920    5        660   0.184           15.2           23      32.3
r000002  241,920    5        660   0.179           15.7           17      19.2
r000003  136,817    5        660   0.179            8.8           20      22.5
r000004  132,118    5        660   0.183            8.4           24      35.0
r000005  123,268    5        660   0.184            7.8           24      35.8
r000006  118,294    5        660   0.183            7.5           26      35.8
r000007  108,059    5        660   0.183            6.8           25      35.3
r000008  105,959    5        660   0.183            6.7           23      35.1
r000009  103,050    5        660   0.183            6.5           25      35.6
r000010  102,756    5        660   0.183            6.5           21      31.7
r000011   94,706    5        660   0.183            6.0           24      35.1
r000012   87,852    5        660   0.182            5.6           23      35.5
r000013   74,026    5        660   0.183            4.7           24      35.9
r000014   73,688    5        660   0.183            4.7           22      32.7
r000015   71,364    5        660   0.183            4.5           24      34.8
r000016   65,301    5        660   0.184            4.1           25      36.1
r000017   61,577    5        660   0.184            3.9           22      29.5
r000018   59,833    5        660   0.181            3.8           18      21.6
r000019   53,673    5        660   0.181            3.4           23      35.8
r000020   49,302    5        660   0.183            3.1           13      32.4
r000021   48,299    5        660   0.183            3.1           13      28.8
r000022   48,186    5        660   0.183            3.1           13      25.0
r000023   42,753    5        660   0.183            2.7           13      21.7
r000024   42,572    5        660   0.183            2.7           13      22.3
r000025   40,226    5        660   0.183            2.5           14      24.2
r000026   38,794    5        660   0.183            2.5           14      23.7
r000027   37,806    5        660   0.183            2.4           14      22.9
r000028   36,220    5        668   0.181            2.3           14      21.2
r000029   31,976    5        668   0.181            2.0           14      24.9
r000030   31,120    5        660   0.183            2.0           14      24.7
r000031   30,511    5        660   0.183            1.9           14      21.2
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.183, min 0.179; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.183, min 0.179; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.183, min 0.179; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 32, recrawl docs/s p50 0.183, min 0.179; 14 days need 0.008 for the smallest

A2: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    2       1260   0.192           14.6           24      36.6
r000001  241,920    2       1260   0.192           14.6           22      33.1
r000002  241,920    2       1320   0.186           15.1           17      19.4
r000003  136,817    2       1260   0.190            8.3           20      23.5
r000004  132,118    2       1260   0.192            8.0           24      35.0
r000005  123,268    2       1260   0.192            7.4           25      35.4
r000006  118,294    2       1260   0.192            7.1           24      35.7
r000007  108,059    2       1260   0.191            6.5           25      35.7
r000008  105,959    2       1260   0.190            6.5           24      35.2
r000009  103,050    2       1260   0.192            6.2           24      35.7
r000010  102,756    2       1260   0.192            6.2           22      32.9
r000011   94,706    2       1230   0.193            5.7           24      34.8
r000012   87,852    2       1230   0.193            5.3           24      35.7
r000013   74,026    2       1260   0.192            4.5           24      35.9
r000014   73,688    2       1260   0.192            4.4           22      32.6
r000015   71,364    2       1260   0.190            4.3           25      35.0
r000016   65,301    3       1260   0.191            4.0           24      35.6
r000017   61,577    3       1260   0.191            3.7           22      30.1
r000018   59,833    3       1290   0.188            3.7           19      21.5
r000019   53,673    3       1260   0.192            3.2           24      35.8
r000020   49,302    3       1245   0.194            2.9           14      32.1
r000021   48,299    3       1245   0.194            2.9           13      28.1
r000022   48,186    3       1260   0.192            2.9           13      23.2
r000023   42,753    3       1260   0.192            2.6           14      21.3
r000024   42,572    3       1260   0.190            2.6           14      21.7
r000025   40,226    3       1260   0.190            2.4           13      23.8
r000026   38,794    3       1245   0.192            2.3           13      23.6
r000027   37,806    3       1260   0.190            2.3           14      22.2
r000028   36,220    3       1260   0.190            2.2           14      21.4
r000029   31,976    3       1260   0.190            1.9           13      23.7
r000030   31,120    3       1275   0.189            1.9           13      25.8
r000031   30,511    3       1275   0.189            1.9           13      21.1
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.192, min 0.186; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.192, min 0.186; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.192, min 0.186; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 32, recrawl docs/s p50 0.192, min 0.186; 14 days need 0.008 for the smallest

A3: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    2       1200   0.200           14.0           24      37.0
r000001  241,920    2       1230   0.198           14.1           22      33.1
r000002  241,920    2       1230   0.193           14.5           18      19.7
r000003  136,817    2       1230   0.195            8.1           20      24.8
r000004  132,118    2       1200   0.199            7.7           24      34.9
r000005  123,268    2       1230   0.198            7.2           24      35.9
r000006  118,294    2       1230   0.197            7.0           25      36.0
r000007  108,059    2       1200   0.199            6.3           25      35.6
r000008  105,959    2       1230   0.199            6.2           23      35.0
r000009  103,050    2       1200   0.198            6.0           24      35.9
r000010  102,756    2       1200   0.199            6.0           23      32.1
r000011   94,706    2       1200   0.199            5.5           23      35.1
r000012   87,852    2       1230   0.197            5.2           24      35.6
r000013   74,026    2       1230   0.197            4.3           24      35.9
r000014   73,688    2       1230   0.197            4.3           23      32.1
r000015   71,364    2       1230   0.197            4.2           24      35.1
r000016   65,301    3       1215   0.199            3.8           25      35.7
r000017   61,577    3       1215   0.199            3.6           21      29.5
r000018   59,833    3       1260   0.192            3.6           20      22.5
r000019   53,673    3       1215   0.199            3.1           24      35.9
r000020   49,302    3       1230   0.198            2.9           14      32.2
r000021   48,299    3       1230   0.198            2.8           14      30.1
r000022   48,186    3       1230   0.198            2.8           14      28.1
r000023   42,753    3       1230   0.197            2.5           14      23.0
r000024   42,572    3       1215   0.197            2.5           14      23.2
r000025   40,226    3       1215   0.198            2.4           14      29.3
r000026   38,794    3       1215   0.198            2.3           14      29.0
r000027   37,806    3       1215   0.198            2.2           14      25.2
r000028   36,220    3       1230   0.195            2.1           14      22.2
r000029   31,976    3       1230   0.196            1.9           14      28.3
r000030   31,120    3       1230   0.196            1.8           14      28.8
r000031   30,511    3       1230   0.196            1.8           14      21.9
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.198, min 0.193; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.198, min 0.193; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.198, min 0.192; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 32, recrawl docs/s p50 0.198, min 0.192; 14 days need 0.008 for the smallest

A4: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    2       1200   0.198           14.1           24      35.9
r000001  241,920    2       1200   0.198           14.1           23      32.3
r000002  241,920    2       1260   0.190           14.7           17      19.3
r000003  136,817    2       1200   0.195            8.1           18      23.0
r000004  132,118    2       1200   0.199            7.7           24      33.9
r000005  123,268    2       1230   0.196            7.3           25      35.8
r000006  118,294    2       1200   0.199            6.9           24      35.5
r000007  108,059    2       1200   0.198            6.3           24      35.3
r000008  105,959    2       1170   0.201            6.1           23      35.0
r000009  103,050    2       1200   0.200            6.0           24      35.3
r000010  102,756    2       1200   0.198            6.0           23      31.8
r000011   94,706    2       1200   0.199            5.5           23      34.5
r000012   87,852    2       1200   0.198            5.1           24      35.3
r000013   74,026    2       1230   0.196            4.4           24      35.4
r000014   73,688    2       1200   0.199            4.3           23      31.8
r000015   71,364    2       1230   0.196            4.2           24      34.9
r000016   65,301    3       1230   0.196            3.9           24      35.2
r000017   61,577    3       1230   0.196            3.6           21      29.4
r000018   59,833    3       1260   0.190            3.6           20      23.0
r000019   53,673    3       1200   0.199            3.1           24      35.2
r000020   49,302    3       1200   0.200            2.9           14      31.7
r000021   48,299    3       1215   0.199            2.8           14      29.6
r000022   48,186    3       1215   0.199            2.8           14      28.0
r000023   42,753    3       1215   0.199            2.5           15      22.8
r000024   42,572    3       1215   0.197            2.5           14      24.4
r000025   40,226    3       1200   0.199            2.3           14      28.1
r000026   38,794    3       1200   0.199            2.3           15      28.2
r000027   37,806    3       1200   0.199            2.2           14      24.7
r000028   36,220    3       1215   0.197            2.1           14      22.2
r000029   31,976    3       1215   0.197            1.9           15      28.7
r000030   31,120    3       1215   0.197            1.8           15      29.1
r000031   30,511    3       1215   0.197            1.8           14      21.8
r000032   30,463    3       1215   0.195            1.8           15      20.8
r000033   29,786    3       1200   0.197            1.7           14      21.5
r000034   29,354    3       1215   0.196            1.7           14      28.7
r000035   25,926    3       1215   0.194            1.5           14      28.0
r000036   24,637    3       1215   0.194            1.5           15      21.0
r000037   24,370    3       1215   0.194            1.5           14      22.2
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.198, min 0.190; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.198, min 0.190; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.198, min 0.190; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 38, recrawl docs/s p50 0.198, min 0.190; 14 days need 0.008 for the smallest

A5: 59 min window
host  size     jobs  mean gap s  docs/s  days for size  busiest min  slot/min
r000000  241,920    1        nan   0.054           51.9           20      32.3
r000001  241,920    1        nan   0.047           59.4           19      28.4
r000002  241,920    0        nan   0.027          103.3           15      19.0
r000003  136,817    0        nan   0.032           50.1           18      21.8
r000004  132,118    1        nan   0.053           28.8           20      29.1
r000005  123,268    1        nan   0.054           26.6           20      30.7
r000006  118,294    1        nan   0.055           25.1           20      31.0
r000007  108,059    1        nan   0.053           23.4           21      29.9
r000008  105,959    1        nan   0.052           23.7           20      30.1
r000009  103,050    1        nan   0.053           22.7           20      30.0
r000010  102,756    1        nan   0.046           25.8           18      26.7
r000011   94,706    1        nan   0.050           22.1           21      29.5
r000012   87,852    1        nan   0.049           20.8           20      29.8
r000013   74,026    1        nan   0.047           18.1           20      29.4
r000014   73,688    1        nan   0.044           19.2           19      27.2
r000015   71,364    1        nan   0.047           17.6           20      29.3
r000016   65,301    1        nan   0.039           19.4           21      30.4
r000017   61,577    1        nan   0.039           18.3           19      26.0
r000018   59,833    0        nan   0.031           22.1           18      22.1
r000019   53,673    0        nan   0.032           19.1           21      30.6
r000020   49,302    3       1140   0.103            5.5           12      23.8
r000021   48,299    3       1140   0.103            5.4           10      22.3
r000022   48,186    3       1140   0.103            5.4           11      22.1
r000023   42,753    3       1140   0.103            4.8           10      21.4
r000024   42,572    3       1140   0.103            4.8           10      21.1
r000025   40,226    3       1140   0.103            4.5           12      22.8
r000026   38,794    3       1140   0.103            4.3           11      23.7
r000027   37,806    3       1140   0.103            4.2           11      22.7
r000028   36,220    3       1170   0.102            4.1           10      20.2
r000029   31,976    3       1170   0.102            3.6           11      23.8
r000030   31,120    3       1140   0.103            3.5           11      25.3
r000031   30,511    3       1140   0.103            3.4            9      20.2
r000032   30,463    3       1170   0.102            3.5           10      19.1
r000033   29,786    3       1170   0.102            3.4           10      18.8
r000034   29,354    4       1160   0.103            3.3           12      24.5
r000035   25,926    4       1160   0.103            2.9           12      24.4
r000036   24,637    3       1170   0.102            2.8           10      19.1
r000037   24,370    3       1170   0.102            2.8           11      20.8
r000038   24,306    4       1160   0.104            2.7           12      21.1
r000039   23,922    4       1160   0.103            2.7           12      24.4
hosts with >= 241,920 URLs: 3, recrawl docs/s p50 0.047, min 0.027; 14 days need 0.200 for the smallest
hosts with >= 100,000 URLs: 11, recrawl docs/s p50 0.053, min 0.027; 14 days need 0.083 for the smallest
hosts with >= 50,000 URLs: 20, recrawl docs/s p50 0.047, min 0.027; 14 days need 0.041 for the smallest
hosts with >= 10,000 URLs: 74, recrawl docs/s p50 0.102, min 0.027; 14 days need 0.008 for the smallest

```

## Interval at send time (C2)

```
A1: 59 min, interval 5.0 s, 32 recrawl hosts, 20651 gaps
gap p1 1.65 p5 2.28 p50 5.11 s; shorter than 4.5 s: 7621 (36.90%)
busiest 60 s, recrawl only: 15; normal + recrawl: 26
A2: 59 min, interval 5.0 s, 32 recrawl hosts, 21611 gaps
gap p1 1.64 p5 2.31 p50 5.11 s; shorter than 4.5 s: 7970 (36.88%)
busiest 60 s, recrawl only: 15; normal + recrawl: 25
A3: 59 min, interval 4.8 s, 32 recrawl hosts, 22325 gaps
gap p1 1.46 p5 2.26 p50 4.95 s; shorter than 4.3 s: 8854 (39.66%)
busiest 60 s, recrawl only: 15; normal + recrawl: 25
A4: 59 min, interval 4.8 s, 38 recrawl hosts, 26487 gaps
gap p1 1.15 p5 2.30 p50 4.98 s; shorter than 4.3 s: 10198 (38.50%)
busiest 60 s, recrawl only: 16; normal + recrawl: 25
A5: 59 min, interval 5.0 s, 96 recrawl hosts, 29667 gaps
gap p1 4.69 p5 6.11 p50 9.67 s; shorter than 4.5 s: 231 (0.78%)
busiest 60 s, recrawl only: 12; normal + recrawl: 21
```

## Hot hosts against slot headroom (scripts/diag3_hot.py, run against R0)

```
A1
host                       lat s  slot r   F0 u  r - u  normal  kept  recrawl  total/r
r000000.recrawl-sim.com     2.66    0.62  0.179   0.44   0.139   78%    0.183     52%
r000001.recrawl-sim.com     3.05    0.54  0.177   0.36   0.107   61%    0.184     54%
r000002.recrawl-sim.com     5.14    0.32  0.168   0.15   0.038   23%    0.179     68%
r000003.recrawl-sim.com     4.37    0.38  0.176   0.20   0.068   39%    0.179     66%
r000004.recrawl-sim.com     2.82    0.58  0.178   0.40   0.131   73%    0.183     54%
r000005.recrawl-sim.com     2.75    0.60  0.180   0.42   0.143   80%    0.184     55%
r000006.recrawl-sim.com     2.75    0.60  0.179   0.42   0.141   79%    0.183     54%
r000007.recrawl-sim.com     2.79    0.59  0.177   0.41   0.140   79%    0.183     55%
r000008.recrawl-sim.com     2.80    0.59  0.178   0.41   0.138   77%    0.183     55%
r000009.recrawl-sim.com     2.76    0.59  0.177   0.42   0.140   80%    0.183     54%
r000010.recrawl-sim.com     3.11    0.53  0.177   0.35   0.106   60%    0.183     55%
r000011.recrawl-sim.com     2.80    0.59  0.178   0.41   0.134   75%    0.183     54%
r000012.recrawl-sim.com     2.78    0.59  0.178   0.41   0.136   77%    0.182     54%
r000013.recrawl-sim.com     2.75    0.60  0.177   0.42   0.139   79%    0.183     54%
r000014.recrawl-sim.com     3.01    0.54  0.176   0.37   0.111   63%    0.183     54%
r000015.recrawl-sim.com     2.83    0.58  0.175   0.41   0.130   74%    0.183     54%
r000016.recrawl-sim.com     2.73    0.60  0.175   0.43   0.140   80%    0.184     54%
r000017.recrawl-sim.com     3.34    0.49  0.174   0.32   0.094   54%    0.184     57%
r000018.recrawl-sim.com     4.56    0.36  0.173   0.19   0.059   34%    0.181     67%
r000019.recrawl-sim.com     2.75    0.60  0.175   0.42   0.143   82%    0.181     54%

A3
host                       lat s  slot r   F0 u  r - u  normal  kept  recrawl  total/r
r000000.recrawl-sim.com     2.66    0.62  0.179   0.44   0.132   74%    0.200     54%
r000001.recrawl-sim.com     2.97    0.55  0.177   0.38   0.105   59%    0.198     55%
r000002.recrawl-sim.com     5.00    0.33  0.168   0.16   0.032   19%    0.193     69%
r000003.recrawl-sim.com     3.98    0.41  0.176   0.24   0.058   33%    0.195     62%
r000004.recrawl-sim.com     2.82    0.58  0.178   0.40   0.121   68%    0.199     55%
r000005.recrawl-sim.com     2.75    0.60  0.180   0.42   0.131   73%    0.198     55%
r000006.recrawl-sim.com     2.74    0.60  0.179   0.42   0.132   74%    0.197     55%
r000007.recrawl-sim.com     2.77    0.59  0.177   0.42   0.125   70%    0.199     55%
r000008.recrawl-sim.com     2.81    0.58  0.178   0.41   0.127   72%    0.199     56%
r000009.recrawl-sim.com     2.74    0.60  0.177   0.42   0.131   74%    0.198     55%
r000010.recrawl-sim.com     3.07    0.54  0.177   0.36   0.094   53%    0.199     55%
r000011.recrawl-sim.com     2.81    0.59  0.178   0.41   0.120   67%    0.199     54%
r000012.recrawl-sim.com     2.77    0.59  0.178   0.42   0.128   72%    0.197     55%
r000013.recrawl-sim.com     2.75    0.60  0.177   0.42   0.127   72%    0.197     54%
r000014.recrawl-sim.com     3.07    0.53  0.176   0.36   0.103   58%    0.197     56%
r000015.recrawl-sim.com     2.81    0.59  0.175   0.41   0.123   70%    0.197     55%
r000016.recrawl-sim.com     2.76    0.59  0.175   0.42   0.128   73%    0.199     55%
r000017.recrawl-sim.com     3.34    0.49  0.174   0.32   0.085   49%    0.199     58%
r000018.recrawl-sim.com     4.38    0.38  0.173   0.20   0.055   31%    0.192     66%
r000019.recrawl-sim.com     2.74    0.60  0.175   0.42   0.128   73%    0.199     55%

```

## Send gaps, hot hosts apart (scripts/diag5_gapsplit.py)

```
A1 hot: 12919 gaps, mean 5.48, p1 1.81, p5 2.26, p50 5.01 s, under 4.50 s: 41.2%
A1 other: 7751 gaps, mean 5.48, p1 0.50, p5 2.38, p50 5.21 s, under 4.50 s: 29.7%
A2 hot: 13528 gaps, mean 5.23, p1 1.71, p5 2.28, p50 5.02 s, under 4.50 s: 41.6%
A2 other: 8106 gaps, mean 5.20, p1 1.42, p5 2.39, p50 5.21 s, under 4.50 s: 28.9%
A3 hot: 13984 gaps, mean 5.06, p1 1.65, p5 2.25, p50 4.80 s, under 4.32 s: 44.1%
A3 other: 8365 gaps, mean 5.04, p1 0.76, p5 2.29, p50 5.10 s, under 4.32 s: 32.4%
A4 hot: 13945 gaps, mean 5.07, p1 1.57, p5 2.26, p50 4.82 s, under 4.32 s: 43.4%
A4 other: 12548 gaps, mean 5.02, p1 0.70, p5 2.36, p50 5.09 s, under 4.32 s: 33.0%
```

## Process 03 after 14 days at the measured big-host rates (scripts/left14.py)

```
rate 0.200: 241,920 docs per host in 14 d, 0 hosts cannot finish, 0 URLs left = 0.00%; 241,920 needs 14.0 d
rate 0.198: 239,501 docs per host in 14 d, 3 hosts cannot finish, 7,258 URLs left = 0.16%; 241,920 needs 14.1 d
rate 0.193: 233,453 docs per host in 14 d, 3 hosts cannot finish, 25,402 URLs left = 0.56%; 241,920 needs 14.5 d
rate 0.192: 232,243 docs per host in 14 d, 3 hosts cannot finish, 29,030 URLs left = 0.63%; 241,920 needs 14.6 d
rate 0.183: 221,357 docs per host in 14 d, 3 hosts cannot finish, 61,690 URLs left = 1.35%; 241,920 needs 15.3 d
rate 0.047: 56,851 docs per host in 14 d, 19 hosts cannot finish, 1,164,255 URLs left = 25.45%; 241,920 needs 59.6 d
```
