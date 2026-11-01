# Midterm Report — Index Infra: Select, Rank and Fetch the Index

**Author:** RexLeee (Index Infra team). **Period:** 2026-09-29 to 2026-11-01. **Times:** UTC.

**Status on 11-01 13:00 (checked read-only on Host1, Host2 and GitHub):**

| Workstream | State | Evidence |
|---|---|---|
| (1) Index selection | **Live.** The Metric reads my list on selectdb `:5445`. The live list is model run 2 (128M URLs, live since 10-14 03:45). The target is now **64M**, and `select-full` selects 64M. The 64M list is not published yet | `model_runs` status `live`; Metric cron `--select_db_url …:5445`; WSE #8–#18 merged |
| (2) Full-text index | **Implemented and tested, not live.** No OpenSearch container runs yet | PR #22 open |
| (3) Recrawl | **Implemented and tested, not live.** One 200-URL trial ran on the production API. No recrawl job runs now | Host2: 0 pending and 0 result jobs; crawler `a289368` with no share setting |

**Diagrams:** the designs and flows behind §2 are on one interactive page: [https://share.onorca.dev/a/s4h7TZpOZL_5](https://share.onorca.dev/a/s4h7TZpOZL_5).

**Number labels.** Each number has one label:
- **official**: the course Metric wrote it to metricdb.
- **backtest**: an offline replay on past golden batches.
- **local**: my own measurement, simulation or projection. The course does not count it.

## TL;DR

1. **Index coverage is counted for the first time.** The Metric showed `indexed_rate = 0` up to batch 19. On batch 20, the first batch after my model was frozen, it shows **24.75 % (head) and 23.02 % (random)**. That is 95 % and 91 % of what the crawler had fetched. On 10-27 the target became 64M, to fit the crawl bandwidth. On the same batch, 64M keeps 23.69 % / 21.99 % and halves the recrawl load. *(official; live)*
2. **The list becomes searchable.** I chose OpenSearch by a rule written before the benchmark, and built a full-text index for it. The index puts 79–85 % of the fetched golden pages in their keyword's top 10. This projects a ranked rate of **0.190 (head) and 0.176 (random)**, where the Metric writes 0 today. *(local, upper bound; implemented and tested; not live)*
3. **The index gets its page text.** I designed and built the recrawl. First it was a job-based client of the crawler team's job API, which cut my requests to them from 10 to 1. Now it is a frontier-based client: the crawler schedules the whole list. It keeps text only: 0.35–0.4 TB instead of 5–6 TB of raw HTML for 128M pages. A production trial fetched 185 of 200 URLs, end to end. *(implemented and tested; not live)*

---

## 1. Business Impact

### 1.1 The goal and how the course measures it

The course search engine holds a list of URLs (the index). The course first asked for K = 128M. On 10-27 TY Huang asked for 64M, so that the crawler can refetch the whole list. Twice a month (the 1st and 16th), the Metric builds a golden set from Google Trends queries. The head set uses the most frequent queries. The random set draws from the same pool at random. For each golden URL, the Metric checks four flags: discovered, crawled, indexed (in our list) and ranked (in the top 10 of our search).

A golden URL reaches a user only if three things happen. My three workstreams cover these three steps, one each:

```text
ranked rate  H/N  =  I/N            ×  F/I                 ×  H/F
                     on the list       page text fetched      in the keyword's top 10
                     (1) selection     (3) recrawl            (2) full-text index
```

Each letter counts golden URLs of one batch, and each set holds the next one:

| Symbol | Counts | Set by |
|---|---|---|
| **N** | All distinct golden URLs of the batch; a URL golden for several keywords counts once | The Metric, from Google Trends keywords |
| **I** | Those of N that are on our list | (1) selection |
| **F** | Those of I whose page text was fetched, so the page is in the full-text index | (3) recrawl |
| **H** | Those of F that reach the top 10 for at least one of their keywords | (2) full-text index |

- **I/N** is the official `indexed_rate`: the share of golden URLs on the list.
- **F/I** is the share of listed golden URLs with page text. A page without text is not in the index, so search cannot find it.
- **H/F** is the share of those pages that search ranks in the top 10. It measures ranking quality.
- **H/N** is the ranked rate. I and F cancel in the product. Because H ⊆ F ⊆ I, the ranked rate can never exceed the indexed rate.

Worked example (batch 20, head, test half of the keywords, 47k-document index):

| Level | Count | Ratio |
|---|---|---|
| N | 3,636 | — |
| I | 965 | I/N = 965 ÷ 3,636 = 0.265 |
| F | 870 | F/I = 870 ÷ 965 = 0.902 |
| H | 691 | H/F = 691 ÷ 870 = 0.794 |
| Product | — | 0.265 × 0.902 × 0.794 = **0.190** = 691 ÷ 3,636 |

Random gives N 3,974 → I 924 → F 821 → H 698, so H/N = 0.176. Three caveats apply:
- This I/N covers only the test half of the keywords. The official value over all keywords is 24.75 %.
- F came from a fetch on my Mac, because the recrawl is not live.
- H came from a 47k-page test index, so H/F is an upper bound.

Source: `docs/experiments/2026-10-23-text-index/results/review/official.json`, run `bg44k/tuned`.

I present the three workstreams in the order the work moved: selection first, then the search that uses the list, then the page text that the search needs.

- **Lagging metric (official):** `indexed_rate` = I/N, written by the Metric.
- **Leading metric (local):** H/N, my projection with the official definitions. The Metric still writes `ranked_rate = 0`, because no ranking step exists in it yet.

### 1.2 Impact summary

| Area | Before | After | Label | State |
|---|---|---|---|---|
| Official indexed rate (head / random) | 0 / 0 (batches 18, 19) | **24.75 % / 23.02 %** (batch 20, 128M list) | official | Live |
| Share of the crawled ceiling | — | **95 % / 91 %** (ceiling 26.03 % / 25.21 %) | official | Live |
| Ranking quality vs V1's features (K = 128M) | 20.52 % / 16.18 % | **22.36 % / 17.82 %** (+1.84 / +1.64 pt) | backtest | Live |
| List publish | TRUNCATE + INSERT, blocks readers | New table + rename; **1.2 ms** lock wait, no reader blocked | local | Live |
| List size for the recrawl | 128M: 106 req/s for 14 days | **64M: 52.9 req/s**, for −1.06 / −1.07 pt on batch 20 | local | Implemented, not published |
| Ranked rate (head / random) | 0 (Metric has no ranking) | **0.190 / 0.176**, upper bound | local | Tested, not live (#22) |
| Index size for 128M pages | 0.24 TB (body with positions) | **0.13 TB** (same top 10) | local | Tested, not live (#22) |
| Recrawl storage for 128M pages | 5–6 TB (raw HTML as WARC) | **0.35–0.4 TB** text; measured 2,545 B/doc → 0.30–0.33 TB | local | Tested, not live |
| Change requests to the crawler team | 10 (C1–C10) | **1** (WebCrawler #85) | — | Done |
| Normal crawl while recrawl runs | **9 %** of its rate (`manual_first`) | **91 %**; recrawl gets 7.64 req/s (2× the need) | local sim | Tested, not live (#106) |

### 1.3 Impact (1): index coverage counted for the first time

- **Before.** The deployed Metric read V1's selectdb `:5444` and hardcoded `indexed = False`, so every batch scored 0. V1 itself listed 32M URLs, and 68 % of them had never been crawled. A page with no content cannot be retrieved.
- **After.** My list holds exactly 128M crawled URLs. PR #9 pointed the Metric at it. Batch 20 (10-16) was the first batch created after the model was frozen (10-11), so it is a true forward test.
- **Result.** 24.75 % (1,814 of 7,329) head and 23.02 % (1,849 of 8,031) random golden URLs are indexed. The crawler had fetched only 26.03 % and 25.21 %, so the list keeps 95 % and 91 % of the reachable ceiling.
- **64M.** The same model ranks the list; `select-full` now keeps the top 64M by score. On batch 20, 64M keeps 23.69 % (head) and 21.99 % (random), against 24.75 % and 23.06 % for 128M with the same counting. Half the list costs about 1 pt of coverage and halves the recrawl.
- **Business insight.** Better ranking can add at most 1.2–2.2 pt. A search over the list, and page text for that search, can add much more. This finding moved my work from selection to search and recrawl.

### 1.4 Impact (2): the full-text index makes the list searchable

- **Problem.** The Metric's ranked flag needs a search over our pages. No search index existed.
- **Result (local, batch 20, test half of the keywords, 47k-document index).**
  - Of the fetched golden pages, **79.4 % (head) and 85.0 % (random)** reach their keyword's top 10.
  - Ranked rate H/N = **0.190 [0.173, 0.207]** head and **0.176 [0.160, 0.191]** random.
  - Query tuning raised the share of present golden pairs in the top 10 from 0.761 to **0.811** (+0.050 [+0.040, +0.061]).
  - The index for 128M pages shrank from 0.24 TB to **0.13 TB**, with the same top 10 on all 1,948 keywords. The 64M list needs about half.
- **Why it is an upper bound.** H/F falls about 1.5 pt each time the index doubles. 64M is about 10 doublings above this test index, so I give no full-size number.
- **What it needs next.** The index can rank only pages with text. The crawler keeps no text, so the index needs the recrawl.

### 1.5 Impact (3): the recrawl gives the index its page text

- **Problem.** The list has URLs but no text. The crawler deletes raw HTML after 8 hours and never fetches a page twice. crawlerdb keeps only the title. 55 % of the 128M list was last fetched before June 2026.
- **Result.**
  - **Cross-team cost:** my first design asked the crawler team for 10 changes. As a client of their job API (WebCrawler #84), I needed 1.
  - **Disk:** keeping text only needs 0.35–0.4 TB for 128M pages, 12–17× less than WARC, on a host with about 2 TB free then.
  - **Production trial (11-01):** 200 URLs to process 03 → 185 fetched in 89.4 s, 0 URLs rewritten, 90 of 100 golden URLs fetched. The full path API → text Parquet → index → eval ran with no error. Process 03's normal rate stayed inside its baseline.
- **Fairness evidence.** My simulations on Host3 (same CPU and disk as Host2) showed a risk. The deployed default `manual_first` leaves the normal crawl at 9 % of its rate. With a weighted 20 % share (my WebCrawler PR #106), the normal crawl keeps 91 %, and the recrawl gets 7.64 req/s. Process 03 needs 3.78 req/s to recrawl its part of the 64M list in 14 days.

### 1.6 Counterfactual and attribution

| Step | Without my work | What I own | What others own |
|---|---|---|---|
| (1) Selection, I/N | Metric shows 0; V1's list is 68 % uncrawled | Model, full pipeline, publish path, Metric switch (#9) | Crawl coverage (crawler team); Metric code (classmates, #3, #7) |
| (2) Search, H/F | Ranked = 0 by definition | Engine choice, index, query, evaluation | A ranked step in the Metric (TA) |
| (3) Recrawl, F/I | No page text: crawlerdb holds only titles | Recrawl design, both clients, fairness experiments, #106, the crawler-side frontier branch | Job API #84 (crawler team); review and merge |

---

## 2. Technical Description

**Interactive diagrams:** [Index Infra midterm: three business impacts and the designs behind them](https://share.onorca.dev/a/s4h7TZpOZL_5). The page shows each impact's design framework, flow and motivation in 7 diagrams. Source: `docs/reports/2026-midterm-technical.html`.

### 2.1 Architecture

```text
crawlerdb (YugabyteDB since 10-30)     metricdb (golden batches)
   │ pull-full (read-only, guarded)        │
   ▼                                       ▼
isel on Host2: features → model → score-full → select-full (exact top K: 128M live, 64M next)
   │ publish → swap → verify-live
   ▼
selectdb :5445 (Host1) ◄── Metric cron, 1st / 16th ──► indexed_rate              (1)
   │ live run's manifest
   ▼
recrawl: isel push ──► crawler bulk crawl API ──► result segments ──► isel pull    (3)
                                                     │ text Parquet
                                                     ▼ text-index-build
                                       OpenSearch alias isel-text → top 10        (2)
```

### 2.2 Index selection (1): from V1 to V2

**Context.** The Metric checks whether each golden URL's string is in our list. That is recall over URLs, and recall adds up over URLs. So the best list is the K crawled URLs with the highest P(URL is in the next golden batch). The task becomes: score 1.66B crawled pages.

**What V1 did, and why I replaced it.** V1 (WSE PR #4) was the team's first selector.

| Area | V1 | Effect |
|---|---|---|
| Model | LightGBM on 56 URL-string features and 1 domain frequency; learning rate 0.5, `best_iteration = 1` | In practice one tree; about 17 distinct scores, 41.6 % of rows tied |
| Training data | Negatives: 1M random rows, uncrawled pages included. Random train/test split. Domain frequency from the same batch's golden | Leakage, and no test of the future |
| Output | 32M URLs, where the course asked for 128M | 68 % never crawled, so they cannot be retrieved |
| Publish | TRUNCATE + INSERT in one transaction; a 5–7 h full scan of crawlerdb per run | Readers blocked during each publish |

**V2 formulation.**
- **Labels in time.** For batch b, the candidate pool holds pages fetched before `as_of_b`, at least 30 h before the batch was created. Features use only data from before `as_of_b`. A positive is a pool page whose URL is in golden(b).
- **Leakage control.** The crawler writes golden labels back into its own columns, for example `source`, `url_score`, `domain_score`, `parent_page_score`, `first_seen` and the 90-day counters. A `FORBIDDEN` list holds 15 such columns, and the code raises `LeakageError` if one is used.
- **Three leaks fixed on 10-11.** Positives now count themselves in their domain statistics. A salted per-row hash replaces `TABLESAMPLE SYSTEM`, which took whole disk pages. One half of the sample builds the domain statistics, and the other half trains.

**The 45 features (five groups).**

| Group | Features | Source | Why it is in the model |
|---|---|---|---|
| URL shape (14) | `url_len`, `path_depth`, `is_root`, `has_query`, `path_date`, `path_marker`, `ext_class`, `sub_www`, `host_labels`, … | URL string | Cannot leak. URL form marks entry pages: 15.6 % of golden URLs are site roots, against 0.2 % of crawled pages |
| Fetch facts (12) | `title_null`, `title_len`, `has_anchor`, `discovery_source_type`, `robots_bits`, `is_redirect`, `has_json_ld`, `hreflang_count`, `fetch_age_days`, … | The crawler's fetch record | Fixed at fetch time, before the batch, so it cannot leak. Static-rank work (fRank) found such page facts useful |
| Links (5) | `log_inlinks`, `log_ext`, `has_ext`, `rel_inlinks`, `rel_ext` | Approximate inlink counts | Having external inlinks matters more than their number. `rel_*` compares a page with its own domain |
| URL history (4) | `hist_decayed`, `hist_head`, `hist_random`, `hist_days_since` | Earlier golden batches, 90-day half-life | A past golden URL comes back in the next batch 0.6–2.7 % of the time, over 400× the base rate |
| Domain (10) | `lift_tld`, `lift_dkey`, `lift_host`, `lift_hp`, `dem_*`, `log_size_*`, `dkey_ext_share`, `history_batches` | Golden rates per domain level, from the aggregate half | The domain prior was the strongest signal in my earlier weight study (−4.38 pt head on batch 18 without it). Empirical-Bayes shrinks small hosts toward their parent: host prefix → host → domain key → TLD → global |

**Why two levels.** One additive score cannot tell "which domain" from "which page in it". So the domain level sets the prior with empirical-Bayes rates, and LightGBM learns the contrasts between pages of the same domain.

**Signals I left out.**
- `url_score`: it is the crawler's golden-discovery output, so it leaks. In the rule-based study, removing it raised recall by 0.5–2.4 pt.
- `later_unlabeled`: it failed the rule I wrote before the test (every CI lower bound below 0).
- Page text and anchor text: the crawler keeps neither.

**Evidence for the choices (backtest, K = 128M, Appendix A).**
- Domain features add 1.16–1.45 pt on head (paired 95 % CI, `lgbm:full` against `lgbm:no_domain`).
- The 45 features beat V1's features, retrained the same way, by +1.84 / +1.64 pt.
- Most of the gain over V1 comes from the training method. In the 10-09 backtest, V1's features with the new training beat random by +17.78 pt (head).
- LightGBM beats logistic regression by 0.50 / 0.47 pt after the leak fix. I recorded no CI for this gap, so I claim only that LightGBM is not worse.
- Settings are fixed in `isel.toml`: learning rate 0.05, 63 leaves, 200 rows per leaf, `deterministic`, seed 7. I did no hyperparameter search.

**Pipeline: each step, its technology and why.**

| Step | Technology | Why |
|---|---|---|
| `pull-golden`, `pull-full` | Postgres `COPY … CSV`, 4 capped sessions, pyarrow → Parquet; SQL gate, read-only session, `ReadGuard` | crawlerdb is shared with the crawler and the Metric. One streaming read, then all work runs on files |
| `derive-*`, `build-dataset` | DuckDB on Parquet; sums in `DECIMAL(38,18)` | Joins and aggregates over 1.66B rows in one process with memory caps; exact sums make reruns bit-identical |
| `backtest`, `train` | LightGBM; scikit-learn baselines | Time-split replays on past batches decide features and model |
| `score-full` | One export file at a time; score floor τ; z check of the kept count | About 10 GB peak. A wrong kept count stops the run |
| `select-full` | Exact top K, URL-hash tie order, Parquet manifest with sha256 | The same list on every rerun; the manifest also feeds the recrawl |
| `migrate-selectdb` | Alembic + SQLAlchemy Core, `alembic check` | Schema changes have a history and a check |
| `publish`, `swap` | `COPY FREEZE` into a new table; rename in one transaction, 5 s lock timeout; refused in the Metric window | Readers are never blocked |
| `verify-live` | The Metric's own SQL, as the `select` role | Proves that the Metric sees the new list |

Since crawlerdb moved to YugabyteDB on 10-30, the reader splits each table by `yb_hash_code(url)` ranges, because YugabyteDB has no `ctid` (PR #23). For 64M, `select-full` applies no per-hostname cap: the crawler decides how fast each host goes.

**Result.**
- The full read took 1 h 48 min for 1,657,046,602 rows (V2's first design estimated about 14 h). Host1 IO pressure stayed at its baseline.
- `score-full` took 148.6 min on 32 CPUs. It kept 147,021,239 rows against a predicted 147,181,326 (z = −1.42, limit 6).
- The z check found a leak after the first full scoring. The leaky model kept 38.14M more rows above τ than predicted, mostly small-domain pages with a +17 logit boost. I fixed the three leaks, refroze the model, and published before batch 20.
- The pipeline went upstream as 11 stacked PRs in NTU-CSIE5376/WebSearchEngine (WSE), #8–#18, all merged by 10-21.

### 2.3 Full-text index (2)

**Context.** The Metric's ranked flag needs the top 10 for each golden keyword. The keywords come from 10 regions, including Chinese, Japanese and Hindi. The index must fit beside the crawler, and the list changes every two weeks.

**Action: how I chose the engine.**
1. **Requirements.** R1 BM25 over title, description and body for 128M pages. R2 many languages. R3 no load on the crawler or crawlerdb. R4 a list swap every two weeks. R5 exact lookup by URL or `doc_id`. R6 low query volume. R7 one student can run it in Docker.
2. **Screen 9 engines against R1–R7.**

   | Engine | Result |
   |---|---|
   | Typesense | Out: the index lives in RAM, 1.1–1.65 TB for 128M pages |
   | ParadeDB `pg_search` | Out: text and index do not fit in `:5445`'s 601 GB, on the crawlerdb host |
   | Meilisearch | Out: rule-based ranking, not BM25 |
   | Vespa | Out: needs a config server and content nodes |
   | Anserini / Pyserini | Out: offline only; kept as the reference for BM25 numbers |
   | Tantivy (library) | Out: I would write the service layer |
   | Manticore | Third choice: only vendor benchmarks |
   | Quickwit, OpenSearch | Finalists |

   Elasticsearch is not listed. It shares OpenSearch's core, and OpenSearch is Apache-2.0.
3. **Trial on 2,399 real pages (10-22).** Quickwit was smaller (2.1 against 3.7 KB per page) and lighter (100 MB against 2.6 GB RAM). OpenSearch found more pages by their own title (299 against 296 of 300) and every page by URL (2,302 against 2,261).
4. **A rule written before the benchmark (10-23 05:40).** OpenSearch wins only if all three hold; otherwise Quickwit.
   - **Q:** the 95 % interval of weighted nDCG@10 (OpenSearch-ICU minus Quickwit) has a lower bound ≥ −0.02. Languages are weighted by batch 20's keywords (English 0.745).
   - **S:** the projected size fits Host2's disk budget (0.82 TB).
   - **M:** Host2 can give a 16–31 GB heap.
5. **Result on NanoMIRACL** (8 languages × 200 questions). Weighted nDCG@10 is +0.010 [+0.001, +0.017] for OpenSearch. Japanese decides it, +0.260: Quickwit keeps a kana run as one token, so `オリンピックの` never matches `オリンピック`. Chinese favours Quickwit by 0.032. Q, S (0.23 TB) and M all hold.

**Why OpenSearch fits this job.**
- **Tokenization.** ICU segments CJK words with a dictionary. Quickwit's official image has no multilingual tokenizer.
- **The two-week swap.** OpenSearch moves an alias in one atomic call, and deletes by `doc_id`. Quickwit's splits cannot change.
- **Comparable numbers.** Its BM25 is Lucene's, the same as Anserini's, so my results compare with IR baselines.
- **Project health.** OpenSearch ships monthly under the Linux Foundation. Quickwit had no release from 2024 until 0.9.0 in 10-2026.

**Action: how the index is built (`text-index-build`).**

| Step | Technology | Why |
|---|---|---|
| 1. Lock | `fcntl.flock` on a work-dir file | Two builds cannot delete each other's index |
| 2. Read the newest text | DuckDB, two passes over the text Parquet: pick each doc's newest row, then read only the winners | 128M bodies are never sorted |
| 3. Mapping | `icu_tokenizer` + `icu_folding` on `title`, `description`, `body`; `url.text` holds the URL's words; `body` with `index_options: freqs`; `body` not in `_source`; `doc_id` as doc values only | BM25 uses term counts, not positions. Same top 10 on all 1,948 keywords; 1,072 instead of 2,165 bytes per doc |
| 4. Space check | Free space ≥ 2 × the projected index above the flood stage (95 % full, where OpenSearch blocks writes) | A full disk would also stop the crawler |
| 5. Bulk load | New index with 0 replicas, refresh off, async translog; 8 MiB `_bulk` requests, 2 in flight; resend on 429 or 503 | Nothing searches it yet, and the Parquet is the source of truth |
| 6. Count and flush | Doc count must equal the winner rows; flush, then wait for retention leases | Without the wait the index was 1.8× larger, because recovery data survived the merge |
| 7. Merge | Second space check; force merge to 1 segment per shard, with a refresh at every poll | Host2 runs one merge thread (`thread_pool.force_merge.size: 1`), so the peak grows by one shard, not one index |
| 8. Seal and swap | Serve settings back; `blocks.write`; one `_aliases` call | Readers switch in one step. Any failure before the move deletes the new index |
| 9. Record | A manifest with sizes and timings | Every build can be audited |

**Action: query and tuning.**
- **Query.** `multi_match` `best_fields` over `title^2`, `description`, `body` and `url.text`, top 10, ties broken by `doc_id`. The keyword is never parsed as query syntax.
- **Protocol.** The sha256 parity of each keyword puts it in a dev half (988) or a test half (960). I chose settings on dev only and compared once on test. One deviation: I added `best_fields` after I saw the dev numbers.

**Result.** The ranked numbers and sizes are in §1.4 and Appendix C. Per-shard merging cut the rebuild peak from 0.38–0.57 TB to 0.27–0.46 TB for 128M pages. PR #22 passes 286 tests, plus 1 integration test against the real OpenSearch 3.9.0 image. The trial job's 185 pages went through `text-index-build` and `text-index-eval` with no error.

### 2.4 Recrawl (3)

**Context.** The TA set the goal on 10-25: the whole list in two weeks. The crawler deletes raw HTML after 8 hours. Host disks are shared with the crawler's HTML buffer and, since 10-30, YugabyteDB nodes.

**Action: how I chose the design.** I compared each option on six points: no writes to crawlerdb; one rate limiter per host, shared with the crawler; few changes for the crawler team; fits the disk; every page's result traceable; a clear owner for the pace.

| Option | Result | Reason |
|---|---|---|
| A separate recrawler (the starter crawler) | Rejected | It shares no rate state with the production crawler |
| Set `should_crawl` back to TRUE, or use #84's `crawl-requests` | Rejected | Writes crawlerdb; HTML is deleted after 8 h; a URL cannot be traced after submit |
| My own lane in the production crawler, with WARC output (C1–C10) | Replaced (10-14 → 10-18) | 10 changes for the crawler team; 5–6 TB of WARC does not fit |
| **Job-based client** of #84's job API | Built, merged (PR #17) | Jobs, TTL, DELETE and FIFO already existed, so 1 request was left |
| **Frontier-based client** + crawler-side frontier | Current design (11-01) | See below |

**Why frontier-based.** In job-based mode, isel scheduled the hosts. A host had one open job of 240 URLs, and its next job waited until the whole job ended. In simulation this capped a big host at about 240 ÷ job lifetime (p50 1,175 s), and lower when the planner reached the host late (0.083–0.088 docs/s in round 7). Client-side scheduling also needed 64M rows of state in selectdb, the Postgres that the Metric reads. In frontier-based mode, the crawler sees each host's whole backlog and sets the pace with its own AutoThrottle.

**Who decides what.**

| Decision | Owner |
|---|---|
| Which pages, in which order | isel: the live run's manifest, by rank |
| When a page is due again (retry, robots, gone) | isel: outcome files |
| When a host is fetched and how fast | crawler: per-slot queues, #106's per-host cap and 20 % share, AutoThrottle |
| How much the crawler holds for isel | crawler: 503 + `Retry-After`, a quota on unacknowledged results |

**Action: each step, its technology and why.**

| Step | Technology | Why |
|---|---|---|
| `recrawl-push` | HTTP POST of up to 10,000 URLs, each `{id: doc_id, url, priority: rank}` | The crawler can rewrite a URL string, so results map back by `id` only |
| Crawler frontier | Per-crawler SQLite frontier fed from an inbox; dispatch by longest backlog first, then #106's per-host cap; a 429 or 503 blocks the slot | The crawler already owns robots, slots and AutoThrottle, so the recrawl uses one rate limiter per host |
| `recrawl-pull` | Long-poll of result segments; acknowledge each segment by name | 16 crawler processes write in parallel, so no single cursor exists |
| Text extraction | Resiliparse: title, description, main body, language | The index needs text, not HTML |
| Storage | zstd Parquet, partitioned by fetch date | 12–17× less than WARC |
| State | Files only, one writer each, written to a temp name and renamed; reads drop duplicate rows | A crash or rerun gives the same files, with no database to run |

The frontier-based plan runs the client, the text and the index on Host3, which has the same CPU and disk as Host2. Host3 also holds a YugabyteDB node, so the client stops when free space falls below 800 GB.

**Action: fairness experiments.** I replayed the crawler with production settings against a mock web server whose latency fits Host2's logs. Seven rounds (1–2 on my Mac, 3–7 on Host3) tested the dispatch policy, the per-host cap and the pacing. The data changed two designs. A fixed 4.8 s per-host interval held fast hosts below their slot rate, so it was dropped. A `waited` term only moved which host started first, so it was dropped too.

**Design evolution.**

```text
10-14  own crawler lane: 10 change requests to the crawler team (C1–C10), raw HTML as WARC
10-18  job-based client of the crawler's job API (#84) → 1 request left
10-21  follow #84's per-crawler partitions; job completion comes from the API's GET
10-29  #84 merged and deployed; simulations find manual_first starves the normal crawl → PR #106
10-30  list target 64M (asked by TY Huang on 10-27)
11-01  frontier-based client: the crawler schedules the whole backlog; isel pushes URLs and pulls results
```

**Result.**
- The job-based client passed 242 unit and 35 integration tests in PR #17. The production trial (§1.5) confirmed the API contract and the text size.
- The frontier-based client ran end to end against the real API and crawler from my branch. The latest isel branch passes 317 unit tests (run on 11-01).
- The trial found two data-quality gaps: an anti-bot page counted as fetched, and JavaScript pages with an empty body.

### 2.5 Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| Select only crawled pages, ranked by P(golden) | Uncrawled pages have no content; V1 spent 68 % of its list on them | Coverage is capped by the crawl |
| As-of pools and a `FORBIDDEN` column list | The crawler writes golden labels back into its columns | The crawler's own `url_score` cannot be used |
| Two levels: empirical-Bayes domain prior + LightGBM | One additive score cannot separate domain from page | Two sample halves, so each half sees fewer rows |
| New table + rename swap, Alembic schema | TRUNCATE + INSERT blocks readers; schema changes need history | Up to three copies of the list on disk |
| 64M with no per-hostname cap | Fits the crawl bandwidth; isel cannot know a host's real pace before the crawl | −1.06 / −1.07 pt on batch 20; big hosts finish at the crawler's pace |
| OpenSearch + ICU by a pre-written rule | Better CJK quality; atomic alias swap; rule fixed before data | 31 GB JVM heap; larger than Quickwit |
| Body without word positions | BM25 needs counts only; same top 10 | Phrase queries on the body fail |
| Keep extracted text, not raw HTML | 12–17× less disk | A better parser cannot reprocess old pages |
| Recrawl scheduling in the crawler (frontier-based) | Jobs made a big host wait for each whole job; the crawler sees the whole backlog | Needs a crawler change that is not merged yet |

---

## 3. Limits, Risks and Next Steps

**Limits.**
- Coverage bounds everything: only about 25–26 % of batch-20 golden URLs were crawled.
- The ranked rate is local. Its F/I came from a fetch on my Mac, not the crawler. Its index held 47k pages.
- Batch 20 is the only forward test so far. Batch 21 (11-01) is still being built, and it will measure the 128M list.

**Risks and asks.**

| Risk | Impact | Ask | Owner |
|---|---|---|---|
| The 64M list needs a new crawl read, and crawlerdb moved to YugabyteDB | The 64M list cannot be built and published | Review PR #23; approve a full read on YugabyteDB | TA |
| The Metric writes `ranked = 0` | The index cannot count, even after deploy | Decide how the Metric reads ranked: a port to the index or flags from an isel job | TA |
| Production runs `manual_first` | A bulk recrawl would cut the normal crawl to about 9 % | Review and merge PR #106; set share 20 | Crawler team, TA |
| Text and index share a disk with YugabyteDB (Host3 in the plan) | Text + index + rebuild peak must fit | Confirm the host and a disk budget for text and index | TA |

**Next steps.**
1. Read crawlerdb on YugabyteDB, select 64M, publish and swap outside the Metric window. Each step needs approval.
2. Run the forward evaluation on batch 21 when the Metric finishes it.
3. Deploy the index after #22 is reviewed.
4. Open the frontier-based recrawl PRs on both repos; start the recrawl once #106 is deployed.
5. Mark anti-bot pages as retry, not fetched.

---

## Appendix

### A. Backtest at K = 128M (model M0', test batches 6, 7, 8, 18, 19 pooled)

| Ranker | Head | Random |
|---|---|---|
| `lgbm:full` (this work) | **22.36 %** (21.82–22.93) | **17.82 %** (17.31–18.31) |
| `logreg`, same features | 21.86 % | 17.35 % |
| `lgbm:no_domain` | 21.06 % | 16.50 % |
| `v1_features`, retrained on the same data | 20.52 % | 16.18 % |
| `domain_prior` only | 17.70 % | — |

Source: `WebSearchEngine/IndexSelectionV2/docs/BENCHMARK.md`; `docs/experiments/2026-10-11-hash-sample-refit.md` §4.1.

### B. List size on batch 20 (official counting method)

| List | Head | Random | 14-day recrawl need |
|---|---|---|---|
| 128M (live) | 24.75 % | 23.06 % | 106 req/s |
| 64M, no per-hostname cap (next) | 23.69 % | 21.99 % | 52.9 req/s |

The live 128M random rate is 23.02 % in metricdb; 23.06 % is the same list counted by my script. Source: `docs/experiments/2026-10-29-pr84-starvation/results/golden_coverage_64m.txt`; WSE `IndexSelectionV2/docs/RECRAWL.md` §3 (branch `idxsel/p14-recrawl-v2`); `docs/RECRAWL_DESIGN.md` §3.

### C. Ranked-rate decomposition (batch 20, test half, 47,162-doc index)

| Tag | I/N (on list) | F/I (text fetched) | H/F (top 10) | H/N, 95 % CI | H/N before query tuning |
|---|---|---|---|---|---|
| head | 0.265 | 0.902 | 0.794 | **0.190** [0.173, 0.207] | 0.175 |
| random | 0.233 | 0.889 | 0.850 | **0.176** [0.160, 0.191] | 0.167 |

I/N here is the test half of the keywords on the 128M list. Source: `docs/experiments/2026-10-23-text-index/results/review/official.json`; engine choice: `docs/experiments/2026-10-22-index-engine-selection.md`, WSE `IndexSelectionV2/docs/TEXT_INDEX.md` (branch `idxsel/p10-text-index`).

### D. Fairness simulation (Host3, process 03's part of the 64M list, 59 min each)

| Run | Normal crawl, % of control | Recrawl req/s | Recrawl share |
|---|---|---|---|
| #84 as deployed, `manual_first` | **9 %** | 18.65 | up to 90.1 % |
| PR #106, weighted 20, headroom 1 | **91 %** | **7.64** | 19.9 % |
| PR #106, weighted 20, 1 host in 10 answers 429 | 90 % | 9.11 | 20.0 % |

Source: NTU-CSIE5376/WebCrawler#106; `docs/experiments/2026-10-29-pr84-starvation/`.

### E. Links

| What | Where |
|---|---|
| Interactive diagrams for §2 | [https://share.onorca.dev/a/s4h7TZpOZL_5](https://share.onorca.dev/a/s4h7TZpOZL_5) (source `docs/reports/2026-midterm-technical.html`) |
| Selection pipeline (merged) | NTU-CSIE5376/WebSearchEngine #8–#18 |
| Full-text index, YugabyteDB reader (open) | NTU-CSIE5376/WebSearchEngine #22, #23 |
| Fair dispatch (open) | NTU-CSIE5376/WebCrawler #106 |
| 64M selection and frontier-based recrawl client (local) | RexLeee/WebSearchEngine branches `idxsel/p12-recrawl-pr84`, `idxsel/p14-recrawl-v2` |
| Crawler-side frontier (local) | RexLeee/WebCrawler branch `bulk-crawl-v2`; specs `specs/bulk-crawl-v2-*-spec.md` |
| Design | `docs/DESIGN.md`, `docs/RECRAWL_DESIGN.md`; WSE `IndexSelectionV2/docs/RECRAWL.md`, `TEXT_INDEX.md` |
| Experiments | `docs/experiments/` (backtest 10-09, refit 10-11, engine 10-22, text index 10-23, starvation 10-29, smoke 11-01) |
| Weekly reports | `docs/reports/2026-W41`, `W42`, `W43` |
