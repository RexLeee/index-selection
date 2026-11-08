# Midterm Report — Index Infra: Select, Fetch and Rank the Index

**Author:** RexLeee (Index Infra team). **Period:** 2026-09-29 to 2026-11-08. **Times:** UTC.

**Status on 11-08 08:20, with GitHub and the ranking experiments updated to 11-08 13:00 (checked read-only on Host1, Host2 and GitHub):**

| Workstream | State | Evidence |
|---|---|---|
| (1) Index selection | **Live.** The Metric reads my list on selectdb `:5445`. The live list is model run 3: **64M URLs, live since 11-02 09:24**. It replaced the 128M list (run 2, live 10-14 to 11-02) | `model_runs` id 3 status `live`, 64,000,000 rows; Metric cron `--select_db_url …:5445`; WSE #8–#18 merged |
| (2) Recrawl | **Live since 11-02 09:54.** 53.06M of the 64M list are fetched (82.9 %); 44.26M have real page text (69.2 %) | selectdb `recrawl.doc`; Host2 containers `recrawl-plan`, `recrawl-archive` on image `46af81a`; WSE #25, #26, #37, #38 merged; #36 in review |
| (3) Full-text index | **Built on Host2, not read by the Metric.** 52.04M docs from the recrawl text. OpenSearch runs only during experiments, and it is stopped now | Build manifest `isel-text-20261107t044459193406z`; WSE #22 open; #30 draft, its default ranking changed on 11-08 (§2.4) |

