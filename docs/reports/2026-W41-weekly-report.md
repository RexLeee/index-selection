# Weekly Report — 2026/10/11 (10/05–10/11) — Index Selection

- **Role:** Index Infra (index selection). **Repository:** `index-selection` (package `isel`), 23 commits this week (`0f6593b` … `f17e9ac`).
- **Times:** UTC. **Status at 13:26:** the production run is in progress. The full table is exported, and `score-full` has scored 74 % of it.

## Brief Summary

**In one sentence:** The learned model M0 is frozen. Its 128M-URL list is being built from a full read of all 1.66B crawled rows, which finished in 1 h 48 min with no visible load on the database host. The model already reaches 94–99 % of the crawled ceiling, so the next large gain must come from crawl coverage, not ranking.

| Topic | This week's finding | Next step |
|---|---|---|
| Learned model (backtest) | Over 5 held-out batches at K = 128M, `lgbm:full` selects **22.49 %** (head) and **18.01 %** (random) of golden URLs. It beats every baseline; a model with V1's features gets 20.59 % / 16.23 % | Forward evaluation on batch 20 (10-16) |
| Ceiling | The model reaches **94–99 %** of the crawled ceiling on head (82–97 % on random) | Coverage is the bottleneck; share findings with the crawler side |
| Leakage and labels | The crawler writes golden back into `source`, `url_score`, `domain_score` and inlink counts. As-of pools and a `FORBIDDEN` list keep this out. Negatives are clean: 2×10⁻⁶–1.5×10⁻⁴ later became golden | Keep the current labels; `later_unlabeled` is not adopted |
| Reproducibility | DuckDB's parallel float sums made reruns train different trees. DECIMAL sums fixed it; reruns are now bit-identical | — |
| M0 | Frozen 10-11 06:37 (deadline 10-15 06:00): 7.31M rows, 18,695 positives, 447 trees | Never retrain this cycle |
| Full-table read | CSV + 4 capped connections: **1 h 48 min** for 1,657,046,602 rows (V2 estimated ~14 h). Host1 IO PSI avg **13.7 %**, at its baseline | Done |
| Deployment | Container on Host2. Fixed a Docker subnet collision and a wrong `select` password on our selectdb | Done |
| Scoring and selection | `score-full` at 189/256 shards (74 %), 0 errors | `select-full` → `publish` → `swap` before 10-15 20:00 |
| Official Metric | It still reads V1's `:5444`, and the deployed container hardcodes `indexed = False` | TA switch to `:5445` before 10-16 12:00 |

## 1. This week's progress

