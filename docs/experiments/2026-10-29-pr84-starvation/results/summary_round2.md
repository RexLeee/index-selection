Round 2, 2026-10-29, `scripts/analyze.py` output. All runs: KEEP=1, 240 s normal only, then 360 s
with jobs of 2 hosts x 120 docs, policy `weighted`. Run names:

- `N0`: no jobs (normal-only control), final code.
- `W50d`, `W30d`, `W50d_dead`, `W50d_cpu`, `W50d_light`: final code `6d23789`, share 50 or 30.
- `capA_dead`: capA patch on `cc965006`, policy `balanced` (control for dead hosts).
- `W50`, `W30`, `W50_dead`, `W50_cpu`, `W50_light`: with the occupancy guard (removed).
- `W50b`, `W30b`, `W50b_dead`, `W50b_cpu`: with MAX_QUEUED 32 (removed).
- `W50c`, `W30c`: with MAX_QUEUED 256, which never binds (equal to the final code but for refill timing).
- `_dead`: MOCK_DEAD_EVERY=10. `_cpu`: MOCK_LINKS=900 MOCK_TEXT_REPEAT=12000. `_light`: 4 jobs, not 48.

| run | normal before | normal after | normal kept | manual after | total after/before | manual early → late |
|---|---|---|---|---|---|---|
| N0 | 50.7 ± 1.4 | 46.4 ± 0.6 | 91% | 0.0 ± 0.0 | 91% | 0.0 → 0.0 |
| W50d | 50.2 ± 1.5 | 28.5 ± 0.7 | 57% | 28.4 ± 0.7 | 113% | 27.5 → 29.4 |
| W30d | 50.1 ± 1.7 | 40.0 ± 0.4 | 80% | 17.1 ± 0.1 | 114% | 17.2 → 17.1 |
| W50d_dead | 48.7 ± 1.0 | 27.2 ± 0.5 | 56% | 27.1 ± 0.6 | 111% | 27.1 → 27.1 |
| capA_dead | 46.9 ± 0.8 | 27.2 ± 0.5 | 58% | 27.2 ± 0.5 | 116% | 27.2 → 27.1 |
| W50d_cpu | 24.5 ± 3.5 | 21.2 ± 0.6 | 87% | 21.2 ± 0.6 | 173% | 21.5 → 20.9 |
| W50d_light | 50.4 ± 1.5 | 44.7 ± 0.4 | 89% | 2.7 ± 0.3 | 94% | 3.2 → 2.2 |
| W50 | 50.7 ± 1.3 | 31.1 ± 0.3 | 61% | 17.3 ± 0.7 | 96% | 18.5 → 16.1 |
| W30 | 49.6 ± 1.7 | 38.3 ± 0.5 | 77% | 11.2 ± 0.3 | 100% | 11.8 → 10.4 |
| W50_dead | 48.3 ± 0.9 | 27.6 ± 0.4 | 57% | 17.7 ± 0.8 | 94% | 19.5 → 16.1 |
| W50_cpu | 25.2 ± 3.1 | 23.7 ± 1.2 | 94% | 13.4 ± 0.8 | 147% | 13.6 → 13.3 |
| W50_light | 50.8 ± 1.3 | 44.4 ± 0.4 | 87% | 2.7 ± 0.2 | 93% | 3.1 → 2.4 |
| W50b | 51.0 ± 1.4 | 22.6 ± 0.5 | 44% | 22.5 ± 0.4 | 88% | 22.8 → 22.2 |
| W30b | 50.0 ± 1.3 | 35.0 ± 0.5 | 70% | 15.0 ± 0.3 | 100% | 15.3 → 14.7 |
| W50b_dead | 48.4 ± 0.7 | 21.2 ± 0.5 | 44% | 21.2 ± 0.5 | 88% | 21.8 → 20.7 |
| W50b_cpu | 24.5 ± 3.5 | 20.8 ± 1.6 | 85% | 12.5 ± 2.1 | 136% | 10.0 → 15.2 |
| W50c | 50.7 ± 1.6 | 28.3 ± 0.6 | 56% | 28.3 ± 0.7 | 112% | 27.7 → 28.9 |
| W30c | 50.0 ± 1.5 | 40.2 ± 0.3 | 80% | 17.2 ± 0.2 | 115% | 17.4 → 17.2 |

| run | inflight | transferring | waiting in slots | waiting share | manual hosts/min | manual gap p50 s | busiest host-min | jobs done | first job s | results | cpu % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| N0 | 252 | 189 | 59 | 23% | 0.0 | - | 55 | 0/0 | - | 0 | 30 |
| W50d | 255 | 223 | 32 | 13% | 98.0 | 2.46 | 64 | 14/62 | 333 | 10147 | 30 |
| W30d | 254 | 229 | 24 | 9% | 85.2 | 3.92 | 54 | 0/48 | - | 6165 | 31 |
| W50d_dead | 253 | 227 | 25 | 10% | 99.0 | 2.54 | 48 | 14/62 | 333 | 9529 | 30 |
| capA_dead | 254 | 229 | 26 | 10% | 99.4 | 2.52 | 55 | 14/62 | 334 | 9523 | 28 |
| W50d_cpu | 256 | 201 | 48 | 19% | 80.5 | 3.42 | 34 | 0/48 | - | 7635 | 92 |
| W50d_light | 252 | 192 | 60 | 24% | 7.3 | 2.35 | 62 | 3/7 | 265 | 1048 | 29 |
| W50 | 254 | 200 | 54 | 21% | 58.0 | 2.62 | 47 | 0/48 | - | 6509 | 31 |
| W30 | 254 | 205 | 49 | 19% | 43.8 | 2.94 | 56 | 0/48 | - | 4070 | 31 |
| W50_dead | 253 | 202 | 51 | 20% | 61.6 | 2.52 | 44 | 0/48 | - | 6348 | 31 |
| W50_cpu | 255 | 193 | 58 | 23% | 60.3 | 4.34 | 26 | 0/48 | - | 4806 | 96 |
| W50_light | 252 | 193 | 59 | 23% | 7.8 | 2.40 | 58 | 3/7 | 269 | 1038 | 30 |
| W50b | 254 | 186 | 69 | 27% | 79.0 | 2.40 | 51 | 0/48 | - | 8162 | 28 |
| W30b | 253 | 205 | 48 | 19% | 49.8 | 2.55 | 54 | 0/48 | - | 5462 | 31 |
| W50b_dead | 254 | 192 | 60 | 24% | 76.4 | 2.43 | 46 | 0/48 | - | 7516 | 26 |
| W50b_cpu | 254 | 191 | 60 | 24% | 60.5 | 4.11 | 36 | 0/48 | - | 4263 | 95 |
| W50c | 255 | 222 | 33 | 13% | 90.5 | 2.45 | 59 | 17/63 | 332 | 10267 | 27 |
| W30c | 254 | 230 | 24 | 9% | 86.0 | 3.80 | 54 | 0/48 | - | 6177 | 31 |

