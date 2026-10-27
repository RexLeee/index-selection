# Weekly Report — 2026/10/26 (10/20–10/26) — Index Selection

**Role:** Index Infra (index selection). **Times:** UTC. **Status on 10-27 (checked 08:40):** the 128M list is still live on selectdb `:5445`, and all 11 upstream PRs are merged. The text index is ready but not deployed. Coverage cannot grow: crawlerdb has been down since 10-26 because Host1 `/data` is full, and WebCrawler PR #84 is still open.

## 1. TL;DR

1. **The whole pipeline is upstream.** The reviewer merged all 11 PRs (#8–#18) by 10-21. The recrawl client follows PR #84's new job layout.
2. **A full-text index is ready for Host2, at 0.13 TB for 128M pages.** On golden batch 20 it projects a ranked rate (golden URLs in their keyword's top 10) of **0.190** (head) and **0.176** (random). Both are upper bounds.
3. **Coverage is blocked outside isel.** crawlerdb has been down since 10-26 because Host1 `/data` is full. PR #84 is still open, and its 10-26 version gives API work strict priority by default.

## 2. Impact

| Area | Before this week | After this week | Why it matters |
|---|---|---|---|
| Upstream delivery | 2 of 10 PRs merged (#8, #9) | **11 of 11 merged** (#8–#18), the last on 10-21 | The course repo holds every step, from the database read to the recrawl |
| Recrawl client | Built for PR #84 `4dce38f`, not committed | Follows `bd40191`. 242 unit and 35 integration tests and a run against the real API pass | It can start on the day #84 is deployed |
| Ranked rate, official-style (batch 20, test half, local) | — (the Metric writes ranked = 0) | head **0.190**, random **0.176** | First estimate of the course's ranked number |
| Present golden pages in the top 10 (test half) | 0.761 (`combined_fields`) | **0.811** (+0.050, 95 % CI [+0.040, +0.061]) | More listed pages reach the first result page |
| Index size for 128M pages | 0.24 TB (body with word positions) | **0.13 TB** (body without positions, same top 10) | Half the space on a disk the crawler shares |
| Rebuild disk peak for 128M pages | 0.38–0.57 TB | **0.27–0.46 TB**, and the build checks free space before it loads and merges | A rebuild stops before the flood stage (95 % full, where OpenSearch blocks writes) |

In the last three rows, "before" is the first version I built this week.

## 3. Context: last week's plan and this week's questions

- **Situation:** on 10-18 the list was live, and the official Metric measured 24.8 % (head) and 23.0 % (random) indexed. The recrawl client waited for PR #84.
- **Complication:** this week PR #84 changed its job layout once and its scheduling three times. The ranked number had no index behind it. Host1 `/data` filled up again.
- **Questions:**
  1. Which search engine fits on Host2 beside the crawler, and how well does it rank batch 20's golden pages?
  2. Can PR #84's scheduler serve the recrawl and the normal crawl together?

**Last week's plan (W42) and what happened:**

| W42 item | Result | Note |
|---|---|---|
| Run forward-eval baselines on batch 20 | Deferred | It needs a `derive-positives` run on Host2, and the text index took the week. I will run batches 20 and 21 together |
| Formal approval on #10, then #11–#17 | Done | #10–#18 merged from 10-18 to 10-21, with four follow-ups (§9) |
| Commit the `4dce38f` alignment | Changed | #84 moved on, so I committed the `bd40191` version (§4.2) |
| Migrate `:5445` to 0003, run `recrawl-sync` | Deferred | Both wait for the #84 deploy. Host2 has no `api` container |
| Run a 1–5M page trial | Deferred | #84 is not merged, and the crawler is idle |
| Ask: free space on Host1 `/data` | Not met | Free space rose to 778 GB by 10-21, then fell about 8 GB/h. crawlerdb hit DiskFull on 10-25 23:54 |
| Ask: dates for the crawler restart and #84 | Partly | The crawler restarted on 10-20 03:33 (about 360 req/s). #84 has no merge date |
| Ask: a per-host share in #85, from 1:1 | Changed | #84 chose its own scheduling (§4.5). #85 is unchanged since 10-18 |
| Ask: accept a top-ranked-first cycle 1 | Open | On 10-25 the TA set the goal: the whole list in two weeks (§8) |

Correction to W42: `:5445` runs on Host1's root disk (601 GB free). The recrawl schema does not need `/data`.

## 4. What changed and why

### 4.1 Upstream delivery: the stack is merged (process change)

- **Problem:** last week only 2 of 10 PRs were merged. Part 3 (#11) also carried hooks for a pseudo golden set that we no longer build.
- **Change:** I added #18 to remove those hooks and rebased #12–#17 on it. In the isel repo, `d53a628` removed the pseudo golden code (−1,776 lines).
- **Why:** each PR keeps one topic, and the merge order follows the pipeline. So #18 had to merge before #12.
- **Result:** the reviewer approved and merged #10–#18 from 10-18 to 10-21. The approvals list four follow-ups, and I have not opened them yet (§9).

### 4.2 Recrawl client follows PR #84's partitioned jobs (architecture change)

- **Problem:** PR #84 `bd40191` (10-20) splits each job into one partition per crawler. It creates `results/{job}/` at POST time, and only the API knows the job status. Our archive took an existing directory as a finished job, so it would take every job at once and fail on each.
- **Change:**
  - A job becomes `completed` only when the API's GET says so. The planner polls every 60 s.
  - The archive reads the URLs and crawler IDs from `request.json`, then each crawler's partition.
  - The planner stops claiming when results are not mounted, or when 48 completed jobs wait for the archive.
- **Why:** a job spans many crawlers, so no single directory shows that the job is done.
- **Result:** 242 unit and 35 integration tests pass, and so does an end-to-end run against `bd40191`'s real API. #17 merged it. The 10-26 head `14e15be` keeps the fields we read, but I have not rerun the end-to-end test on it.

### 4.3 Full-text index on OpenSearch (architecture change)

- **Problem:** the list had no search index, so the course's ranked number could not exist. Host2's `/data` also holds the crawler's 8-hour buffer (5.42 TB on 10-23).
- **Change:**
  - I wrote the engine rule before the runs. It tests quality on NanoMIRACL (a multilingual retrieval benchmark), index size and memory. OpenSearch 3.9 with the ICU analyzer (Unicode word segmentation) passed.
  - I tuned the query on half of batch 20's keywords and tested it once on the other half. The result is `best_fields` over title, description, body and the URL's words.
  - The body no longer stores word positions (`index_options: freqs`).
  - A review fixed 12 build problems. The worst one: a rebuild kept three copies of the index on disk until the merge ended.
  - I wrote the Host2 compose files: loopback only, a 31 GB heap and one merge thread. Nothing is deployed.
- **Why:** the list is multilingual, and Japanese decided the engine (+0.26 nDCG@10 on Japanese). On Host2, the limit is the disk peak during a rebuild, not the size of one index.
- **Result:** §2 and §7 give the numbers. 286 unit tests and a 3-shard integration test pass.

### 4.4 Engine survey: why not Typesense (decision)

- **Problem:** the professor first proposed Typesense. The old spec estimated 100–200 GB of RAM for it, from book metadata (28M books in 14 GB).
- **Check:** Typesense keeps the whole inverted index in RAM: 2–3 times the searchable text. Our pages hold 4.3 KB of searchable text each, so 128M pages are about 550 GB.
- **Result:** Typesense needs **1.1–1.65 TB of RAM**. Host2 had 268 GB free on 10-22, and each host has 755 GB in total. Above 85 % RAM use, Typesense refuses writes, so the index cannot even be built.
- **Other reasons:**
  - Typesense is built for search-as-you-type on short fields. Long pages with BM25 ranking suit Lucene or Tantivy engines.
  - A full rebuild every two weeks hits a known disk growth problem (Typesense issue #2005).
  - WSE's `TypesenseRankMeasure` does not bind us: its `test()` is commented out, and it never writes `ranked_num`.
  - Title and description only would need 80–115 GB, but golden keywords that appear only in the body would be lost.

Survey of nine engines (source: `docs/experiments/2026-10-22-index-engine-selection.md` §4–§7):

| Engine | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| **OpenSearch 3.9** | Lucene BM25; ICU and per-language analyzers; delete by `doc_id`; atomic alias swap | JVM, heap ≤ 31 GB; 3.7 KB/doc at 2.4k pages before tuning | **Chosen** |
| Quickwit | ~100 MB RAM; 2.1 KB/doc, about 45 % smaller | BM25 is opt-in; the official image has no Japanese segmentation; splits are immutable | Fallback if disk or RAM runs short |
| Typesense | Typo-tolerant, simple to run | Index in RAM: 1.1–1.65 TB for 128M pages | Not feasible |
| Meilisearch | Good multilingual tokenizer | Rule-based ranking, not BM25; bulk loads crash without RAM | Not for long pages |
| Manticore | BM25, ICU and jieba | Only vendor benchmarks | Third choice |
| ParadeDB `pg_search` | SQL, Tantivy inside | Text and index do not fit in `:5445`'s 601 GB; Quickwit's language limits | Not feasible |
| Vespa | Full-featured, real-time updates | Needs config servers and content nodes | Too heavy |
| Anserini / Pyserini | Lucene BM25, the IR reference | No online service; a rebuild for every change | Offline baseline only |
| Tantivy library | Fast BM25 core | We would write the service layer; Quickwit's language limits | Not recommended |

### 4.5 PR #84 scheduling: measured with a replay (process change)

- **Problem:** PR #84 `00b2353` (10-21) alternates normal and API requests 1:1, with no limit per host. In Scrapy, a request that waits for its host's delay still holds one of the process's 256 slots. On Discord, the options were a ratio, a higher API priority, or a job choice by KL divergence (a measure of how much two distributions differ).
- **Change:** I replayed the scheduler with real Scrapy 2.16 at 1/4 scale (64 slots) and jobs shaped like isel's. I also sampled Host2's logs on 10-25.
- **Why:** the options needed numbers. On Host2, the normal crawl sent only 0–0.19 req/s to the list's largest hosts. So those hosts have spare rate, but the shared slots do not.
- **Result:**
  - With `00b2353`, the normal crawl falls from 34.8 to 5.4 req/s (−84 %). API requests that wait hold 45 of the 64 slots.
  - A higher API priority changes nothing (5.4 / 5.2 req/s). KL ranking cannot help: isel jobs never share a host, so the value is infinite for every job.
  - A cap of 2 API requests per host, with lookahead (skip to the next host when one is full), gives 18.7 (normal) and 18.2 (API).
  - On 10-26, PR #84 added a per-host cap (16 in production) and a lookahead. It also made API work go first by default (§8).

## 5. Architecture and process changes

**Pipeline.** The steps marked `+` are new this week:

```text
pull-full → score-full → select-full → publish → swap → verify-live      (unchanged)
recrawl-plan → PR #84 API → crawler → results/{job}/crawler_NN/
  + plan polls GET every 60 s → completed → recrawl-archive → text Parquet → DELETE
  + text-index-build  (text Parquet → new OpenSearch index → merge → alias swap)
  + text-index-eval   (golden keywords → top 10 → recall and ranked rate)
```

**Text index rebuild, before and after the review:**

```text
Before (8101a52)
  load → wait for leases → force merge every shard → refresh once → swap alias
After (1dd8078)
  + check free space: 2 × rows × 1,003 B, and keep the 5 % flood margin
  load → wait for leases (a timeout stops the build) → flush
  + check free space: the new index's size
  force merge one shard at a time, + refresh on every poll → swap alias
```

**PR #84 dispatch in one crawler process:**

```text
00b2353 (10-21)
  normal ⇄ API, 1:1 → downloader (256 slots)
  API refill: oldest job first, up to 256 requests, no per-host limit
14e15be (10-26)
  API (manual_first) → normal ⇄ API (balanced), 1:1 → API (normal_first) → downloader
  API refill: up to 64 jobs, ≤ 16 requests per host, longest remaining work first
  policy: one setting in the API config, default manual_first
```

## 6. Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| OpenSearch 3.9 with ICU, not Quickwit | It met the rule written before the runs: +0.010 nDCG@10 [+0.001, +0.017], 0.23 TB at 128M, and it fits Host2's memory | A JVM node with a 31 GB locked heap. On Chinese alone, Quickwit scores 0.032 higher |
| `best_fields` with the URL's words | A title that names the query beats a long page that repeats one word. URL slugs carry words | It was chosen on one batch; batch 21 (11-01) is the first independent check. +55 B per doc |
| Body without word positions | BM25 (the ranking function) uses term counts, not positions. The top 10 matched on all 1,948 keywords | A phrase query on the body now fails with HTTP 400 |
| On Host2: no previous index kept, loopback only, no security plugin | A second copy does not fit beside the crawler's buffer. The crawler API uses the same model | No instant rollback. Any Host2 user can delete the index, and a rebuild from Parquet takes hours |
| Job completion comes from the API's GET, not from files | A job spans many crawlers. Only the API sees every partition | The planner depends on the API. When the API does not answer, the round ends |
| Drop the pseudo golden set | It is no longer needed, and production never used it | Only official batches can test the model. The next one is batch 21 |

## 7. Evidence

**Golden batch 20, counted the official way.** The local index holds the on-list golden pages and 44k crawled pages. The rows use the test half of the keywords. Source: `docs/experiments/2026-10-23-text-index/results/review/official.json`.

| Tag | Indexed (official flag) | On list, text fetched | Of those, in top 10 | Ranked rate, 95 % CI |
|---|---|---|---|---|
| head | 26.5 % | 90.2 % | 79.4 % | **0.190** [0.173, 0.207] |
| random | 23.3 % | 88.9 % | 85.0 % | **0.176** [0.160, 0.191] |

The ranked rate is the product of the first three columns. It is an upper bound. The top-10 share falls about 1.5 points per doubling of the index, and 128M is about 11 doublings above this index. So I give no 128M number.

**PR #84 replay.** Scrapy 2.16.0 at 1/4 scale (64 slots), jobs shaped like isel's, rates in req/s. The scripts are not committed yet (§9).

| Scheduler | Normal | API |
|---|---|---|
| Normal crawl only | 34.8 | — |
| `00b2353`: 1:1, no per-host limit | 5.4 | 5.2 |
| 1:1, cap of 2 per host, lookahead | 18.7 | 18.2 |
| API first, cap of 2 per host | 10.1 | 20.3 |

## 8. Risks and asks

| Risk | Impact | Ask | Owner |
|---|---|---|---|
| Host1 `/data` is full (0 B free on 10-27). crawlerdb has refused writes since 10-25 23:54 and has crash-looped since 10-26 07:01 | The crawler has fetched almost nothing since about 10-26 00:00. The Metric reads crawlerdb for batch 21 (11-01 12:00) | Free space and restart crawlerdb before 11-01. Add a disk alert at 90 % | TA |
| PR #84 `14e15be` gives API work strict priority by default. Its doc says this "can starve lower-priority queues under continuous load" | API work may hold up to 16 requests per host and all 256 slots of a process. isel keeps up to 48 jobs in flight | Pick the policy for the first trial and a minimum rate for the normal crawl. I will size `max_jobs_inflight` from the trial | TA, crawler team |
| The goal is the whole list in two weeks. At 0.2 qps per host, 14 days reach at most 123.0M of 128M (96.1 %) | 57 hosts with 18.8M URLs need more than 14 days. www.espn.com needs 38 | Accept that cycle 1 ends at 96 % or less, and that those hosts finish in cycle 2 | Professor, TA |
| The Metric writes ranked = 0 and runs on Host1. The index will listen only on Host2's loopback | Our ranked number cannot count, even after the deploy | Decide how the Metric reads ranked: a Host2 port, or flags that an isel job writes | TA |
| crawlerdb may move to YugabyteDB, which has no `ctid` | `pull-full` reads by `ctid` ranges and would stop working | Tell us before a cutover, and keep a PostgreSQL copy for the next full read | TA |

## 9. Next week

1. Open the four review follow-ups on RexLeee/WebSearchEngine. Fix the publish retry first; the reviewer wants it before the next production publish.
2. Rerun the recrawl end-to-end test on PR #84 `14e15be`. Remove the stale "128 slots per process" text from the design docs.
3. Replay `14e15be`'s default policy, and commit the replay scripts to `docs/experiments/`.
4. Push `idxsel/p10-text-index` and open the text index PR (needs approval).
5. After batch 21 (11-01), if crawlerdb is back: test the query on batch 21, and run forward-eval on batches 20 and 21.

Blocked until #84 is deployed: migrate `:5445` to 0003, run `recrawl-sync`, and run the 1–5M trial. Each step needs approval.

## 10. Links

| What | Where |
|---|---|
| Upstream PRs | NTU-CSIE5376/WebSearchEngine #8–#18 (merged); text index branch `idxsel/p10-text-index` (local) |
| Crawler side | NTU-CSIE5376/WebCrawler PR #84 (head `14e15be`), issue #85 |
| Design and runbook | `docs/RECRAWL_DESIGN.md`; WSE `IndexSelectionV2/docs/TEXT_INDEX.md` and `docs/RECRAWL.md` |
| Experiment records | `docs/experiments/2026-10-22-index-engine-selection.md` §10; `docs/experiments/2026-10-23-text-index/` |
| Live list | selectdb `:5445`, model run 2, live since 10-14 03:45; schema 0002 |
| Commits | `index-selection` `d53a628` … `6d62308` (6); WSE `8065fb6`, `b725936` (merged) and `8101a52` … `6483010` (10, local) |