**Diagrams:** the designs and flows behind §2 are on one interactive page: [https://share.onorca.dev/a/s4h7TZpOZL_5](https://share.onorca.dev/a/s4h7TZpOZL_5).

**Number labels.** Each number has one label:
- **official**: the course Metric wrote it to metricdb.
- **backtest**: an offline replay on past golden batches.
- **live**: measured on the production system (Host1, Host2) with my read-only queries.
- **local**: my own measurement or simulation outside production. The course does not count it.

## TL;DR

1. **Index coverage is counted for the first time.** The Metric showed `indexed_rate = 0` up to batch 19. On batch 20, the first batch after my model was frozen, it shows **24.75 % (head) and 23.02 % (random)**: 95 % and 91 % of what the crawler had fetched. The list shrank to 64M on 11-02 to fit the crawl bandwidth, for about −1 pt on batch 20. *(official; live)*
2. **The list has its page text.** My recrawl client drives the crawler team's bulk crawl API. In 6 days it fetched **53.06M of the 64M pages** and stored them as 74 GiB of text, not 5–6 TB of HTML. The client sets no rate of its own; the crawler paces every host. *(live)*
3. **The list is searchable, and search finds 3× more golden pages.** I built a 52M-doc OpenSearch index from that text. On batch 20, the share of golden URLs in their keyword's top 10 rose from **2.4–3.1 % to 7.6–7.7 %** (head / random), after a ranking change that I adopted by a rule written before the test. A second round on 11-08 found a better form on the dev half: the list rank now scales the text score instead of adding to it. It raises H/F on dev from 0.418 to 0.435, and it is now #30's default. Batch 22 (11-16) decides if it stays. The Metric still writes `ranked = 0`. *(live index; my evaluation; the second round is dev only)*

---

## 1. Business Impact

### 1.1 The goal and how the course measures it

The course search engine holds a list of URLs (the index). The course first asked for K = 128M. On 10-27 TY Huang asked for 64M, so that the crawler can refetch the whole list. Twice a month (the 1st and 16th), the Metric builds a golden set from Google Trends queries. The head set uses the most frequent queries. The random set draws from the same pool at random. For each golden URL, the Metric's table has four flags: discovered, crawled, indexed (in our list) and ranked (in the top 10 of our search). The Metric fills the first three. It writes ranked = 0, because it has no search step yet (WSE `Metric/Measure/CrawlerAllMetricMeasure.py`, `ranked_num: 0`).

A golden URL reaches a user only if three things happen. My three workstreams cover these three steps, one each:

```text
ranked rate  H/N  =  I/N            ×  F/I                 ×  H/F
                     on the list       page text fetched      in the keyword's top 10
                     (1) selection     (2) recrawl            (3) full-text index
```

Each letter counts golden URLs of one batch, and each set holds the next one:

| Symbol | Counts | Set by |
|---|---|---|
| **N** | All golden URLs of the batch | The Metric, from Google Trends keywords |
| **I** | Those of N that are on our list | (1) selection |
| **F** | Those of I whose page is in the full-text index | (2) recrawl |
| **H** | Those of F that reach the top 10 for their keyword. The Metric has no ranked rule yet, so this rule is mine | (3) full-text index |

- **I/N** is the official `indexed_rate`, counted over distinct URLs.
- **F/I** is the share of listed golden URLs with a page in the index. A page without text can be found only by its URL words.
- **H/F** is the share of those pages that search ranks in the top 10. It measures ranking quality. My evaluation counts it over (keyword, URL) pairs, because a URL can be golden for several keywords.
- **H/N** is the ranked rate. I report it over distinct URLs, as the Metric counts I/N. Because H ⊆ F ⊆ I, the ranked rate can never exceed the indexed rate.
- **Why a product of ratios.** Each ratio is a rate given the step before it, and one workstream owns it. So a change in one workstream moves one factor, and the factors show where the losses are. On batch 20 head: 0.237 on the list, about 0.80 of those in the index, and 0.391 of those in the top 10.

Worked example (batch 20, head, 52M-doc index, first-round ranking R0):

| Level | Count | Ratio |
|---|---|---|
| Golden pairs | 7,681 | — |
| Pairs whose page is in the index (F) | 1,470 | F/N = 0.191 |
| Pairs in the keyword's top 10 (H) | 575 | H/F = 0.391; H/N = 0.075 |
| Distinct URLs: N, H | 7,329; 557 | **H/N = 0.076** [0.069, 0.084] |

Random gives 8,045 pairs → 1,425 present → 619 hits, so H/F = 0.434; over distinct URLs, H/N = 619 ÷ 8,031 = **0.077** [0.069, 0.084]. On the list (I/N), batch 20 has 23.69 % (head) and 21.99 % (random) on the 64M list. So about 80 % of listed golden URLs have a page in the index (0.191 ÷ 0.237; pairs against distinct URLs, so approximate). Source: `text-index-eval` output `docs/experiments/2026-11-07-text-rank/results/eval/20261107T083811868183Z.json`.

I present the three workstreams in the order the data flows: the list, then its page text, then the search over that text.

- **Lagging metric (official):** `indexed_rate` = I/N, written by the Metric.
- **Leading metric (live index, my evaluation):** H/N over distinct URLs. N and I use the Metric's own counting and match its batch-20 totals (7,329 and 8,031). The Metric still writes `ranked_rate = 0`.

### 1.2 Impact summary

| Area | Before | After | Label | State |
|---|---|---|---|---|
| Official indexed rate (head / random) | 0 / 0 (batches 18, 19) | **24.75 % / 23.02 %** (batch 20, 128M list) | official | Live |
| Share of the crawled ceiling | — | **95 % / 91 %** (ceiling 26.03 % / 25.21 %) | official | Live |
| Ranking quality vs V1's features (K = 128M) | 20.52 % / 16.18 % | **22.36 % / 17.82 %** (+1.84 / +1.64 pt) | backtest | Live |
| List publish | TRUNCATE + INSERT, blocks readers | New table + rename; **1.2 ms** lock wait, no reader blocked | local | Live |
| List size | 128M: 106 req/s for 14 days | **64M: 52.9 req/s**, for −1.06 / −1.07 pt on batch 20 | local | Live since 11-02 |
| Pages with text | 0 (crawler keeps only titles) | **53.06M fetched, 44.26M real text** of 64M, in 6 days | live | Live |
| Recrawl storage | 5–6 TB for raw HTML of 128M pages | **74 GiB** of zstd text for 53M pages | live | Live |
| Ranked rate H/N (head / random, batch 20) | 0 (Metric has no ranking); 2.36 % / 3.11 % with plain BM25 | **7.60 % / 7.71 %** | live index | Built, not read by the Metric (#22, #30) |
| Ranking quality H/F (batch 20 / 21, all pairs) | 0.150 / 0.157 | **0.415 / 0.384** (first round, R0) | live index | Built (#30) |
| Ranking quality H/F, second round (batches 20 + 21, dev half) | 0.418 (R0) | **0.435** (scale over the best 4,500); 0.434 online | live index, dev only | #30 default; batch 22 decides |
| Index size | — | **43.56 GB** for 52.04M docs (837 B per doc) | live | Built |

### 1.3 Impact (1): index coverage counted for the first time

- **Before.** The deployed Metric read V1's selectdb `:5444` and hardcoded `indexed = False`, so every batch scored 0. V1 itself listed 32M URLs, and 68 % of them had never been crawled. A page with no content cannot be retrieved.
- **After.** My list holds exactly 128M crawled URLs. PR #9 pointed the Metric at it. Batch 20 (10-16) was the first batch created after the model was frozen (10-11), so it is a true forward test.
- **Result.** 24.75 % (1,814 of 7,329) head and 23.02 % (1,849 of 8,031) random golden URLs are indexed. The crawler had fetched only 26.03 % and 25.21 %, so the list keeps 95 % and 91 % of the reachable ceiling.
- **64M, live since 11-02.** The same model ranks the list; `select-full` keeps the top 64M by score. On batch 20, 64M keeps 23.69 % (head) and 21.99 % (random), against 24.75 % and 23.06 % for 128M with the same counting. Half the list costs about 1 pt of coverage and halves the recrawl.
- **Batch 21 (11-01).** The official rate is **9.55 % (head) and 8.23 % (random)**. The cause is in the golden set, not the list:
  - 9,041 of its 15,008 golden pairs are `google.com/goto?url=<token>` redirect links. Batches 8–20 have none. These links can never be discovered, crawled or indexed.
  - Without them, 23.6 % (698 of 2,956) head and 21.7 % (664 of 3,057) random golden URLs are indexed, in line with batch 20 (computed from `metric_url`).
  - The Metric team found the cause on 11-06 and is fixing it. Batch 21 has not been recomputed yet.
  - Both batch-21 runs started on 11-01, before the 64M swap, so batch 21 still measures the 128M list.
- **Business insight.** Better list ranking can add at most 1.2–2.2 pt. Page text and a search over it can add much more. This finding moved my work from selection to the recrawl and the index.

### 1.4 Impact (2): the recrawl gives the list its page text

- **Problem.** The list has URLs but no text. The crawler deletes raw HTML after 8 hours and does not fetch a page twice. crawlerdb keeps only the title. 55 % of the 128M list was last fetched before June 2026.
- **Result (live, 11-02 09:54 to 11-08 08:17).**
  - 53,058,440 of 63,999,416 list docs are `fetched` (82.9 %). 5.46M are pending, 4.49M failed and 0.95M are gone (404, 410 or not HTML).
  - The fetched pages hold 74 GiB of zstd Parquet text on Host2. Raw HTML of the same pages would need terabytes.
  - 11,696 crawler jobs were applied; 22 were lost on 11-04 and their docs went back to the queue.
- **Text quality.** `fetched` overstates the useful text:
  - 44.26M docs have real text: status 200, at least 200 characters, and not a known challenge page. That is 69.2 % of the list.
  - 1.57M docs are AWS WAF 202 challenges, and 0.46M are one of two shared "Client Challenge" pages.
  - These pages stay in the index: on the dev half they still hit at 0.53 (batch 20) and 0.43 (batch 21) through URL words and list rank. PR #38 reports `real_text` next to `fetched`.
- **Cost to the normal crawl.** Production runs the crawler API with `manual_first`, so every recrawl request displaces a normal one. The normal crawl ran at a median 23–31 % of its baseline during the bulk phase (11-02 to 11-04, about 260–290 API req/s). It is back at 79–88 % on 11-08, with about 25 API req/s. The client does not throttle; I report this ratio and the recrawl counts every 2 hours (Appendix D).

### 1.5 Impact (3): the full-text index makes the list searchable

- **Problem.** The Metric's ranked flag needs a search over our pages. No search index existed.
- **Result (live index, 52.04M docs).**
  - With plain BM25, the index found only 15 % of present golden pairs in the top 10 (H/F 0.150 on batch 20, 0.157 on batch 21). Popular, weakly matching pages crowded out golden pages.
  - My ranking change raised H/F to **0.415 (batch 20) and 0.384 (batch 21)** on the same pairs. On the held-out test half the gain is **+0.267 [+0.232, +0.302]** and **+0.240 [+0.188, +0.292]** (98.3 % intervals).
  - The ranked rate H/N on batch 20 rose from 2.36 % / 3.11 % to **7.60 % / 7.71 %** (head / random). For batch 21 without the goto links, it is 7.6 % (head, 225 of 2,956) and 5.8 % (random, 177 of 3,057).
  - The online index matches my offline replay within 0.004 on every half.
- **How I kept the result honest.**
  - I wrote the adoption rule on 11-07 03:50, before any variant ran (`rule.md`).
  - A keyword goes to the dev or test half by the parity of its sha256. I chose every setting on dev and read the test half to confirm.
  - Batch 20's test lower bound had to be > 0, and batch 21's interval could not lie wholly below 0. Pairs whose URL was never golden before could not get worse at 95 %, and no script group could either.
  - Batch 22 (11-16) is held back as a clean read.
- **Second round, 11-08.** A second rule (`rank-r2/rule.md`) asked whether a scheme from the literature beats R0. I wrote it before any candidate was scored.
  - It read only the dev halves of batches 20 and 21. The test halves were not read again.
  - It scored 107 candidates. A best-of-107 pick is biased upward, so I report a simultaneous interval for each family (§2.4, "Statistics").
  - Only one change won on both batches: the scale over the best 4,500. #30 now uses it. This is a deviation from the rule, which asked for batch 22 first, and `rule.md` records it.
  - Batch 22 decides by a frozen check (§3, next steps). If the scale fails, three config values bring R0 back, with no rebuild.
- **What it needs next.** The Metric must read the index, which needs a TA decision (§3). The index serves only after #22 and #30 merge.

### 1.6 Counterfactual and attribution

| Step | Without my work | What I own | What others own |
|---|---|---|---|
| (1) Selection, I/N | Metric shows 0; V1's list is 68 % uncrawled | Model, full pipeline, publish path, Metric switch (#9) | Crawl coverage (crawler team); Metric code (classmates, #3, #7) |
| (2) Recrawl, F/I | No page text: crawlerdb holds only titles | Recrawl client design and code (#17, #25, #26, #36–#38), live operation and monitoring | Bulk crawl API #84 and site policies #87 (crawler team); the stable retry policy (#29, hedger9487); review and merge |
| (3) Search, H/F | Ranked = 0 by definition | Engine choice, index build, ranking, evaluation | A ranked step in the Metric (TA) |

---

## 2. Technical Description

**Interactive diagrams:** [Index Infra midterm: three business impacts and the designs behind them](https://share.onorca.dev/a/s4h7TZpOZL_5). Source: `docs/reports/2026-midterm-technical.html`.

### 2.1 Architecture

```text
crawlerdb (YugabyteDB since 10-30)     metricdb (golden batches)
   │ pull-full (read-only, guarded)        │
   ▼                                       ▼
isel on Host2: features → model → score-full → select-full (exact top K = 64M)
   │ publish → swap → verify-live
   ▼
selectdb :5445 (Host1) ◄── Metric cron, 1st / 16th ──► indexed_rate              (1)
   │ live run's manifest
   ▼
recrawl-sync → recrawl.host / recrawl.doc (selectdb)                               (2)
recrawl-plan ──POST/GET/DELETE──► crawler bulk crawl API (#84) ──► result files
recrawl-archive ◄── result files ──► text Parquet (/data/isel/text)
   │ text-index-build
   ▼
OpenSearch alias isel-text → rescore by list rank → host cap → top 10              (3)
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

**The formulas in the selection model.** Each one has the same layout: the formula, why it has this form, and the data. Reference numbers point to Appendix F.

1. **Decay of older batches.**
   ```text
   w(b′, b) = 0.5 ^ ((as_of_b − created_b′) / 90 days)
   ```
   - *Why.* An old batch still predicts the next one, but less than a recent one. A half-life gives one weight per batch and needs one setting (`half_life_days = 90`).
   - *Data.* A past golden URL comes back in the next batch 0.6–2.7 % of the time, over 400× the base rate. So history must stay in the model, but it must fade.
2. **Domain rates, shrunk toward the parent level** (empirical Bayes [9, 10]).
   ```text
   r_x    = (g_x + a) / (e_x + a / r_parent)      a = 1 pseudo hit
   lift_x = ln(r_x / r_global)
   levels: host prefix → host → domain key → TLD → global
   ```
   - *Why.* g_x is the decayed count of golden pages at level x, and e_x is the estimated count of crawled pages there. A host with 3 pages and 1 hit has a raw rate of 0.33. That is noise, not signal. The prior adds a pseudo hit at the parent's rate, so a small key stays near its parent and a large key keeps its own rate. A key with no data equals its parent. The log makes the lift additive for the model.
   - *Data.* Without the domain features, head recall falls by 1.16–1.45 pt (paired 95 % CI). In the earlier weight study it fell by 4.38 pt on batch 18. I did not tune a.
3. **A page against its own domain.**
   ```text
   rel_inlinks = ln(1 + inlinks) − mean over the domain key of ln(1 + inlinks)
   ```
   - *Why.* Inlink counts grow with the size of a site. The difference tells the model which page of a site is more linked, and the log stops a few huge counts from dominating. Static-rank work uses link counts in the same way [6].
4. **The model.** LightGBM [11], a gradient-boosted tree ensemble, gives P(URL is golden in the next batch).
   - *Why.* Trees learn the interactions between page facts and domain lifts without hand-made products.
   - *Data.* It beats logistic regression on the same features by 0.50 / 0.47 pt.
5. **Kept-count check in `score-full`** (`IndexSelectionV2/src/isel/score.py`).
   ```text
   per shard s:  p_s = sample rows with score ≥ τ / sample rows
                 expected_s = scored_s × p_s
                 var_s      = scored_s² × p_s (1 − p_s) / sample rows × (1 − sample rate)
   z = (kept − Σ expected_s) / sqrt(Σ var_s)        stop if |z| > 6
   ```
   - *Why.* The 2 % sample predicts how many rows of the full crawl pass the floor τ. The variance is binomial, with a factor for a draw without replacement. A model that sees the sample and the full rows differently breaks this prediction, so the check catches leaks before a publish. The limit 6 is wide: sampling noise never stops a run.
   - *Data.* The leaky model kept 38.14M more rows than predicted, and the check stopped it. The fixed model gave z = −1.42.

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
| `select-full` | Exact top K, URL-hash tie order, Parquet manifest with sha256 | The same list on every rerun; the manifest also feeds the recrawl and the index |
| `migrate-selectdb` | Alembic + SQLAlchemy Core, `alembic check` | Schema changes have a history and a check |
| `publish`, `swap` | `COPY FREEZE` into a new table; rename in one transaction, 5 s lock timeout; refused in the Metric window (1st and 16th, 11:30–20:00) | Readers are never blocked |
| `verify-live` | The Metric's own SQL, as the `select` role | Proves that the Metric sees the new list |

Since crawlerdb moved to YugabyteDB on 10-30, the reader splits each table by `yb_hash_code(url)` ranges, because YugabyteDB has no `ctid` (PR #23). For 64M, `select-full` applies no per-hostname cap: the crawler decides how fast each host goes.

**Result.**
- The full read took 1 h 48 min for 1,657,046,602 rows (V2's first design estimated about 14 h). Host1 IO pressure stayed at its baseline.
- `score-full` took 148.6 min on 32 CPUs. It kept 147,021,239 rows against a predicted 147,181,326 (z = −1.42, limit 6).
- The z check found a leak after the first full scoring. The leaky model kept 38.14M more rows above τ than predicted, mostly small-domain pages with a +17 logit boost. I fixed the three leaks, refroze the model, and published before batch 20.
- The pipeline went upstream as 11 stacked PRs in NTU-CSIE5376/WebSearchEngine (WSE), #8–#18, all merged by 10-21.
- The 64M list (run 3) was selected from the same frozen scores and swapped in on 11-02 09:24, outside the Metric window.

### 2.3 Recrawl client (2)

**Context.** The index needs page text. The crawler deletes raw HTML after 8 hours, and crawlerdb keeps only the hash, length and title. The TA set the goal on 10-25: fetch the whole list in two weeks, through the production crawler.

**Design rules.**
1. **No writes to crawlerdb.** The client talks to the crawler only through its bulk crawl API (WebCrawler #84).
2. **The client sets no rate.** The crawler's AutoThrottle and site policies pace every host, so each host has one rate limiter. The client sizes its calls to the server's limits: `job_size` 10,000 (the API's `max_urls`) and 64 jobs in flight (`MANUAL_BULK_CRAWL_MAX_ACTIVE_JOBS`).
3. **Every doc is traceable.** State lives in selectdb, results in files, and each result maps back to its doc.
4. **Best pages first.** Docs go out in list-rank order, so the pages most likely to be golden get text first.

**Code and state.** Package `isel`, `IndexSelectionV2/src/isel/recrawl.py`, in the org repo NTU-CSIE5376/WebSearchEngine. State is the schema `recrawl` in selectdb: tables `host`, `doc` and `job`, created by Alembic revision `0003_recrawl`. The `select` role, which the Metric uses, cannot see it.

**Commands.**

| Command | Mode | What it does | Status |
|---|---|---|---|
| `recrawl-sync` | Once, after each swap | Loads the live run's manifest into `recrawl.host` and `recrawl.doc`, 1M rows per transaction. Docs that left the list get `rank = NULL`. Refused in the Metric window | Live |
| `recrawl-plan` | Loop, every 30 s | Recovers lost claims, polls open jobs, frees finished hosts, deletes applied jobs, then claims hosts and submits new jobs | Live |
| `recrawl-archive` | Loop, every 15 s | Reads each completed job's result files, extracts text, writes Parquet, and applies outcomes to `recrawl.doc` | Live |

**One plan cycle.**
1. **Recover.** A job still `claimed` after 600 s becomes `lost`, and its docs and hosts are released.
2. **Poll.** A GET for each open job, at most every 15 s. A 404 makes the job `lost`.
3. **Drain.** A host is freed as soon as all its docs in a job have result files, even while the job waits for other hosts. The TA asked for this on 11-02, so a slow host cannot hold a whole job.
4. **Delete.** A DELETE for every applied job.
5. **Stop rules.** No new claims if a job is in `error`, the result disk is unmounted, the API backoff is active, 64 jobs are open, 64 completed jobs wait for the archive, or less than 800 GB is free.
6. **Claim, then send.** Hosts are taken in `best_rank` order, each with up to 240 due docs in rank order. Claims are committed before the POST. A 400 "at index N" marks that doc `gone` and resends the rest.

**Outcomes and retries.**

| Result | New state | Next try | Status |
|---|---|---|---|
| 2xx HTML | `fetched` | Revisit after 30 days; revisits off in #36 | Live; #36 in review |
| 2xx non-HTML, 404, 410 | `gone` | Never | Live |
| robots `IgnoreRequest` | `failed` | After 30 days | Live |
| 401, 402, 403 | `failed` | 2 attempts, then after 30 days | #36 in review (live: 5 attempts) |
| 429, 503 | `failed` | Due again at once, with no attempt used; the host pause paces it | #36 in review (live: attempt +1, fixed 600 s host pause) |
| Other failure | `failed` | `600 · 2^attempts` s, capped at 24 h; after 5 attempts, after 30 days | Live |

**Host pacing (#36, in review).** The client does not throttle. It only pauses a host that answered 429 or 503:
- 429/503 and no status-200 page in the batch: the pause doubles from 600 s, capped at 6 h.
- 429/503 with at least one 200 page: 600 s, and the streak resets.
- No 429/503: no pause.

A 202 WAF challenge does not count as a page. A host is freed in the drain, with its pause set in the same transaction. Before this, a throttled host finished its own docs in a median 5.6 min, then sat idle for a median 87 % of its 45.3 min job.

**Order of work (#36, in review).** A host's never-fetched docs go before its revisits, and `revisit = false` turns revisits off. A never-fetched doc has no text and cannot hit; a revisit only refreshes text. At about 1M docs a day, one round of 57M revisits takes 59 days, longer than the 30-day revisit period.

**URL encoding (#37, merged 11-08; deployed with #36).** The API rejects a raw `[`, `]` or `|` with a 400, and 1,629 list docs were marked `gone` for this. The client now percent-encodes the path, query and fragment per RFC 3986 before sending. The stored URL and `doc_id` do not change, so golden matching does not change.

**Text quality report (#38, merged 11-08).** `recrawl-plan --dry-run` reports `real_text` and `waf_202` next to `fetched`. Real text means status 200, at least 200 characters, and a `payload_sha1` that is not one of the two known challenge pages. It changes no outcome.

**Results to text.**

| Step | Technology | Why |
|---|---|---|
| Map results | `request.json` lists each URL's `crawler_id`; results map to docs by position | The crawler can rewrite a URL string, so the client never matches by string. A reordered list halts the planner |
| Extract | Resiliparse: main-content text with a full-text fallback, title, description, language; body capped at 200k chars | The index needs text, not HTML |
| Store | One zstd Parquet per job, partitioned by fetch date; a gzipped per-URL log | 74 GiB for 53M pages |
| Apply | 8 worker processes prepare jobs; one process writes outcomes | One writer per table keeps the apply simple and repeatable |

**After go-live: what changed and why.**

```text
10-21  #17 merged: first client of the job API (242 unit, 35 integration tests)
11-02  #25 merged: sized to Host2's deployed limits; hosts drain early (TA's request). R3 starts 09:54
11-02  three live faults fixed in #26: archive workers hit pids_limit (lazy DuckDB import);
       status 781 and 901 halted the planner (accept any integer); a half-dead selectdb
       connection hung the planner (TCP keepalives on both ends)
11-06  #26 merged with a blanket 202/403 host block; #29 (hedger9487) removed the block
       two hours later: probing could retry blocked top URLs while healthy URLs waited
11-08  #36, #37, #38 opened from the live data (issues #31, #32, #34, #35);
       #37 and #38 merged the same day, #36 in review
```

#36 pauses a host only on 429 or 503, so it does not bring back the block that #29 removed. A replay of Host2's job logs (11-05 to 11-08, 115 hosts with a 429) gives the time those hosts spend held or paused:

| Pause rule | Host-hours |
|---|---|
| Fixed 600 s, hosts held until the job ends (live) | 5,095 |
| Fixed 600 s, hosts freed in the drain | 2,067 |
| Doubles on any 429, cap 24 h | 70,919 |
| Doubles only with no page, cap 6 h (#36) | 4,767 |

**The formulas in the recrawl.**

1. **Retry delay for a failed doc.**
   ```text
   delay = min(600 s × 2^attempts, 24 h);  after 5 attempts, 30 days
   ```
   - *Why.* Capped exponential backoff spreads retries out, so a host that is down is not asked again and again [17]. The cap keeps a doc from waiting for days after a short outage. The doc's host is still paced by the crawler, so the client needs no jitter of its own.
2. **Host pause after 429 or 503** (#36).
   ```text
   no 200 page in the batch:  pause = min(600 s × 2^streak, 6 h)
   at least one 200 page:     pause = 600 s, streak = 0
   ```
   - *Why.* 429 means "too many requests" [18], and 503 means the server is overloaded for now [19]. Both ask the client to wait. A host that still serves some pages is only busy, so a short pause is enough. A host that serves nothing gets longer pauses.
   - *Data.* The replay above: doubling on any 429 holds hosts for 70,919 host-hours. #36's rule holds them for 4,767, fewer than the live rule's 5,095.
3. **Revisit cost.**
   ```text
   one revisit round = 57M docs ÷ about 1M docs a day ≈ 59 days  >  30-day revisit period
   ```
   - *Why.* A refresh policy trades freshness against a fixed crawl budget [20]. Here a revisit only refreshes text, but a first fetch can add a new hit. So #36 spends the budget on first fetches first, and turns revisits off until the list is fetched.

**Tests.** Upstream main passed 252 unit and 40 integration tests before #37 and #38 merged on 11-08. With #36, #37 and #38 merged, 264 unit and 52 integration tests pass. The integration tests run the planner and archive against a real Postgres and a fake crawler that writes per-crawler result files.

### 2.4 Full-text index (3)

**Context.** The Metric's ranked flag needs the top 10 for each golden keyword. The keywords come from 10 regions, including Chinese, Japanese and Hindi. The index must fit beside the crawler, and the list changes every two weeks.

**Action: how I chose the engine.**
1. **Requirements.** R1 BM25 over title, description and body for the list. R2 many languages. R3 no load on the crawler or crawlerdb. R4 a list swap every two weeks. R5 exact lookup by URL or `doc_id`. R6 low query volume. R7 one student can run it in Docker.
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
5. **Result on NanoMIRACL** (8 languages × 200 questions, a small subset of MIRACL [3]). The metric is nDCG@10 [2], weighted by language:
   ```text
   DCG@10 = Σ_{i=1..10} rel_i / log2(i + 1);   nDCG@10 = DCG@10 / ideal DCG@10
   weighted nDCG@10 = Σ_lang share_lang × mean nDCG@10_lang
   ```
   nDCG rewards a relevant page more near the top, and the weights match batch 20's mix of languages. Weighted nDCG@10 is +0.010 [+0.001, +0.017] for OpenSearch. Japanese decides it, +0.260: Quickwit keeps a kana run as one token, so `オリンピックの` never matches `オリンピック`. Chinese favours Quickwit by 0.032. Q, S and M all hold.

**Why OpenSearch fits this job.**
- **Tokenization.** ICU segments CJK words with a dictionary. Quickwit's official image has no multilingual tokenizer.
- **The two-week swap.** OpenSearch moves an alias in one atomic call, and deletes by `doc_id`. Quickwit's splits cannot change.
- **Comparable numbers.** Its BM25 is Lucene's, the same as Anserini's, so my results compare with IR baselines.
- **Project health.** OpenSearch ships monthly under the Linux Foundation. Quickwit had no release from 2024 until 0.9.0 in 10-2026.

**Action: how the index is built (`text-index-build`).**

| Step | Technology | Why |
|---|---|---|
| 1. Lock | `fcntl.flock` on a work-dir file | Two builds cannot delete each other's index |
| 2. Read the newest text | DuckDB over the text Parquet: each doc keeps its newest row, and a row with text beats a row without. A join on the selection manifest adds each doc's list rank | 52M bodies are never sorted |
| 3. Rank check | Stop if fewer than 99 % of the docs have a list rank | The ranking needs the rank of every doc |
| 4. Mapping | `icu_tokenizer` + `icu_folding` on `title`, `description`, `body`; `url.text` holds the URL's words; `body` with `index_options: freqs`, not in `_source`; `sel_prior` as a `rank_feature` = 10⁹ / (rank + 1); `dynamic: strict` | BM25 uses term counts, not positions, and the top 10 was the same without them |
| 5. Space check | Free space ≥ 2 × the projected index above the flood stage (95 % full, where OpenSearch blocks writes) | A full disk would also stop the crawler |
| 6. Bulk load | 8 shards, 0 replicas, refresh off, async translog; 8 MiB `_bulk` requests, 2 in flight; resend on 429 or 503 | Nothing searches it yet, and the Parquet is the source of truth |
| 7. Count and flush | Doc count must equal the winner rows; flush, then wait for retention leases | Without the wait the index was 1.8× larger, because recovery data survived the merge |
| 8. Merge | Second space check; force merge to 1 segment per shard, one shard at a time | The peak grows by one shard, not one index |
| 9. Seal and swap | Serve settings back; `blocks.write`; one `_aliases` call | Readers switch in one step. Any failure before the move deletes the new index |
| 10. Record | A manifest with sizes and timings | Every build can be audited |

**Result of the build (11-07).** 52,044,958 docs in 3.41 h (5,486 docs/s during the load). The index is 43.56 GB, 837 bytes per doc, from 253.9 GB of text. Every doc got a list rank.

**Action: the ranking.** Plain BM25 failed at this size: popular pages with weak matches filled the top 10. Two rounds of experiments chose the query that #30 runs now:

```text
search(keyword)
  OpenSearch
    1. match    multi_match best_fields over title^2, description, body, url.text^2   (BM25)
    2. rescore  BM25's best 4,500 (563 per shard × 8 shards):
                score = BM25 × (1 + 0.19302 × ln(1 + sel_prior))
  client
    3. order by score, then doc_id
    4. keep 10, at most 2 per host
```

Each formula below has the same layout: the formula, why it has this form, the data, and references (Appendix F). All H/F numbers here are on the dev halves of batches 20 and 21 (1,021 keywords and 1,946 pairs), except the test-half line in part 3.

**1. Text match: BM25 with `best_fields`** [1].

```text
BM25(q, d) = Σ_{t ∈ q} IDF(t) × tf(t,d) × (k1 + 1) / (tf(t,d) + k1 × (1 − b + b × |d| / avgdl))
IDF(t)     = ln(1 + (N − df(t) + 0.5) / (df(t) + 0.5))         k1 = 1.2, b = 0.75 (Lucene defaults)
best_fields: score(q, d) = max over the fields f of weight_f × BM25(q, f)
```

- *Why.* The tf term saturates, so a word repeated 50 times does not count 50 times. The b term removes the advantage of long pages. `best_fields` takes the best field, so a short title with every keyword wins over a long body that repeats one word. The URL's words get weight 2, because many golden pages are entry pages whose URL names the keyword.
- *Data.* The old query gave 0.167, and `url.text^2` gave 0.174. Of the k1 and b settings, the rule picked k1 1.2, b 0.9. I added that setting after I saw the grid, so I kept the defaults.

**2. The list rank as a prior feature.**

```text
sel_prior S = 10⁹ / (rank + 1)                    a rank_feature, written at build time
ln(1 + S) ≈ ln 10 × (9 − log10(rank + 1))          S ≥ 15 for every rank of the 64M list
```

- *Why.* The list rank is query-independent evidence: it is my model's order of P(golden) for the page. Such priors help most for entry pages and popular pages [4, 6]. The log makes each factor of 10 in rank worth the same: rank 10 against 100 counts as much as 1M against 10M. OpenSearch's `rank_feature` with `log` computes ln(scaling_factor + S) [21], so the prior needs no script.
- *Data.* I used the rank, not the model's score. On dev, the best rank prior gave 0.418, and the best score prior gave 0.408.

**3. First round, 11-07: add the prior (R0). Confirmed on the test halves.**

```text
score = BM25 + 1.7372 × ln(1 + S)          over BM25's best 1,500 (188 per shard)
      ≈ BM25 + 4 × (9 − log10(rank + 1))    1.7372 = 4 / ln 10: 4 points per factor of 10 in rank
```

- *Why.* A sum is the standard way to add query-independent evidence to BM25 [5]. The experiment notes write the same score as BM25 + 4 × (8 − log10(rank + 1)). It differs by a constant 4 for every doc in the window, so the order is the same. The rule tried three transforms (log of the rank, the model's score, a saturation of the score), each with five weights.
- *Data.* The weight has a clear peak. Saturation of the score gave 0.420, within 1 SE of the best. The rule picks the simplest candidate within 1 SE, so it took the log of the rank at λ = 4.

  | Weight λ, points per factor of 10 | 1 | 2 | 4 | 8 | 16 |
  |---|---|---|---|---|---|
  | H/F with the host cap | 0.320 | 0.385 | **0.418** | 0.388 | 0.348 |

  | Setting | H/F |
  |---|---|
  | BM25 with `url.text^2` | 0.174 |
  | Host cap only | 0.179 |
  | List-rank prior only | 0.405 |
  | All three (R0) | 0.418 |

- *Test halves.* +0.267 [+0.232, +0.302] on batch 20 and +0.240 [+0.188, +0.292] on batch 21, 98.3 % intervals. These are the numbers in §1.5 and Appendix C.

**Why `rescore`, not a `should` clause.** My first online form put the prior in a `should` clause, so it acted on every matching doc. 39–44 % of the dev top 10 then came from outside BM25's top 1,500, mostly popular pages with a median list rank of about 400. Online results fell 0.03–0.06 below the offline replay, and the rule's 0.01 gate failed. `rescore` limits the prior to the best text matches and needed no rebuild [21]. It passed the gate, with at most 0.0036 between online and offline.

**4. Second round, 11-08: scale the text score (A-4500). Dev only; #30's default.**

```text
score = BM25 × (1 + 0.19302 × ln(1 + S))           over BM25's best 4,500 (563 per shard)
      ≈ BM25 × (1 + 4/9 × (9 − log10(rank + 1)))
0.19302 = 4 / 9 / ln 10:  factor 5.0 at rank 0, 1.54 at rank 64M, +0.444 per factor of 10 in rank
OpenSearch: rescore, score_mode "multiply",
            rescore_query = bool.should [rank_feature log, constant_score 1]
```

- *Why a product.* BM25's scale changes by query. On dev, the median of the top-10 mean BM25 is 20.6, and it varies widely between queries. The sum adds the same points to every query. So for a query with low BM25 the prior decides almost all, and for a query with high BM25 it decides little. The product makes the prior's effect proportional to the text score.
  - A best weight chosen per keyword gives 0.472. This oracle cannot run, but it shows that one fixed sum does not fit every query.
  - The constant 1 keeps part of the text score free of the prior. The pure product BM25 × (g + 1) gained −0.0005.
- *Why 4,500.* A deeper window brings in more golden pages, and more popular pages with a weak match. The sum lifts those popular pages into the top 10, so it peaks at 4,500 and then falls. The product holds them to their own low text score, so it rises to 4,500 and stays flat to 10,000.

  | Window | 1,500 | 2,000 | 3,000 | 4,500 | 6,000 | 10,000 |
  |---|---|---|---|---|---|---|
  | Golden pairs in the window | 0.703 | 0.719 | 0.745 | 0.769 | 0.780 | 0.801 |
  | Sum (R0's score) | **0.4178** | 0.4203 | 0.4250 | 0.4265 | 0.4209 | 0.4157 |
  | Product | 0.4255 | 0.4291 | 0.4332 | **0.4347** | 0.4342 | 0.4347 |

- *Why not the others.* 107 candidates from the literature, against R0:

  | Family | Best against R0 | Result |
  |---|---|---|
  | Sigmoid or saturation of the rank [5] | +0.001 | Same as R0 |
  | Log with a floor at rank 1M or 10M | −0.011 | Lifting deep pages hurts |
  | BM25 over its top-10 mean per query [7]; z-score per query | +0.002; −0.003 | Not significant |
  | Reciprocal rank fusion of BM25 and the list rank [8] | −0.045 | Ranks alone lose BM25's score gaps |
  | Sum, best 4,500 | +0.009 | Falls after 4,500 |
  | **Product, best 4,500 (taken)** | **+0.017** (SE 0.005) | One request |
  | Product, best 1,500, plus the 50 docs of highest prior with every keyword in the title or URL [7] | +0.018 (SE 0.005) | Same pages as the deeper window, but a second request; not taken |

- *Online and cost.* On the live alias with 8 shards, the product at 4,500 gave 0.4342; the offline replay gave 0.4347. R0 gave 0.4173 online. OpenSearch's `took` p50 is 52 ms, against 50 ms for R0.
- *Who gains.* No list-rank bin, script or token count is lower than under R0.

  | List rank | Pairs | R0 | Product, 4,500 |
  |---|---|---|---|
  | under 0.1M | 681 | 0.564 | 0.579 |
  | 0.1–1M | 441 | 0.562 | 0.569 |
  | 1–5M | 324 | 0.321 | 0.327 |
  | 5–10M | 153 | 0.222 | 0.268 |
  | 10–20M | 139 | 0.130 | 0.158 |
  | 20M or more | 208 | 0.120 | 0.154 |

  Kana (115 pairs, 0.365) and the other scripts (81 pairs, 0.173) do not change. Pages at rank 20M or more still lose against BM25 alone, which gave 0.231.
- *Config.* `[text_index]` holds `prior_score_mode = "multiply"`, `prior_boost = 0.19302` and `prior_depth = 4500`. `"total"`, `1.7372` and `1500` bring R0 back, with no rebuild.

**5. Host cap.**

```text
top 10 = the first 10 hits in score order, at most 2 per host;  host = hostname without "www."
```

- *Why.* Without a cap, one site can fill the top 10 with similar pages. Google has used the same limit, two results per site in most cases, since June 2019 [16]. The client reads 200 hits (`fetch_k`), so the cap can refill the top 10.
- *Data.* 0.174 → 0.179. The cap removes 1.6 % of the pairs.

**6. Statistics: how two settings are compared.**

```text
H/F  = Σ found pairs / Σ present pairs       present: the page is in the 51.2M index; fixed for every run
Δ    = H/F(candidate) − H/F(reference), on the same pairs
SE   = sd of Δ* over 10,000 paired bootstrap draws of keywords (seed 20)
h    = 95th percentile over draws of max_c |Δ*_c − Δ_c|      simultaneous half-width of a family
dev  = keywords with int(sha256(keyword), 16) mod 2 = 0; test = the rest
98.3 % = 1 − 0.05 / 3                                          three planned reads (Bonferroni)
```

- *Why.*
  - Pairs of one keyword share one top 10, so they are not independent. Resampling keywords keeps that dependence [12, 14].
  - Both settings use the same draws. This paired test removes the noise that the two settings share.
  - The max over a family holds the error rate at 5 % for the whole family, not for each candidate [13].
  - A pick from many candidates on the same data is biased upward [15]. So dev chooses, and data that did not choose confirms.
- *Data.* The first round's family of 33 had h = 0.033. In the second round, the window family of 18 has h = 0.012, and +0.017 is above it. The 107 candidates together bias the best by about 2–3 SE, so the true gain may be smaller. One batch detects about +0.03, so batch 22 alone may not settle +0.017.

**Where the misses are (dev, 1,946 pairs, under R0).** 41.7 % hit. 27.1 % are in BM25's top 1,500 but score too low. 29.6 % are outside the top 1,500: 63 % of those pages lack the full keyword, for example `turquia` against `wiki/Turkey`. 1.6 % are removed by the host cap. A LightGBM reranker on 14 features did not beat the linear score. The deeper window of the second round reaches 22 % of the pairs outside the top 1,500 (0.703 → 0.769 in the window).

**Tests.** PR #22 passes 286 tests plus 1 integration test against the real OpenSearch 3.9.0 image. PR #30 adds the prior in both modes, the host cap and their tests: 305 tests pass, and 3 integration tests pass on a real node, with the prior test run in both modes. Turning the change off needs no rebuild: set `prior_boost = 0` and `host_cap = 0`.

### 2.5 Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| Select only crawled pages, ranked by P(golden) | Uncrawled pages have no content; V1 spent 68 % of its list on them | Coverage is capped by the crawl |
| As-of pools and a `FORBIDDEN` column list | The crawler writes golden labels back into its columns | The crawler's own `url_score` cannot be used |
| Two levels: empirical-Bayes domain prior + LightGBM | One additive score cannot separate domain from page | Two sample halves, so each half sees fewer rows |
| New table + rename swap, Alembic schema | TRUNCATE + INSERT blocks readers; schema changes need history | Up to three copies of the list on disk |
| 64M with no per-hostname cap | Fits the crawl bandwidth; isel cannot know a host's real pace before the crawl | −1.06 / −1.07 pt on batch 20; big hosts finish at the crawler's pace |
| Recrawl through the crawler's API, with no client rate | One rate limiter per host, owned by the crawler; no writes to crawlerdb | Under `manual_first`, the bulk phase cut the normal crawl to 23–31 % of its baseline |
| Pause a host only on 429/503 (#36) | A blanket 202/403 block retried blocked top URLs while healthy ones waited (#29) | WAF pages stay `fetched`; #38 reports them |
| First fetches before revisits; revisits off (#36) | A never-fetched page cannot hit; a full revisit round takes 59 days | Old text is not refreshed until revisits come back |
| Keep extracted text, not raw HTML | 74 GiB for 53M pages | A better parser cannot reprocess old pages |
| OpenSearch + ICU by a pre-written rule | Better CJK quality; atomic alias swap; rule fixed before data | 31 GB JVM heap; larger than Quickwit |
| List rank as a `rescore` prior, only over the best text matches (R0, 11-07) | H/F 0.174 → 0.418 on dev; +0.267 and +0.240 on the test halves; online matches offline | Weight fits this index size; a new list needs a new prior file and a rebuild |
| The prior scales BM25 instead of adding to it, over the best 4,500 (#30 default, 11-08) | BM25's scale changes by query; 0.418 → 0.435 on dev, 0.434 online; no group lower | Chosen on dev from 107 candidates; batch 22 decides; three config values bring R0 back |

---

## 3. Limits, Risks and Next Steps

**Limits.**
- Coverage bounds everything: only about 25–26 % of batch-20 golden URLs were crawled.
- The ranking was tuned on the dev halves of batches 20 and 21, and the test halves were read twice. The second read only checked online against offline. Batch 22 is the first clean read.
- #30's default was chosen on dev alone, from 107 candidates. Its +0.017 is biased upward by about 2–3 SE, and the test halves have not confirmed it. One batch detects about +0.03, so batch 22 alone may not settle it.
- The ranking is weaker on deep pages. On dev, pairs with a list rank of 20M or more score 0.231 with BM25 alone, 0.120 under R0 and 0.154 under #30's default. Under R0, the "other" script group on batch 20's test half fell from 0.333 to 0.282 on 39 pairs.
- Batch 21's official numbers are distorted by the goto links until the Metric team recomputes them.

**Risks and asks.**

| Risk | Impact | Ask | Owner |
|---|---|---|---|
| The Metric writes `ranked = 0` | The index cannot count, even after deploy | Decide how the Metric reads ranked: a port to the index or flags from an isel job | TA |
| 60 % of batch-21 golden pairs are `google.com/goto` links | Official batch-21 rates drop to 9.55 % / 8.23 % | Review #39 (draft, Metric team: resolve the links when golden sets are built); recompute batch 21; check batch 22 (11-16) | Metric team |
| `manual_first` gives the recrawl priority over the normal crawl | Bulk phases cut the normal crawl to 23–31 % of its baseline | Agree when the next full recrawl (a new list, or revisits) may run | TA, crawler team |
| WAF and challenge pages count as `fetched` | `fetched` overstates real text by 8.8M docs | #38 (merged 11-08) reports `real_text`; decide how to mark these pages (issue #34) | Me, TA |
| The list's crawl snapshot is from 10-11 | On batch 21, 72 % of crawled golden URLs that are off the list were crawled after the snapshot | Review #23; approve a full read on YugabyteDB for a new snapshot | TA |
| OpenSearch needs a 31 GB heap and about 44 GB of disk on Host2 | The index cannot serve without them | Confirm OpenSearch may run permanently on Host2 | TA |
| #22 and #30 conflict with `main` in `config/isel.toml` | #38 adds two `[recrawl]` keys where #22 starts `[text_index]` | Rebase #22, then #30, and keep both sides | Me |

**Next steps.**
1. Merge #36 (#37 and #38 merged on 11-08); deploy #36 and #37 together, with migration `0004` and a one-time requeue of 271k throttled docs and the 1,629 `invalid_url` docs.
2. Get #22 and #30 reviewed; serve `isel-text` without a stop.
3. Read batch 22 (11-16) once, after checking it has no goto links. The check is frozen in `rank-r2/rule.md`:
   - If the 95 % interval of the product (4,500) minus R0 has a lower bound above 0, keep the product.
   - Else, if the same holds for the product plus the 50 strong matches, take that form.
   - Else, go back to R0: `prior_score_mode = "total"`, `prior_boost = 1.7372`, `prior_depth = 1500`. No rebuild is needed.
4. Agree with the TA how the Metric reads ranked.
5. With approval, take a new crawl snapshot, select a new list, recrawl its new pages and rebuild the index.

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

### B. List size and official rates

**List size on batch 20 (official counting method).**

| List | Head | Random | 14-day recrawl need |
|---|---|---|---|
| 128M (live 10-14 to 11-02) | 24.75 % | 23.06 % | 106 req/s |
| 64M, no per-hostname cap (live since 11-02) | 23.69 % | 21.99 % | 52.9 req/s |

The recrawl need is the list size over 14 days: 64M ÷ (14 × 86,400 s) = 52.9 req/s, and 128M gives 105.8 req/s. The live 128M random rate is 23.02 % in metricdb; 23.06 % is the same list counted by my script. Source: `docs/experiments/2026-10-29-pr84-starvation/results/golden_coverage_64m.txt`.

**Official rates by batch (metricdb `metric_headset_total`, `metric_randomset_total`).**

| Batch | Set | Total | Discovered | Crawled | Indexed |
|---|---|---|---|---|---|
| 19 | head | 7,475 | 51.5 % | 30.6 % | 0 |
| 19 | random | 8,046 | 31.8 % | 19.0 % | 0 |
| 20 | head | 7,329 | 45.3 % | 26.0 % | **24.75 %** |
| 20 | random | 8,031 | 47.0 % | 25.2 % | **23.02 %** |
| 21 | head | 7,310 | 21.1 % | 13.1 % | 9.55 % (23.6 % without goto) |
| 21 | random | 7,955 | 17.7 % | 10.7 % | 8.23 % (21.7 % without goto) |

The without-goto rates come from `metric_url` (`docs/issues/2026-11-07-non-text-index.md` A1).

### C. Full-text index on 52.04M docs (index `isel-text-20261107t044459193406z`)

**H/F on fixed pairs** (pairs present in the plain-BM25 index; 98.3 % intervals for the change).

| | Batch 20 | Batch 21 |
|---|---|---|
| All pairs | 0.150 → **0.415** | 0.157 → **0.384** |
| Test half | 0.133 → 0.400 | 0.143 → 0.383 |
| Test half, change | +0.267 [+0.232, +0.302] | +0.240 [+0.188, +0.292] |
| Golden for the first time, test half | 0.146 → 0.358 | 0.157 → 0.343 |
| Query latency p50, at the client | 23 → 58 ms | 22 → 59 ms |

**`text-index-eval` on the 52M index with R0** (own denominator; 95 % intervals). Batch 22 will give these numbers for #30's default.

| Batch | Set | Pairs | Present (F/N) | H/F | H/N, distinct URLs |
|---|---|---|---|---|---|
| 20 | head | 7,681 | 0.191 | 0.391 [0.361, 0.421] | **0.076** [0.069, 0.084] |
| 20 | random | 8,045 | 0.177 | 0.434 [0.403, 0.464] | **0.077** [0.069, 0.084] |
| 21 | head | 7,405 | 0.079 | 0.396 [0.355, 0.435] | 0.031 [0.027, 0.035] |
| 21 | random | 7,946 | 0.060 | 0.370 [0.327, 0.417] | 0.022 [0.019, 0.026] |

Batch 21's H/N includes the goto links in N. Without them it is 7.6 % (head) and 5.8 % (random).

**Gates of the rule** (`rule.md`).

| Gate | Condition | Result |
|---|---|---|
| G1 reproduce | The dump reproduces the eval's hits | Pass: 419 and 161, 0 queries differ |
| G2 depth | Recall at depth 1,500 ≥ 0.35 | Pass: 0.617 and 0.599 |
| G3 control | An index of only golden pages reaches ≥ 0.75 | Pass: 0.833 |
| Online = offline | Within 0.01 | `should` form failed (−0.03 to −0.06); `rescore` passed (max 0.0036) |

Source: `docs/experiments/2026-11-07-text-rank/` (`README.md`, `rule.md`, `results/online-rescore.json`, `results/gate-rescore.json`, `results/miss.json`, `results/eval/`); build manifest `results/build/rebuild-20261107.json`; engine choice `docs/experiments/2026-10-22-index-engine-selection.md`.

**Second round on dev (11-08), against R0.** Batches 20 and 21, dev halves only: 1,021 keywords, 1,946 pairs. Paired bootstrap over keywords, 10,000 draws.

| Setting | Form | Offline | Online | Against R0 | Batch 20 / 21 |
|---|---|---|---|---|---|
| R0 (first round) | sum, best 1,500 | 0.4178 | 0.4173 | — | 0.4291 / 0.3891 |
| Product, best 1,500 | product | 0.4255 | 0.4229 | +0.0077 (SE 0.0038) | 0.4384 / 0.3927 |
| **Product, best 4,500 (#30 default)** | product | **0.4347** | **0.4342** | **+0.0170** (SE 0.0047) | 0.4470 / 0.4036 |
| Product, best 1,500, plus 50 strong matches | product, two requests | 0.4358 | 0.4347 | +0.0180 (SE 0.0047) | 0.4499 / 0.4000 |
| Best weight per keyword (oracle, cannot run) | — | 0.4717 | — | +0.054 | — |

OpenSearch's `took` p50 on the live alias is 50 ms for R0, 53 ms for the product at 1,500 and 52 ms for the product at 4,500. The window curve and the groups are in §2.4.

Source: branch `exp/rank-r2` (commit `17b4f0b`), `docs/experiments/2026-11-08-rank-r2/` (`README.md`, `rule.md`, `results/r2-dev.json`, `r2-dev-ext.json`, `r2-firstm.json`, `r2-deep.json`, `r2-online-dev.json`, `r2-online-deep.json`, `r2-deep-groups.json`); WSE `IndexSelectionV2/docs/TEXT_INDEX.md`, "Second round on dev, 2026-11-08".

### D. Recrawl live state (selectdb `recrawl`, Host2 audit)

**Docs by state (11-08 08:03–08:17).**

| State | Docs | Share |
|---|---|---|
| fetched | 53,058,440 | 82.9 % |
| — with real text | 44,259,768 | 69.2 % |
| — WAF 202 | 1,570,786 | 2.5 % |
| — shared challenge page | 459,295 | 0.7 % |
| — status 200, empty text | 1,378,469 | 2.2 % |
| pending | 5,459,316 | 8.5 % |
| failed | 4,492,090 | 7.0 % |
| gone | 951,105 | 1.5 % |
| dispatched | 45,120 | 0.1 % |
| **Total** | **63,999,416** | |

**Normal crawl against its baseline** (sum over 16 workers of normal req/s ÷ the 24-hour median before 11-02; `~/isel/work/tools/audit.jsonl`, every 2 h).

| Day | Median ratio | API req/s (median) |
|---|---|---|
| 11-02 | 0.31 | 257 |
| 11-03 | 0.23 | 280 |
| 11-04 | 0.23 | 292 |
| 11-05 | 0.54 | 88 |
| 11-06 | 0.54 | 45 |
| 11-07 | 0.46 | 28 |
| 11-08 | 0.82 | 25 |

Workers 7–15 also lost normal requests to a crawler bug (WebCrawler #125), fixed in the 11-07 18:20 crawler deploy. So the ratio before 11-08 is not caused by the recrawl alone.

### E. Links

| What | Where |
|---|---|
| Interactive diagrams for §2 | [https://share.onorca.dev/a/s4h7TZpOZL_5](https://share.onorca.dev/a/s4h7TZpOZL_5) (source `docs/reports/2026-midterm-technical.html`) |
| Selection pipeline (merged) | NTU-CSIE5376/WebSearchEngine #8–#18 |
| Recrawl client (merged) | NTU-CSIE5376/WebSearchEngine #17, #25, #26, #37, #38; policy fix #29 |
| Recrawl client (in review) | NTU-CSIE5376/WebSearchEngine #36; issues #31–#35 |
| goto links in golden sets (Metric team, draft) | NTU-CSIE5376/WebSearchEngine #39 |
| Full-text index (open) | NTU-CSIE5376/WebSearchEngine #22 (build, eval), #30 (ranking, draft) |
| YugabyteDB reader (open) | NTU-CSIE5376/WebSearchEngine #23 |
| Design | `docs/DESIGN.md`; WSE `IndexSelectionV2/docs/RECRAWL.md`, `TEXT_INDEX.md` |
| Experiments | `docs/experiments/` (backtest 10-09, refit 10-11, engine 10-22, text index 10-23, text rank 11-07); ranking round 2 on branch `exp/rank-r2`, `docs/experiments/2026-11-08-rank-r2/` |
| Issues found in operation | `docs/issues/2026-11-07-non-text-index.md`, `docs/issues/2026-11-08-recrawl.md` |
| Weekly reports | `docs/reports/2026-W41`, `W42`, `W43` |

### F. References

Each reference is cited in the text by its number.

| # | Reference | Where it is used |
|---|---|---|
| 1 | S. Robertson, H. Zaragoza. *The Probabilistic Relevance Framework: BM25 and Beyond.* Foundations and Trends in IR 3(4), 2009 | BM25 formula and its tf saturation and length normalization (§2.4) |
| 2 | K. Järvelin, J. Kekäläinen. *Cumulated gain-based evaluation of IR techniques.* ACM TOIS 20(4), 2002 | nDCG@10 in the engine choice (§2.4) |
| 3 | X. Zhang et al. *MIRACL: A Multilingual Retrieval Dataset Covering 18 Diverse Languages.* TACL 11, 2023 | NanoMIRACL questions for the engine choice (§2.4) |
| 4 | W. Kraaij, T. Westerveld, D. Hiemstra. *The importance of prior probabilities for entry page search.* SIGIR 2002, pp. 27–34 | Query-independent priors such as URL form help find entry pages (§2.4) |
| 5 | N. Craswell, S. Robertson, H. Zaragoza, M. Taylor. *Relevance weighting for query independent evidence.* SIGIR 2005, pp. 416–423 | Adding a transformed static feature to BM25; log, saturation and sigmoid forms (§2.4) |
| 6 | M. Richardson, A. Prakash, E. Brill. *Beyond PageRank: machine learning for static ranking.* WWW 2006 | Static rank from page and link facts (§2.2, §2.4) |
| 7 | X. Long, T. Suel. *Optimized query execution in large search engines with global page ordering.* VLDB 2003, pp. 129–140 | Combining term scores with a global page order; candidates by global order (§2.4) |
| 8 | G. Cormack, C. Clarke, S. Büttcher. *Reciprocal rank fusion outperforms Condorcet and individual rank learning methods.* SIGIR 2009 | Rank fusion candidate (§2.4) |
| 9 | B. Efron, C. Morris. *Data analysis using Stein's estimator and its generalizations.* JASA 70(350), 1975 | Shrinking small groups toward a parent rate (§2.2) |
| 10 | G. Casella. *An introduction to empirical Bayes data analysis.* The American Statistician 39(2), 1985 | Empirical-Bayes rates with a prior from the data (§2.2) |
| 11 | G. Ke et al. *LightGBM: A highly efficient gradient boosting decision tree.* NeurIPS 2017 | Selection model (§2.2) |
| 12 | B. Efron, R. Tibshirani. *An Introduction to the Bootstrap.* Chapman & Hall, 1993 | Paired bootstrap intervals (§2.4) |
| 13 | P. Westfall, S. Young. *Resampling-Based Multiple Testing.* Wiley, 1993 | Max-statistic simultaneous interval over a family (§2.4) |
| 14 | M. Smucker, J. Allan, B. Carterette. *A comparison of statistical significance tests for information retrieval evaluation.* CIKM 2007 | Bootstrap and randomization tests for IR comparisons (§2.4) |
| 15 | G. Cawley, N. Talbot. *On over-fitting in model selection and subsequent selection bias in performance evaluation.* JMLR 11, 2010 | Why a pick from many candidates needs data it did not see (§1.5, §2.4) |
| 16 | Google site diversity change, announced 2019-06-06; reported in [Search Engine Journal](https://www.searchenginejournal.com/google-site-diversity-change/) and [Engadget](https://www.engadget.com/2019/06/07/google-search-top-results-update/) | At most two results per site in most cases (§2.4, host cap) |
| 17 | M. Brooker. [*Exponential Backoff And Jitter.*](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) AWS Architecture Blog, 2015 | Capped exponential backoff for retries (§2.3) |
| 18 | M. Nottingham, R. Fielding. RFC 6585, *Additional HTTP Status Codes*, 2012 | 429 Too Many Requests (§2.3) |
| 19 | R. Fielding, M. Nottingham, J. Reschke. RFC 9110, *HTTP Semantics*, 2022 | 503 Service Unavailable and `Retry-After` (§2.3) |
| 20 | J. Cho, H. Garcia-Molina. *Effective page refresh policies for Web crawlers.* ACM TODS 28(4), 2003 | Freshness against a fixed crawl budget (§2.3) |
| 21 | OpenSearch documentation: [rescore](https://docs.opensearch.org/latest/query-dsl/rescore/) and [`rank_feature` query](https://docs.opensearch.org/latest/query-dsl/specialized/rank-feature/) | `rescore` window and `score_mode`; `rank_feature` log function (§2.4) |