Before this week, V1 (PR #4) ran in production with known problems, and my rule-based V2 had been validated on one shard (10-04). V2 used the crawler's `url_score`, which leaks golden, and its weights were tuned in-sample. This week I replaced it and broke the work into 5 questions:

1. Which method and which evaluation rules fit this task?
2. How can we read crawlerdb without disturbing production?
3. Does a learned model beat simple rankings on real data, out of time?
4. Can the model be frozen before batch 20, and reproduced?
5. Can the full 128M list be built and published before 10-15 20:00, safely?

---

### 1.1 Research: problem definition and method choice (10-05 – 10-06)

The official Metric checks whether each golden URL's **raw string** is in `selected_urls_current`. That is a recall over URLs, and recall adds up over URLs. So the best list is the K URLs with the highest P(URL ∈ next golden). The task reduces to scoring every crawled URL.

**Leakage sources in crawlerdb** (they hold the answer in a backtest):

| Column | How it leaks |
|---|---|
| `source`, `url_score` | `golden_inject.py` sets `source = 1`, `url_score = 3.0` on golden URLs older than 4 weeks |
| `url_score` | The crawler's own ranker is trained on golden batches 1–4 |
| `domain_score` | Set from golden domain counts |
| `parent_page_score` | Copies the parent page's `url_score` |
| `inlink_count_*` | Golden parent patrols add extra inlinks |
| `first_seen` | Reset on injection |

**What was wrong with V1 (PR #4):**

| Aspect | Problem |
|---|---|
| Model | LightGBM, learning rate 0.5, `best_iteration = 1`: in practice one tree. Test AUC fell from 0.957 to 0.79–0.82 |
| Data | Negatives are 1M random rows, including never-crawled pages. Random train/test split. The domain frequency uses golden from the same batch |
| Output | 32M URLs, 68 % never crawled. About 17 distinct scores, 41.6 % of rows tied |
| Operation | TRUNCATE + INSERT in one transaction, which blocks readers. A full crawlerdb scan of 5–7 h per run |

**Method choice:** a two-level golden-propensity model. A domain level uses empirical-Bayes-smoothed golden rates, and a page level uses LightGBM for contrasts inside a domain. The basis is fRank (WWW 2006) and query-view pruning (TOIS 2012). Domain greedy quotas and link centrality were kept as baselines only.

#### Observation

- Leakage is the largest risk in this task, larger than the choice of algorithm.
- Selecting never-crawled pages wastes slots: a page without content cannot be retrieved.

---

### 1.2 Read safety for crawlerdb (10-08, tuned 10-11)

Every crawlerdb statement goes through 3 layers:

1. **SQL gate:** it lexes each statement and lets only read-only forms through.
2. **`default_transaction_read_only = on`** on the server session.
3. **`ReadGuard`:** no reads on the 1st/16th 00:00–20:00 or every hour :58–:10. It pauses while more than 15 other backends wait on IO.

Before every read I also check Host1 IO PSI by hand (avg10 ≤ 35).

**Changes this week, based on measurements:**

| Change | Evidence |
|---|---|
| Removed the daily 02:50–04:40 window | No heavy cron or timer on either host then; `sar` 10-03..10-11 shows no extra load. A "45.8 % PSI" once blamed on this window came from the hourly Metric scan |
| Hourly window :55–:10 → :58–:10 | The Metric scan (`count(*)` over all 256 tables, 16 threads, 7–8 GB/s) runs from :00 to :07 |
| Readable time 60 % → 72 % | Simulated with the repo's own guard |

#### Observation

- The hourly Metric scan already reads every heap page. Data checksums are off, so hint bits write no WAL. A read-only scan adds little work on the server.

---

### 1.3 Real-data pull and label audit (10-09)

All reads ran from the Mac over the VPN through the 3 layers above. Nothing was written on the servers.

| Data | Result |
|---|---|
| Golden | 11 batches: 1–9, 18, 19 (10–17 are empty) |
| 1 % block sample | 16,480,360 rows, 1.2 GB, 256/256 shards; 18.6 MB/s effective; Host1 PSI 6.4–17.9 % during the read |
| Positive rows | All 11 batches; about 35 % of golden URL spellings were crawled (26.5 % for batch 19) |
| Validation | Re-fetched share 1.63 %; routing candidate rate 0.9996 |
| Snapshot S0 | 2026-10-09 05:39:22 |

**Label audit per batch** (`build-dataset`):

| Batch | as-of | Golden URLs | Pool (est.) | Positives | Canonical-only | `later_golden_share` |
|---|---|---|---|---|---|---|
| 1 | 02-26 12:25 | 13,633 | 6,075,300 | 119 | 0 | 1.5×10⁻⁴ |
| 4 | 04-15 06:00 | 14,821 | 134,459,700 | 802 | 6 | 3.8×10⁻⁵ |
| 6 | 05-21 18:00 | 14,918 | 804,662,000 | 2,642 | 15 | 9.6×10⁻⁶ |
| 8 | 06-04 08:00 | 14,431 | 1,129,897,400 | 3,611 | 35 | 5.0×10⁻⁶ |
| 9 | 06-11 08:00 | 7,139 | 1,148,639,800 | 1,589 | 15 | 3.7×10⁻⁶ |
| 18 | 09-16 00:09 | 7,671 | 1,223,463,100 | 1,946 | 22 | 2.2×10⁻⁶ |
| 19 | 09-30 06:00 | 14,432 | 1,506,221,300 | 2,944 | 27 | 0 |

(Excerpt; all 11 batches are in `docs/experiments/2026-10-09-real-data-backtest.md` §6.)

#### Observation

- The pool grew from 6.1M pages (batch 1) to 1.51B pages (batch 19).
- Canonical-only positives are about 1 % of positives, so spelling losses are small.
- Negatives that later became golden are 2×10⁻⁶–1.5×10⁻⁴ per batch. The negatives are clean.
- Batches 9 and 18 have about half the golden URLs. This matches issue #2: batch 9 has no head URLs, and batch 18 has 500 queries per set.

---

### 1.4 Backtest of the learned model (10-09, rerun 10-10)

**Time points.** Each batch b has an as-of time, `as_of_b = min(created_b − 30 h, S0)`.
- The pool is the pages with `last_fetch_ok < as_of_b`. Each URL is fetched only once, so the current crawl state rebuilds the pool as it was then.
- Golden-derived features use only earlier batches available before as-of, with a 90-day half-life.

**Labels.**
- A positive is a pooled page whose raw or canonical URL is in golden(b).
- A negative is any other pooled page, from a 10 % hash subsample of the 1 % sample, with a case-control offset.
- Head and random positives have equal total weight.

**Features (45, in 5 groups):**

| Group | Count | Examples | Level |
|---|---|---|---|
| URL string | 14 | `url_len`, `path_depth`, `is_root`, `path_marker` | page |
| Crawl metadata | 12 | `title_len`, `is_redirect`, `has_json_ld`, `fetch_age_days` | page |
| Inlinks | 5 | `log_inlinks`, `log_ext`, `rel_inlinks`, `rel_ext` (relative to the domain mean) | page |
| URL golden history | 4 | `hist_decayed`, `hist_head`, `hist_days_since` | page |
| Domain | 10 | `lift_tld` → `lift_dkey` → `lift_host` → `lift_hp` (hierarchical empirical Bayes), `dem_host` | domain |

**LightGBM:** binary objective, learning rate 0.05, 63 leaves, `min_data_in_leaf` 200, feature and bagging fraction 0.8, `lambda_l2` 1.0, `deterministic = true`. Early stopping runs on the latest training batch (patience 100), then the model is refit on all training batches. No hyperparameter search was done.

**Evaluation:**
- Test batch t trains only on batches available before as-of_t.
- K = 128M in the population maps to rank 1.28M in the 1 % sample, which is the cut-off.
- The metric is the indexed exact rate, the official raw-string rule.
- CIs use a paired bootstrap over queries with 1,000 resamples.
- Test batches: 6, 7, 8, 18, 19. Run `20261010T043436446958Z`, 58.5 min, peak 13.0 GB.

**Pooled result (K = 128M):**

| Model | head | diff vs `lgbm:full` (95 % CI) | random | diff vs `lgbm:full` (95 % CI) |
|---|---|---|---|---|
| **`lgbm:full`** | **22.49 %** (21.93–23.07) | — | **18.01 %** (17.50–18.51) | — |
| `later_unlabeled` | 22.48 % | −0.06 … +0.04 | 18.01 % | −0.05 … +0.05 |
| `lgbm:no_history` | 22.39 % | −0.16 … −0.04 | 17.99 % | −0.09 … +0.05 |
| `lgbm:no_inlinks` | 21.73 % | −0.86 … −0.64 | 17.38 % | −0.74 … −0.53 |
| `lgbm:no_domain` | 21.00 % | −1.66 … −1.34 | 16.55 % | −1.62 … −1.30 |
| `v1_features` | 20.59 % | −2.07 … −1.75 | 16.23 % | −1.96 … −1.61 |
| `logreg` | 19.86 % | −2.84 … −2.41 | 15.78 % | −2.43 … −2.03 |
| `domain_prior` | 17.72 % | −5.05 … −4.49 | 12.88 % | −5.41 … −4.84 |
| `inlinks` | 15.93 % | −6.89 … −6.26 | 13.27 % | −4.99 … −4.50 |
| `ext` | 14.91 % | −7.93 … −7.24 | 11.83 % | −6.48 … −5.92 |
| `random` | 2.81 % | −20.20 … −19.17 | 2.39 % | −16.09 … −15.16 |

**Per test batch (K = 128M).** The ceiling is the share of golden URLs crawled before as-of; no ranking can exceed it.

| Batch | Tag | Ceiling | `lgbm:full` | Share of ceiling | `v1_features` | `inlinks` | `random` |
|---|---|---|---|---|---|---|---|
| 6 | head | 18.40 % | 17.89 % | 97 % | 17.35 % | 14.38 % | 3.18 % |
| 6 | random | 17.11 % | 16.47 % | 96 % | 15.92 % | 13.65 % | 2.47 % |
| 7 | head | 20.16 % | 19.88 % | 99 % | 18.55 % | 14.06 % | 2.79 % |
| 7 | random | 19.34 % | 18.69 % | 97 % | 17.24 % | 14.11 % | 2.76 % |
| 8 | head | 25.70 % | 25.17 % | 98 % | 22.97 % | 18.31 % | 3.04 % |
| 8 | random | 24.19 % | 23.42 % | 97 % | 20.79 % | 17.12 % | 2.88 % |
| 18 | head | 28.41 % | 26.77 % | 94 % | 24.52 % | 17.45 % | 3.17 % |
| 18 | random | 22.41 % | 20.56 % | 92 % | 18.34 % | 14.73 % | 2.68 % |
| 19 | head | 26.09 % | 24.84 % | 95 % | 21.49 % | 16.19 % | 2.05 % |
| 19 | random | 14.94 % | 12.27 % | 82 % | 9.97 % | 7.52 % | 1.32 % |

**Effect of K (`lgbm:full`, head):** batch 8 gets 23.83 / 24.51 / 25.17 % at K = 32M / 64M / 128M, and batch 18 gets 24.19 / 25.64 / 26.77 %.

#### Observation

- `lgbm:full` beats every baseline, and every paired CI excludes 0.
- **Most of the gain over V1 comes from the training method**, not the features. With V1's features only, the time split, as-of features, correct negatives and real boosting already give 20.59 % head. The 45 features add 1.9 pt more.
- The feature gain grows over time: 0.5 pt (batch 6, head) and 3.3 pt (batch 19, head).
- **Domain features matter most:** removing them costs 1.3–1.7 pt on head, and removing inlinks costs 0.6–0.9 pt. URL history adds only about 0.1 pt. `rel_ext` has the highest gain (42–53 %), then `lift_hp` (8–16 %).
- Most hits sit in the top 32M URLs. Halving K to 64M costs only 0.2–1.1 pt on head.
- `later_unlabeled` drops only 0–22 negatives per test batch, and all its CIs include 0. Under the pre-registered rule (DESIGN §5) it is **not adopted**.
- **Batch 19 random is the outlier.** It has the lowest ceiling (14.94 %) and the lowest share of the ceiling (82 %). This matches issue #2, where batch 19 shifts toward news, Reddit and TikTok, and its same-keyword URL retention falls to 28 %.

---

### 1.5 Reproducibility fix (10-10)

- **Problem:** two runs on the same data trained different trees.
  - DuckDB's parallel DOUBLE sums differ by about 10⁻¹⁵ between runs.
  - Some `rel_*` values flipped between exactly 0 and ±4×10⁻¹⁶. LightGBM puts 0 in its own bin, so those rows landed in different bins.
- **Fix:** all 6 cross-row float sums now go through `DECIMAL(38, 18)`. DECIMAL sums use integer arithmetic, so the order of addition does not matter. Train frames are also sorted by (url, shard), because bagging samples rows by position.
- **Result:** two reruns give bit-identical `results.json`. The pooled headline moved by at most 0.06 pt (22.47 → 22.49 % head).

#### Observation

- One judgement changed: the URL-history ablation in head became significant, but only by about 0.1 pt.

---

### 1.6 M0 freeze (10-11)

| Item | Value |
|---|---|
| Frozen at | 2026-10-11 06:37:45 (deadline 10-15 06:00) |
| Snapshot S0 | 2026-10-09 05:39:22 |
| Training batches | 2–9, 18, 19; early stopping on batch 19 |
| Training rows | 7,310,996, of which 18,695 positives |
| Best iteration | 447 (`lgbm:full`); 345 for the `v1_features` comparison model |
| Cost | 4.5 min, peak 9.5 GB, 8 threads on the Mac |
| Code version | `12ea5d2` |
| Integrity | `model.txt` SHA-256 `fb88fc3c…`, verified again on Host2 |
| Sample selection | `select-sample` wrote 1,280,000 rows (K × 1 %) in 24.6 min |

#### Observation

- M0 predicts batch 20 with information up to S0 only. Batch 20 did not exist when M0 was frozen, so it is a true forward test.

---

### 1.7 Production pipeline and deployment (milestone 2, 10-11)

**Pipeline:** `pull-full` (export the crawled rows) → `score-full` (score with M0, keep score ≥ τ) → `select-full` (dedupe, exact top-K) → `publish` (new table on `:5445`) → `swap` (rename; previous list kept) → `verify-live` (Metric's own SQL).

| Design decision | Reason |
|---|---|
| τ = sample score at rank `round(K · p · 1.15)`; only rows ≥ τ are written | No need to hold 1.66B scores together. If fewer than K distinct URLs pass, the step fails and asks for a larger margin |
| Domain aggregates computed from the sample once; one export file loaded at a time | Features match training exactly. Peak memory is about 10 GB, against an estimated 63 GB for whole-shard loading |
| `publish` is the only code that writes; it refuses ports 5432/5433/5444 and any database with crawlerdb or metricdb tables | Protects the production databases |
| Swap by rename under `lock_timeout`; refused 11:30–20:00 on measurement days | Readers never see an empty table; Metric never reads a half-switched list |
| Container: multi-stage build, digest-pinned images, `read_only`, uid 1014, 64 GB / 16 CPU caps, `cpu_shares` 256, `oom_score_adj` 1000 | Reproducible, and other workloads win under contention |
| DSNs as Compose secret files, mode 600 | Env values show in `docker inspect`; V1's cron file leaked a DSN in plain text |

**Deployment checks on Host2:**

| Check | Result |
|---|---|
| Code (`git archive`) | 44 files, SHA-256 identical to the commit |
| Data and M0 (rsync, 1.15 GB) | 277 files identical; M0 checksum matches |
| `docker build --target test` | 237 passed, with no network |
| `check-config` in the container | `config_sha256` equals the local value |
| `pull-full --dry-run` | heap 3.427 TB, 256 shards, 6,512 chunks of 512 MiB; largest shard 85.1 GB |
| Parity test | Treating the real 1 % sample as a full export, `score-full` reproduces `select-sample` row for row (0 mismatches) |

**Bugs found and fixed this week:**

| Bug | Effect | Fix |
|---|---|---|
| A loaded LightGBM Booster ignores the training `num_threads` at `predict` | 144 OpenMP threads under a 16-CPU quota | Pass `num_threads` on every `predict`; predictions are bit-identical, so no retraining |
| `score-full` loaded a whole shard | Estimated 63 GB peak | Load one export file at a time; 10.0 GB measured |
| An empty export file | DuckDB typed the host key as INT32, and the join failed | Explicit string type |
| Pinned Docker subnet `172.20.251.0/24` | Another user's network took `172.20.0.0/16` on Host2 | `network_mode: bridge` |
| `select` role on our selectdb had V2's password | Metric could not log in after a switch | `init-selectdb` reset it; the 100k trial list did not change |

#### Observation

- Every output records its code version and config hash, so the published list can be traced to the exact model and code.

---

### 1.8 Full-table read (10-11)

**What changed from V2:**

| | V2 | isel |
|---|---|---|
| Format | Binary `COPY`, decoded row by row in Python | `COPY … (FORMAT CSV)`, parsed by pyarrow (C++), Parquet written by a separate thread |
| Connections | 1 | 4, sharing a queue of shards, with a combined cap of 2 GB/s of heap (about a quarter of the hourly scan) |
| Speed | 16 MB/s measured on one shard → about 14 h | see below |
| Output | — | Host2 disk, not crawlerdb's nearly full `/data` |

**Local benchmarks:** the CSV path took 2.33 s vs 6.17 s for 2M rows, with identical output. 4 connections took 6.4 s vs 11.8 s for 8M rows. An integration test checks the CSV export against the binary sample row by row, including URLs with quotes, commas, newlines, backslashes and CJK characters.

**Production run:**

| Item | Value |
|---|---|
| Window | 09:19:08 (test shard 100), then 09:31:07–11:19:53 |
| Rows | **1,657,046,602**, 256/256 shards, 0 failed, 0 errors |
| Output | 526 Parquet files, 56 GB |
| Speed | 352–366 MB/s for 1 connection; about 700–750 MB/s for 4 |
| Pauses | 09:56–10:10 and 10:56–11:10; the guard stops at :56 because a chunk may take 120 s |
| Rows changed during the read | 23.4M, by the crawler's `url_score` rescoring job; M0 uses neither column it touches. 3 shards grew, and the export read the new blocks |

**Load on Host1** (2-second samples; an auto-stop was armed for PSI > 35 three times in a row):

| Period | IO PSI avg10 | Disk await |
|---|---|---|
| Baseline before the read | about 15 % | 0.1–0.3 ms |
| While we read (2,505 samples) | avg 13.7 %, max 24.4 % | avg 0.25 ms |
| Hourly Metric scan (we were paused) | avg 30.5 %, max 51.6 % | up to 0.85 ms |

#### Observation

- The auto-stop never fired, and IO pressure while we read matched the baseline.
- Cluster WAL was 75 MB/s while we read, against a 42–70 MB/s baseline. WAL cannot pile up: archiving is off, there are no replication slots, and `pg_wal` stays at its 20 GB cap. Free space on `/data` stayed at 301–303 GB.
- 4 connections reached about 2× the speed of one connection, well below the 2 GB/s cap. The cap did not limit the read.

---

### 1.9 Full scoring and selection (in progress)

| Item | At 13:26 |
|---|---|
| Progress | 189/256 shards; 1,228M rows scored (74 %) |
| Kept (score ≥ τ) | 139.0M rows (11.3 %) |
| Speed | about 175k rows/s; 4–9 cores and 9–19 GB of the 16-core / 64 GB limit |
| Errors | 0 |
| Expected finish | about 14:10; `select-full` starts automatically after it |

#### Observation

- More rows pass τ than the 9 % first estimate, because τ is set with a 15 % margin. More candidates are safe: `select-full` still takes exactly K = 128,000,000 distinct URLs.

---

## 2. Main findings

### Finding 1 — The ranking is close to its ceiling

> **Index selection is now limited by crawl coverage, not by ranking.**

The model gets 94–99 % of what is reachable on head queries. Only 18–28 % of head golden URLs were crawled before the batch, and no list can select the rest.

---

### Finding 2 — Most of the gain over V1 comes from how the model is trained

With V1's features but the new training (time split, as-of features, correct negatives, real boosting), the model already reaches 20.59 % head, against 2.81 % for random. The 45 new features add 1.9 pt more.

---

### Finding 3 — Leakage control matters more than the algorithm

The crawler writes golden back into several columns. Without as-of pools and the `FORBIDDEN` list, a backtest would look good for the wrong reason and fail after release.

---

### Finding 4 — Domain-level signals are the strongest

Hierarchical domain rates (TLD → domain → host → host + first path segment) cost 1.3–1.7 pt when removed. The URL's own golden history adds only about 0.1 pt, because the same URL reappears in the next batch only 0.6–2.7 % of the time.

---

### Finding 5 — A full-table read can be fast and still light on crawlerdb

CSV parsing and 4 capped connections read 3.43 TB in 1 h 48 min. That is about 8× faster than V2's estimate, and Host1 IO pressure stayed at its baseline.

---

### Finding 6 — crawlerdb is not frozen

The crawler is stopped, but a rescoring job updates 4,400–6,600 rows/s and keeps Host1 IO PSI near 15 %. It changes only `url_score` and `url_score_updated_at`, which M0 does not use. During a read, a moved row can be missed or read twice. Duplicates are removed by URL; missed rows are estimated at about 10⁴ (under 0.01 %).

---

## 3. Questions

### Q1. Will Metric read our list before batch 20?

The deployed `metric` container still points to V1's `:5444` and hardcodes `indexed = False`. Can the TA deploy Metric `main` (`b0bea75`) and point `--select_db_url` to `:5445` before 10-16 12:00? Pointing it back to `:5444` reverts the change. `verify-live` measures our list with the same SQL either way.

---

### Q2. Which number should be the headline for the instructor?

I propose the batch-20 forward evaluation as the headline, using the same SQL as Metric. The backtest would be supporting evidence: it uses inlink counts measured after the past batches, which may be optimistic by up to 0.6–0.9 pt.

---

### Q3. How should evaluation treat the golden data gaps?

Batch 9 has no head URLs, batches 10–17 are empty, batch 18 has half the queries, and batch 19 shifts toward news and social sites (issue #2). My backtest tests on batches 18 and 19. Should they be kept, weighted down, or reported separately?

---

### Q4. Should index selection and crawl scheduling work together?

Finding 1 says coverage is the bottleneck. Issue #2 finds that YouTube is a discovery problem and Wikipedia a scheduling problem. Would it help to share M0's scores with the crawler side, for example to rank pending URLs?

---

### Q5. Is a full read acceptable every cycle?

This read took 1 h 48 min with 4 capped connections. A two-week cycle would repeat it. Is that acceptable, or should later cycles read only changed rows?

---

### Q6. Is pseudo golden worth the effort?

Free SerpApi and Serper quotas could add about 2,400–3,200 positives (+8–10 %). The expected gain is only 0.1–0.3 pt (low confidence). Should I wait for the batch-20 result before spending the quota?

---

## 4. Discussion / Conclusion

This week turned index selection from a hand-weighted rule into a learned, frozen and reproducible model, with an evaluation that matches how the list is used.

- The model beats every baseline, and it is within 0.3–2.7 pt of the crawled ceiling in every test batch. Further gains need crawl coverage.
- The largest gain came from correct problem framing and training (as-of pools, clean labels, time split), not from new features.
- The production pipeline runs end to end on Host2. The full read finished quickly without visible load on crawlerdb. Scoring is 74 % done, and the list will be published before batch 20.

Two things are outside this work: whether Metric reads `:5445` in time (Q1), and how the golden data gaps should be handled in evaluation (Q3).

---

## 5. Next week's plan

1. **Finish this cycle's list**
   - Finish `score-full` and `select-full` (exactly 128,000,000 rows).
   - `publish` and `swap` on `:5445` before 10-15 20:00, then `verify-live --batch 19` as a sanity check.
2. **Confirm with the TA that Metric reads `:5445`** before 10-16 12:00.
3. **Batch 20 forward evaluation** (from 10-16 20:00)
   - `pull-golden`, `pull-positives --batches 20`, `forward-eval`, `verify-live --batch 20`.
   - Write a new experiment record and compare with the backtest.
4. **Close review items**
   - Compare the start and end snapshots of the full read.
   - Check the feature config in `score-full`, not only the snapshot time.
   - Check finished shards' files on resume.
   - Pass `GIT_SHA` as a compose build argument.
   - Improve `score-full` throughput (it uses 4–9 of 16 cores).
5. **Decide on pseudo golden** after the batch-20 result (Q6).
6. **Explore new features:** anchor-text and title content, and freshness or trend signals for head queries.

---

## Appendix A. Commits this week

| Commit | Subject |
|---|---|
| `0f6593b` | feat: add offline index selection research pipeline |
| `6ed2ecd` | feat(pg): guard reads and harden the SQL gate |
| `d05d031` | feat(pseudo): add Google Trends pseudo golden |
| `f6928f1` | fix(features): order train frames by url and shard |
| `32402d1` | feat(dataset): audit labels and add the later_unlabeled ablation |
| `9ab440a` | fix(features): sum cross-row floats as DECIMAL |
| `47d93f4` | docs: add the real-data backtest record |
| `3adf2d7` | docs: rerun the backtest with the float fix |
| `7ac04d4` | docs: note that the commits are pushed |
| `8d5972c` | feat(pg): read DSNs from <NAME>_FILE secret files |
| `a794a85` | perf(guard): narrow the read windows |
| `7eefeef` | feat(io): record the code version in run outputs |
| `9fef446` | fix(model): pass num_threads to LightGBM predict |
| `e641d1e` | feat(pull): export the full pool in ctid ranges |
| `2f5639e` | feat(score): add score-full and select-full |
| `3c377d3` | feat(publish): write lists to our selectdb |
| `165266f` | build: run isel as a container on Host2 |
| `12ea5d2` | docs: document milestone 2 |
| `8187898` | perf(pull): stream the full export as CSV |
| `1e5a1d0` | feat(pull): record export speed and cluster IO |
| `6293fb9` | perf(pull): export with parallel capped sessions |
| `c73c633` | docs: describe the CSV and parallel full export |
| `f17e9ac` | fix(deploy): run Host2 jobs on the default bridge |

## Appendix B. Where things are

| Artifact | Location |
|---|---|
| Design, runbook, safety rules | `docs/DESIGN.md` |
| Literature and system research | `docs/RESEARCH.md` |
| Backtest record (full tables for §1.3–1.5) | `docs/experiments/2026-10-09-real-data-backtest.md` |
| Backtest results | `work/backtests/20261010T043436446958Z/` (local, not in git) |
| M0 | `work/models/20261011T063745518690Z/` (Mac and Host2) |
| Full export | Host2 `~/isel/work/full/export/` |
| Scoring and selection outputs | Host2 `~/isel/work/full/scored/`, `~/isel/work/full/selection/run=<id>/` |
| Related report | WebSearchEngine issue #2 (crawler coverage, golden set inventory) |
