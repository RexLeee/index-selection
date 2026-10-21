# Weekly Report — 2026/10/18 (10/12–10/18) — Index Selection

**Role:** Index Infra (index selection). **Times:** UTC. **Status on 10-18:** the fixed 128M list is live on selectdb `:5445`, and the official Metric measured it. The recrawl client is ready, but it waits for WebCrawler PR #84 and a crawler restart.

## 1. TL;DR

1. **The fixed list is live.** I fixed last week's label leak, refroze the model (M0') and published 128M URLs with a 1.2 ms swap.
2. **The official Metric measured it on batch 20,** the first batch created after the model was frozen: **24.8 %** (head) and **23.0 %** (random) of golden URLs are indexed. That is 95 % and 91 % of what the crawler has.
3. **Selection is no longer the bottleneck. Crawl coverage is.** The next step is the recrawl. It now reuses the crawler team's job API (PR #84) instead of a new crawler lane, and is tested against the real PR code.

## 2. Impact

| Area | Before this week | After this week | Why it matters |
|---|---|---|---|
| Official measurement | Metric read V1's `:5444` and hardcoded `indexed = False`. No list was measured | Batch 20: **24.8 % head, 23.0 % random** indexed | The course dashboard now counts our work |
| Selection quality | Unknown out of sample | The list holds **95 % / 91 %** of the golden URLs that were crawled | Better ranking can add at most 1.2–2.2 pt. More coverage can add much more |
| Model vs V1's features | — | **+1.84 pt** head, **+1.64 pt** random (backtest, K = 128M) | The new training method beats V1's approach on the same data |
| List correctness | The leak gave 38M slots to boosted small-domain pages | Those **38M slots (30 % of the list)** go to pages the model ranks fairly | The backtest moved only 0.1–0.2 pt, but the published list changed a lot |
| Availability | — | Swap lock wait **1.2 ms**, no reader blocked | We can publish a new list at any time outside the Metric window |
| Recrawl storage | Plan: keep raw HTML as WARC, **5–6 TB** | Keep extracted text only, **0.35–0.4 TB** | 12–17× less disk on a host with about 2 TB free |
| Cross-team cost | 10 change requests to the crawler team (C1–C10) | **1** open request (lane allocation, WebCrawler #85) | The crawler team ships one API; we own the rest |
| Review cost | One PR with 49 files and +9,550 lines | **10 stacked PRs**; 2 merged, #10 approved in a comment | Reviewers read one topic at a time |

## 3. Context: what this week had to answer

- **Situation:** last week the full 128M pipeline ran end to end for the first time.
- **Complication:** right after scoring, I found a label leak. The list could not be published, and batch 20 (10-16) was the only forward test this month.
- **Questions:**
  1. Can the model be fixed and frozen before batch 20 exists?
  2. Can the list go live, and can the official Metric read it, before 10-16?
  3. How do we get page text for 128M URLs at 0.2 requests per second per host?

## 4. What changed and why

### 4.1 Model: three layers of label leakage fixed

- **Problem:** some features told the model how a row entered the training data. That depends on the label.
- **Change:**
  - Every row now counts itself in its domain statistics (`2352caa`).
  - Sampling hashes single rows instead of whole heap pages.
  - One half of the sample builds the domain statistics, and the other half trains (`6022068`).
- **Why:** a test on sample rows alone cannot see a leak through sample membership. I added a check that compares the kept share of the full table with the sample.
- **Result:** M0' was frozen on 10-11. The kept share now matches the sample (z = −1.42, limit 6).

### 4.2 Publish path: versioned schema and atomic swap (process change)

- **Problem:** handwritten SQL created the tables, and schema changes had no history. The Metric reads our live table, so a half-built table must never be visible.
- **Change:** Alembic manages the schema, `publish` loads a new table, and `swap` renames it into place. Then `verify-live` reruns the Metric's own SQL.
- **Why:** every schema change can be upgraded and rolled back, and readers see either the old list or the new list.
- **Result:** the list has been live since 10-14 03:45. `verify-live` matches the official numbers to within 0.1 pt.

### 4.3 Upstream delivery: stacked PRs (process change)

- **Problem:** the reviewer could not review a 49-file PR.
- **Change:** I split it into WSE #8–#17, one topic each, and each part links a diff of only its own changes.
- **Why:** each part can be merged on its own, and the order follows the pipeline.
- **Result:** #8 and #9 are merged, and #9 made the Metric read `:5445`. The TA gave #10 an LGTM with two follow-ups. I checked them in the code, opened RexLeee/WebSearchEngine#2, replied, and marked #10 ready.

### 4.4 Recrawl: from a new crawler lane to a client of PR #84 (architecture change)

- **Problem:** the list has only URLs. The crawler keeps HTML for 8 hours and never fetches a page twice, so the index has no text.
- **Change:** isel sends batches of URLs to PR #84's job API, reads the results, saves only the text, and then deletes the job.
- **Why:** PR #84 already gives jobs, result files, a TTL, DELETE and FIFO order. A separate lane would duplicate the crawler's politeness logic.
- **Result:** 287 unit tests and 31 integration tests pass, and an end-to-end test against PR #84's own code at `4dce38f` passes too.

## 5. Architecture and process changes

**Pipeline.** The steps marked `+` are new this week:

```text
pull-full → score-full → select-full
  + migrate-selectdb   (Alembic; refuses schema drift)
  + publish            (load selected_urls_r<id> in one transaction)
  + swap               (rename into selected_urls_current; refused during the Metric window)
  + verify-live        (Metric's SQL, run as the read-only select role)
  Metric cron (1st/16th) now reads :5445 instead of V1's :5444
```

**Recrawl architecture, before and after:**

```text
Before (proposal C1–C10, 10-13)
  isel → queue files in /data/ipc → new recrawl lane in the crawler
       → shared per-host gate → WARC writer (5–6 TB) → new API resource for status

After (10-18, PR #84 at 4dce38f)
  recrawl-sync    : live list → recrawl.host / doc / job   (selectdb :5445)
  recrawl-plan    : POST urls, ttl_days=3 → PR #84 API → crawler (FIFO jobs)
  crawler         : results/{job_id}/ (one rename per job)
  recrawl-archive : results → text Parquet in /data/isel/text (read only)
  recrawl-plan    : DELETE the job once its text is saved
```

**Job life:**

```text
claimed → submitted → applied → deleted
              │
              └── 404, or TTL passed → lost → the docs go back to the queue
```

## 6. Key decisions

| Decision | Why | Trade-off |
|---|---|---|
| Keep extracted text, not raw HTML | An inverted index needs only text. Host2 has about 2 TB free while the crawler runs | We cannot re-extract text later with a better parser |
| Reuse PR #84 instead of a new lane | The crawler team owns politeness and routing. isel needs no root and no `/data/ipc` writes | API requests run at `priority=-1` and stall on busy hosts (#85) |
| Match results by position, not by URL | The API rewrites URLs (w3lib), so string matching would lose docs without an error | Any reorder halts the planner. It fails loudly, by design |
| The TTL (3 days) is the only job deadline, and isel deletes jobs after use | One deadline instead of two. The result directory holds at most 144k pages | An expired job is refetched, not recovered |
| Alembic for the selectdb schema | Versioned and reversible changes; drift is refused | Runtime SQL (`COPY FREEZE`, rename) stays in psycopg, outside the ORM |

## 7. Evidence

**Batch 20, official Metric** (M0' was frozen 10-11; batch 20 was created 10-16):

| Tag | Golden URLs | Crawled (ceiling) | Indexed | 95 % CI | Share of ceiling |
|---|---|---|---|---|---|
| head | 7,329 | 26.0 % | **24.8 %** | 23.2–26.1 % | 95 % |
| random | 8,031 | 25.2 % | **23.0 %** | 21.7–24.3 % | 91 % |

**Forward result against the backtest** (share of the crawled ceiling):

| | head | random |
|---|---|---|
| Backtest, batches 6–19 | 92–98 % | 81–96 % |
| Forward, batch 20 | 95 % | 91 % |

The forward result falls inside the backtest range on both tags. Random is higher in absolute terms (23.0 % against 12.1 % on batch 19) because more of batch 20 was crawled.

## 8. Risks and asks

| Risk | Impact | Ask | Owner |
|---|---|---|---|
| Host1 `/data` is 97 % full (229 GB free) | The recrawl schema needs about 37 GB there. A full disk stopped crawlerdb on 10-05 | Free space on Host1, or approve another disk | TA |
| The crawler has been stopped since 10-06, and PR #84 is not merged | No recrawl, and coverage cannot grow before batch 21 | A date for the restart and for the #84 deploy | TA, crawler team |
| API requests yield to normal requests on busy hosts | The 10 largest hosts on our list get almost no recrawl budget | Agree on a per-host share in #85, starting at 1:1 | Crawler team |
| 14 days reach about 108M of 128M pages | The first cycle is partial | Accept a top-ranked-first recrawl for cycle 1 | Professor, TA |

## 9. Next week

1. Run the baselines on batch 20 (`forward-eval`), and write the experiment record.
2. Get a formal approval on #10, then rebase and open #11–#17 one at a time.
3. Commit the PR #84 `4dce38f` alignment on `feat/recrawl`.
4. After Host1 has space: migrate `:5445` to revision 0003 and run `recrawl-sync`. Each step needs approval.
5. After #84 is deployed: run a 1–5M page trial, and measure job latency, text size and the per-host rate.

## 10. Links

| What | Where |
|---|---|
| Upstream PRs | NTU-CSIE5376/WebSearchEngine #8–#17; follow-up RexLeee/WebSearchEngine#2 |
| Crawler side | NTU-CSIE5376/WebCrawler PR #84 (job API), issue #85 (lane allocation) |
| Design and runbook | `docs/DESIGN.md`; `docs/RECRAWL_DESIGN.md` (branch `feat/recrawl`) |
| M0' refit record | `docs/experiments/2026-10-11-hash-sample-refit.md` |
| Live list | selectdb `:5445`, model run 2 = select-full run `20261014T030855450365Z` |
| Commits | `index-selection` `2352caa` … `9733d47` (18), plus the uncommitted `4dce38f` alignment |
