| run | normal before | normal after | normal kept | manual after | total after/before | manual early → late |
|---|---|---|---|---|---|---|
| manual_first | 50.0 ± 1.5 | 0.5 ± 0.1 | 1% | 6.1 ± 0.4 | 13% | 6.9 → 5.3 |
| balanced | 50.3 ± 1.6 | 6.6 ± 0.6 | 13% | 6.6 ± 0.6 | 26% | 8.0 → 5.1 |
| capA_manual_first | 51.2 ± 1.6 | 7.9 ± 4.3 | 15% | 28.3 ± 3.5 | 71% | 31.7 → 24.7 |
| capA_balanced | 50.7 ± 1.5 | 28.5 ± 0.6 | 56% | 27.4 ± 1.7 | 110% | 27.7 → 27.0 |
| capA_keep_manual_first | 50.7 ± 1.5 | 2.7 ± 0.7 | 5% | 31.4 ± 1.3 | 67% | 31.7 → 30.9 |
| capA_keep_balanced | 51.1 ± 1.8 | 28.3 ± 0.6 | 55% | 28.2 ± 0.7 | 111% | 27.5 → 28.9 |

| run | inflight | transferring | waiting in slots | waiting share | manual hosts/min | manual gap p50 s | busiest host-min | jobs done | first job s | results | cpu % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| manual_first | 256 | 29 | 226 | 88% | 20.6 | 2.22 | 56 | 0/48 | - | 2350 | 6 |
| balanced | 256 | 59 | 196 | 77% | 20.6 | 2.11 | 48 | 0/48 | - | 2423 | 14 |
| capA_manual_first | 256 | 131 | 124 | 48% | 83.6 | 2.23 | 57 | 19/48 | 286 | 10308 | 21 |
| capA_balanced | 255 | 222 | 33 | 13% | 82.8 | 2.52 | 51 | 16/48 | 334 | 9958 | 29 |
| capA_keep_manual_first | 256 | 132 | 124 | 48% | 96.8 | 2.17 | 78 | 18/66 | 284 | 11371 | 19 |
| capA_keep_balanced | 255 | 222 | 32 | 13% | 89.7 | 2.45 | 55 | 16/64 | 333 | 10253 | 29 |

manual_first: before jobs inflight 256, transferring 178, waiting 76, slots 146, cpu 32 %; after: slots 78, manual hosts touched 33
balanced: before jobs inflight 256, transferring 177, waiting 78, slots 146, cpu 36 %; after: slots 163, manual hosts touched 35
capA_manual_first: before jobs inflight 256, transferring 176, waiting 78, slots 146, cpu 32 %; after: slots 231, manual hosts touched 96
capA_balanced: before jobs inflight 256, transferring 180, waiting 76, slots 146, cpu 30 %; after: slots 252, manual hosts touched 96
capA_keep_manual_first: before jobs inflight 256, transferring 176, waiting 80, slots 146, cpu 30 %; after: slots 230, manual hosts touched 130
capA_keep_balanced: before jobs inflight 256, transferring 177, waiting 78, slots 146, cpu 32 %; after: slots 252, manual hosts touched 128