| run | manual share of requests | lowest 30 s normal/s | lowest 30 s manual/s | job done p50 s | job done max s | oldest open job s |
|---|---|---|---|---|---|---|
| N0 | 0.0% | 43.3 | 0.0 | - | - | - |
| W50d | 50.0% | 26.9 | 27.0 | 335 | 340 | 357 |
| W30d | 30.0% | 38.9 | 16.8 | - | - | 358 |
| W50d_dead | 50.0% | 25.7 | 25.6 | 337 | 353 | 357 |
| capA_dead | 50.0% | 26.1 | 26.0 | 337 | 356 | 357 |
| W50d_cpu | 49.9% | 20.4 | 20.4 | - | - | 361 |
| W50d_light | 5.7% | 43.3 | 1.9 | 334 | 339 | 362 |
| W50 | 35.8% | 30.2 | 15.6 | - | - | 362 |
| W30 | 22.6% | 37.2 | 10.0 | - | - | 357 |
| W50_dead | 39.0% | 26.5 | 15.0 | - | - | 358 |
| W50_cpu | 36.2% | 21.8 | 11.9 | - | - | 362 |
| W50_light | 5.8% | 43.1 | 2.1 | 323 | 357 | 357 |
| W50b | 50.0% | 21.4 | 21.3 | - | - | 357 |
| W30b | 29.9% | 33.1 | 14.2 | - | - | 362 |
| W50b_dead | 50.0% | 19.4 | 19.7 | - | - | 357 |
| W50b_cpu | 37.6% | 18.3 | 6.2 | - | - | 362 |
| W50c | 50.0% | 25.4 | 24.6 | 336 | 365 | 363 |
| W30c | 30.0% | 39.5 | 17.0 | - | - | 357 |

N0: before jobs inflight 256, transferring 178, waiting 76, slots 146, cpu 32 %; after: slots 186, manual hosts touched 0
W50d: before jobs inflight 256, transferring 175, waiting 81, slots 146, cpu 32 %; after: slots 253, manual hosts touched 124
W30d: before jobs inflight 256, transferring 176, waiting 80, slots 146, cpu 32 %; after: slots 265, manual hosts touched 95
W50d_dead: before jobs inflight 256, transferring 192, waiting 62, slots 146, cpu 32 %; after: slots 252, manual hosts touched 121
capA_dead: before jobs inflight 256, transferring 180, waiting 76, slots 146, cpu 29 %; after: slots 252, manual hosts touched 120
W50d_cpu: before jobs inflight 256, transferring 141, waiting 115, slots 144, cpu 98 %; after: slots 231, manual hosts touched 96
W50d_light: before jobs inflight 256, transferring 174, waiting 82, slots 146, cpu 29 %; after: slots 190, manual hosts touched 14
W50: before jobs inflight 256, transferring 174, waiting 82, slots 146, cpu 39 %; after: slots 227, manual hosts touched 93
W30: before jobs inflight 256, transferring 170, waiting 85, slots 146, cpu 30 %; after: slots 212, manual hosts touched 60
W50_dead: before jobs inflight 256, transferring 181, waiting 75, slots 146, cpu 34 %; after: slots 226, manual hosts touched 96
W50_cpu: before jobs inflight 256, transferring 137, waiting 118, slots 144, cpu 97 %; after: slots 211, manual hosts touched 78
W50_light: before jobs inflight 256, transferring 175, waiting 80, slots 146, cpu 32 %; after: slots 190, manual hosts touched 12
W50b: before jobs inflight 256, transferring 179, waiting 76, slots 146, cpu 32 %; after: slots 233, manual hosts touched 96
W30b: before jobs inflight 256, transferring 173, waiting 81, slots 146, cpu 30 %; after: slots 225, manual hosts touched 89
W50b_dead: before jobs inflight 256, transferring 184, waiting 72, slots 146, cpu 31 %; after: slots 233, manual hosts touched 96
W50b_cpu: before jobs inflight 256, transferring 141, waiting 113, slots 144, cpu 98 %; after: slots 203, manual hosts touched 83
W50c: before jobs inflight 256, transferring 174, waiting 82, slots 146, cpu 29 %; after: slots 251, manual hosts touched 125
W30c: before jobs inflight 256, transferring 170, waiting 86, slots 146, cpu 32 %; after: slots 264, manual hosts touched 96
