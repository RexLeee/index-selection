# Index Selection 調研報告

- 日期：2026-10-06
- 範圍：現況（SSH 到 ntu-sa1、ntu-sa2 唯讀調研）、論文、方法選型、未來 recrawl 與 inverted index 架構
- 信心等級：HIGH 是多個來源或直接引用；MED 是單一權威來源；LOW 是社群討論或推論
- 標 † 的 DOI 或網址，這次沒有重新驗證，來自調研 agent 的記憶

---

## 1. 結論

1. **index selection 化簡成一個打分問題。** 官方 metric 是 URL 層的 recall，而 recall 對 URL 可加。所以最佳清單就是 P(u ∈ 下一批 golden | 特徵) 最高的 K 個。
2. **採用兩層的 golden 傾向模型。** 網域層用 empirical Bayes 平滑 golden 比例，頁面層用 LightGBM 學同網域內的對比。依據是 fRank（WWW 2006）和 query-view pruning（TOIS 2012）。信心 HIGH。
3. **洩漏是這題最大的風險，比演算法選擇更重要。** crawler 會把 golden 注入回去，所以 `source`、`url_score`、`domain_score` 等欄位在回測中是錯的值。解法是用 `last_fetch_ok` 重建每個 batch 當時的候選池。
4. **排序的改進空間有限。** 只選已爬頁時，head 的上限約 30%。V2 規則式已拿到已爬 golden 的 80–91%（head）。新方法的價值在於方法可解釋、評估站得住。
5. **報告給老師的主要數字是前瞻評估。** 在 batch 20（2026-10-16）前凍結模型和快照，再對新 batch 評估。

---

## 2. 問題定義

| 項目 | 內容 | 來源 |
|---|---|---|
| 清單大小 | 每兩週 K = 1.28 億個 URL | 老師要求（`INDEX_INFRA_WORK_SUMMARY.md:14`） |
| 候選池 | 已爬 URL，約 16.6 億列 | 使用者決定（2026-10-06） |
| golden 來源 | Google Trends 熱門查詢（10 個 geo、168 小時）。head 取頻率前 1000；random 從同一池均勻抽 1000 | `Metric/Query/*.py` |
| golden URL | SerpApi `engine=google, num=10`，不帶 `gl`/`hl`。每個 query 約 7.7–8 個 organic URL | 部署中的 `metric` 容器 |
| 判定規則 | golden 的原始字串完全等於清單某列 | `CrawlerAllMetricMeasure.py` |
| 量測時間 | 每月 1、16 日 12:00 UTC 量 random，18:00 量 head | `metric` 容器的 crontab |

**上限（2026-10-02/03 的 metric 數字）：** head 已發現 51.5%、已爬 30.6%；random 已發現 31.8%、已爬 19.0%。

---

## 3. 現況調研（2026-10-06）

### 3.1 主機與服務

| 主機 | 服務 |
|---|---|
| ntu-sa1（ubuntu1） | crawlerdb `:5432`、metricdb `:5433`、V1 selectdb `:5444`（3,200 萬列）、V2 selectdb `:5445`（10 萬列試跑）、`metric` cron 容器 |
| ntu-sa2（ubuntu2） | `crawler`、`scheduler_control`、`scheduler_ingest` 容器（程式在容器內 `/app`，repo `fmb123456/WebCrawler`，HEAD `2e4f87c`）、Superset、Grafana |

**今天的事故：** ntu-sa1 的 `/data`（7.0 TB）寫滿，crawlerdb 重啟約 10,291 次，log 是 `could not write to file "pg_wal/xlogtemp": No space left on device`。維運之後清出 447 GB，DB 已重新啟動。

### 3.2 crawler 的事實

- 每天成功抓取 26–29M 頁，成功率約 91%；每天新發現約 86M 個 URL。
- **每個 URL 只爬一次。** offerer 把選中的列設成 `should_crawl=FALSE`，沒有程式會設回 TRUE。所以 `last_fetch_ok` 就是那次爬取的時間。
- **頁面內容不落地。** spider 把 HTML 寫進 `/data/ipc/crawl_result` 的 JSONL，外部 job 8 小時後刪除。DB 只留 `title`、第一個 anchor、`content_hash`、長度。
- **shard 路由**（`libs/db/sharding/key.py`）：key 是白名單內的完整 host，否則是 eTLD+1。固定 overrides 是 wikipedia.org→0、x.com→32、youtube.com→64、reddit.com→96、instagram.com→128、espn.com→160、facebook.com→192、yahoo.com→224。其餘是 `int(md5(key), 16) % 256`。
- crawler 存 `canonicalize_url(response.url)`，也就是轉址後的網址。原始網址那一列不會有 `last_fetch_ok`。

### 3.3 洩漏來源

| 欄位 | 機制 | 位置 |
|---|---|---|
| `source`、`url_score` | `golden_inject.py` 對滿 4 週的 batch 執行 `ON CONFLICT DO UPDATE SET source=1, url_score=3.0` | `scripts/golden_inject.py:110-118` |
| `url_score` | crawler 自己的 LightGBM P(golden) ranker，用 batch 1–4 訓練 | `golden_discovery_ranker_v1` |
| `domain_score` | 依網域在 golden batch 出現的次數給 1.0、0.95、0.8 | `scripts/update_golden_domain_scores.py` |
| `parent_page_score` | router 把父頁的 `url_score` 附到每個 outlink | router `service.py:229-241` |
| `first_seen` | 注入時會重設 | V2 調研 |
| `inlink_count_*` | golden parent patrol 重抓父頁時會再 +1；不去重 | `WEIGHT_EVAL_REPORT.md` §9 |

### 3.4 V1（PR #4）的問題

| 面向 | 問題 |
|---|---|
| 演算法 | LightGBM 只用 56 個 URL 字串特徵加一個網域頻率；run 13 的 `best_iteration=1`（learning rate 0.5），test AUC 0.784，基本上是單棵樹 |
| 訓練資料 | 正例是最近 10 批 golden，負例是 100 萬個隨機列（含未爬頁）；train/test 隨機切，沒有時間切分；網域頻率特徵直接用 golden 算，沒有排除同批 |
| 規模 | K = 3,200 萬，老師要 1.28 億 |
| pipeline | 舊版 `indexSelection.py` 的 import 在第 10 行就失敗；新版每次整個覆寫 selectdb |
| 結果 | 只算已爬部分，batch 18 回測約 14.7% |

### 3.5 V2 的 feature 重要性結果（`WEIGHT_EVAL_REPORT.md`，樣本內）

- 網域先驗是最強的單一訊號；拿掉它，B18 head 下降 4.38 個百分點。
- 「有沒有外部連入」比「有幾個」重要。ext 的上限越低越好。
- root 有用：golden 中 15.6% 是首頁，全體已爬頁只有 0.2%。
- `url_score`、json_ld、hreflang、parent、no_title 有害。它們反映網站類型，在加總公式中會把 golden 網域上的非 golden 頁擠進清單。
- 過去 golden 在下一批重複出現的比例是 0.6–2.7%，比一般頁高 400 倍以上。
- B 版（只算 batch 前已爬的頁）中，V2 拿到已爬 golden 的 80–91%（head）和 65–80%（random）。

---

## 4. 論文調研

### 4.1 index selection 與分層索引

| 文獻 | 重點 | 對本題的意義 |
|---|---|---|
| Risvik, Aasheim, Lidal, "Multi-tier architecture for web search engines," LA-WEB 2003 | 索引分三層；最上層用靜態與 query log 證據挑文件 | 1.28 億清單就是最上層 tier |
| Ntoulas & Cho, SIGIR 2007（DOI 10.1145/1277741.1277776†） | 比較 keyword 與 document 層級的兩層索引剪枝 | 依 query-independent 熱門度選文件是可行策略 |
| Anagnostopoulos et al., "Stochastic query covering," WSDM 2011（TWEB 2015, DOI 10.1145/2699671） | 把選文件寫成 query 分佈上的隨機集合覆蓋；greedy 有近似保證 | 和本題目標最接近的形式化 |
| Baeza-Yates, Boldi, Chierichetti, WWW 2015（DOI 10.1145/2736277.2741100） | 約 0.4% 的頁面回答約 20% 的查詢 | 覆蓋高度集中，8% 預算夠用 |
| Garcia, Williams, Cannane, ACSC 2004 | 依 query log 命中頻率排 posting | 用 query log 建先驗有效 |
| Altingovde, Ozcan, Ulusoy, TOIS 2012（DOI 10.1145/2094072.2094074） | 加入 query view 的剪枝「大幅勝過」只用內容的剪枝 | 有 log 監督的選取勝過純啟發式 |

⚠️ 2024 年 Google 文件外洩的報導提到 Base、Zeppelins、Landfills 三層 tier，但只有 SEO 二手來源。信心 LOW。

### 4.2 query-independent 靜態排名

| 文獻 | 重點 |
|---|---|
| Richardson, Prakash, Brill, "Beyond PageRank," WWW 2006（DOI 10.1145/1135777.1135881） | fRank 用 RankNet 學 anchor、頁面、網域、熱門度特徵；pairwise accuracy 67.4%，超過機率線的幅度是 PageRank 的兩倍以上。不用連結圖的特徵就勝過 PageRank |
| Kraaij, Westerveld, Hiemstra, SIGIR 2002（DOI 10.1145/564376.564383） | URL 形狀（root、subroot、path、file）是 entry-page 搜尋最好的先驗 |
| Bendersky, Croft, Diao, WSDM 2011 | 內容品質特徵改善排序；recrawl 後可用 |
| Boldi & Vigna, Internet Mathematics 2014（arXiv 1308.2140） | 建議用 harmonic centrality；PageRank 不滿足部分公理 |
| Common Crawl host graph（2025 Q4：2.508 億 host、109 億條邊） | 每季發佈 host 層級 harmonic centrality 和 PageRank |

⚠️ Fetterly, Craswell, Vinay（SIGIR 2009）認為 PageRank 是最可靠的爬取選擇訊號；Richardson 2006 認為非連結特徵勝過 PageRank。沒有研究用 Google top-10 當目標比較，所以要自己測。

### 4.3 依搜尋影響排序爬取

| 文獻 | 重點 |
|---|---|
| Pandey & Olston, WWW 2005 | 依「對使用者看到的結果的影響」排程，同品質下資源少很多 |
| Pandey & Olston, WSDM 2008 | 估計未爬頁對 query workload 的影響；對 tail query 最有效 |
| Cho & Schonfeld, VLDB 2007（RankMass） | N 次下載後覆蓋多少 PageRank 質量的保證 |
| Olston & Najork, FnTIR 2010（DOI 10.1561/1500000017） | 爬取選擇是最大化重要度加權的覆蓋 |
| Fetterly et al., SIGIR 2009 | 用「可能達到的最大 NDCG」衡量爬取，這就是我們的上限欄位 |
| Ostroumova et al.（Yandex）, ECIR 2014 | 用 URL 和來源特徵預測新 URL 的熱門度和衰減 |

**對應到本題：** 他們的「影響」就是我們的 golden label。我們能直接觀察過去 query 的 label，但必須推廣到沒見過的 URL 和未來的 query。

### 4.4 學習問題的設定

- **PU learning。** Elkan & Noto（KDD 2008†）證明：若正例是完全隨機地被標上（SCAR），分類器輸出等於 p(y=1|x)·c；常數 c 不改排序。
  - ⚠️ 我們的 label 不是 SCAR：URL 要在 Google top-10 又被爬到才會有 label。Bekker & Davis（Machine Learning 2020†）處理這種 SAR 情況。
  - 本設計的負例是雜湊抽樣的 case-control，抽樣比例只平移 logit，top-K 排序不變。
- **top-K 目標。** 候選池就是整個語料，所以用 pointwise logloss 加負例抽樣最實際（Kar et al., ICML 2015†）。
- **GBDT 對神經網路。** 表格資料上樹模型仍然勝出（Grinsztajn et al., NeurIPS 2022, arXiv 2207.08815）。
- **稀疏網域比例。** 用 Beta-binomial empirical Bayes 往上層收縮（Agarwal et al., KDD 2010†）。
- **洩漏與回饋迴圈。** Kaufman et al.（TKDD 2012†）；Sculley et al.（NeurIPS 2015†）。

### 4.5 熱門查詢與新鮮度

- Dong et al., WSDM 2010：先偵測時效性查詢，再用專門的 ranker。靜態先驗會低估新頁。
- Lefortier et al., CIKM 2013（arXiv 1307.6080）：短命新內容大多從少數來源頁連出，價值幾天內衰減。
- 推論：fresh 配額要等能選未爬頁或有 recrawl 時才有意義。只選已爬頁時延後。

### 4.6 多樣性與去重

- Greedy max-coverage 有 (1−1/e) 近似（Nemhauser et al. 1978†）。
- SimHash-64、k=3 適用 80 億頁（Manku et al., WWW 2007）。需要內容，Phase B 之後才能用。
- DustBuster（Bar-Yossef et al., WWW 2007）只用爬取 log 學 URL 改寫規則。

---

## 5. 方法選型

| 方法 | 優點 | 缺點 | 需要的資料 | 複雜度 | 結論 | 信心 |
|---|---|---|---|---|---|---|
| A. 兩層 golden 傾向模型（EB 網域層 + LightGBM 頁面層） | 有 fRank、query-view pruning 支持；可擴到 16 億列；可解釋 | 會記住網域；對新頁和熱門頁弱 | 過去 batch 加上 as-of 特徵 | 中低 | **採用** | HIGH |
| B. base + fresh 雙配額 | 處理 head query 漂移 | 需要可靠的 first-seen；分配比例是超參數 | 爬取時間戳、新聞與 hub 網域 | 中 | 延後 | MED-HIGH |
| C. 網域 greedy 配額（yield curve） | 和目標一致；防止大網域吃掉預算 | 稀疏網域的曲線很吵 | 多個 batch | 中高 | 當 A 的對照 | MED |
| D. 連結中心性 baseline | 簡單、穩健 | 忽略 query 分佈 | 連結圖 | 低 | 只當 baseline | HIGH（當 baseline） |

**為什麼加總公式不夠：** V2 的結果顯示，json_ld、`url_score` 這類特徵在加總中有害。它們區分的是「哪個網域」，不是「網域內哪一頁」。兩層模型把網域層和頁面層分開，頁面層另外用網域內相對值（例如 inlinks 除以網域平均）。

---

## 6. 評估最佳實踐

1. **前推回測：** 用 batch < t 訓練，在 t 評估。絕不隨機切 URL（Kaufman 2012）。
2. **時間點重建：** pool_t = `last_fetch_ok` < 建立時間 − 30h。golden 衍生特徵只用 as-of 之前已存在的 batch。
3. **指標：** recall@K 對應官方 metric；同時報告已爬上限（Fetterly 2009 的作法）；head、random 分開。
4. **信賴區間：** 以 query 為單位的群集 bootstrap；和 baseline 做配對差異（Smucker et al., CIKM 2007†）。
5. **事先寫定的採用規則：** 在 t−1 調參、在 t 報告；配對 95% CI 下界 > 0 才採用。
6. **ablation：** 拿掉 inlinks、拿掉網域層、拿掉 URL 歷史、V1 複製版、同特徵的 logistic regression。

---

## 7. 陷阱

- **回饋洩漏：** 注入的 golden 有和 label 綁在一起的爬取歷史。主模型禁用這些欄位，用時間點候選池排除「因為是 golden 才被爬」的頁。
- **過度擬合 golden：** 每批約 1.5 萬個 label，熱門 URL 很少重複。看網域外推是否穩定。
- **拼法不一致：** golden 是原始 SERP 字串，crawler 存正規化字串。同時報告 exact 和 canonical，差距就是拼法損失。
- **樣本外數字只有前瞻評估才算數：** 回測的 inlink 特徵仍可能被 patrol 膨脹。

---

## 8. 未來架構調研（recrawl → inverted index）

### 8.1 開源與研究搜尋引擎怎麼選、怎麼存

| 系統 | 選什麼 | 怎麼存 |
|---|---|---|
| OpenWebSearch.eu OWI（ECIR 2024） | frontier service 分配爬取；核心索引是高流量網站 | WARC → Resiliparse on Spark → Parquet；輸出 `index.ciff.gz` 加 `metadata-N.parquet`，依日期與語言分區；`changelog.json` 處理下架 |
| ChatNoir（ECIR 2018） | 靜態語料（ClueWeb、Common Crawl） | Elasticsearch，130 節點、約 30 億頁 |
| Stract | 依 host harmonic centrality 給爬取預算，host 內依頁面 centrality | WARC → 每個 WARC 一個 Tantivy index 再合併。**repo 已在 2026-04-02 封存** |
| Marginalia | 小型、文字為主的網站 | 每網域一個 Parquet，之後改用自家 columnar 格式 |
| Common Crawl（2025-09） | HyperBall harmonic centrality 排 URL | WARC/WAT/WET 加 columnar Parquet URL index；23.9 億頁 |

**結論：** 我們的形狀（selection 分數 → manifest → 抓取 → WARC → 抽文字 Parquet → index）和 OWI、Stract 相同。

### 8.2 儲存與抽取

- 每頁平均（Common Crawl 2025-09）：壓縮 WARC 約 40 KB，壓縮 WET 純文字約 3.1 KB。
- 抽取：Resiliparse 比 Trafilatura 快。⚠️ DCLM（2024-06）說 8 倍；WCXB（2026-05）量到 3.5 倍（28 ms 對 97 ms/頁）。
- 近似重複：SimHash-64、k=3（Manku 2007）。

### 8.3 索引引擎

| 引擎 | 證據 | 增量更新 | 學生維運難度 |
|---|---|---|---|
| Lucene（Anserini/Pyserini） | ClueWeb22-B 英文 8,700 萬篇：4 個分區共 809 GB，取 1000 筆 p95 1.02 s（arXiv 2402.04357） | segment，通常批次重建 | 低 |
| OpenSearch/Elasticsearch | 官方建議每 shard 10–50 GB、少於 2 億篇 | 原生刪除與合併；alias 原子切換 | 中 |
| Tantivy | 500 萬篇 Wikipedia 桌機 3 分鐘內 | `delete_term` 在 commit 生效 | 中（要自己寫服務層） |
| PISA | ClueWeb12-B13 5,200 萬篇 11 小時 | 靜態；可匯入 CIFF | 研究 baseline |
| Vespa | 新文件 1 秒內可搜尋 | 即時 | 高 |

### 8.4 規模估算（1.28 億篇）

| 項目 | 估計 | 依據 |
|---|---|---|
| 原始 WARC | 約 5.1 TB | 40 KB/頁 |
| 抽出的文字 | 約 0.4 TB | 3.1 KB/頁 |
| 完整 Lucene index（位置與原文） | 約 1.2 TB | 9.3 KB/篇 |
| 精簡 BM25 index | 約 0.2 TB | ⚠️ 從 passage 的 6 倍比例外推 |
| 抽取運算 | 約 1,000 core-hour（128 核約 8 小時） | 28 ms/頁 |
| 14 天全量重抓 | 每天 9.1M 頁，佔 crawler 產能約 34% | 每天 26–29M |

---

## 9. 來源（存取日期 2026-10-06，除非另註）

- Hendriksen et al., "The Open Web Index," ECIR 2024 — https://downloads.webis.de/publications/papers/hendriksen_2024.pdf
- OWS Book, OWI Access 與 Preprocessing — https://openwebsearcheu-public.pages.it4i.eu/ows-the-book/
- Bevendorff et al., Elastic ChatNoir, ECIR 2018 — https://webis.de/downloads/publications/papers/bevendorff_2018.pdf
- Stract — https://github.com/StractOrg/stract（2026-04-02 封存）
- Marginalia docs — https://docs.marginalia.nu/4_data/3_crawl_data/
- Common Crawl 2025-09 — https://commoncrawl.org/blog/september-2025-crawl-archive-now-available
- Common Crawl host graph 2025 Q4 — https://commoncrawl.org/blog/host--and-domain-level-web-graphs-october-november-december-2025
- DCLM, arXiv 2406.11794（2024-06）；WCXB, arXiv 2605.21097（2026-05-20）
- Manku, Jain, Das Sarma, WWW 2007 — https://research.google/pubs/pub33026/
- Elastic shard sizing — https://www.elastic.co/docs/deploy-manage/production-guidance/optimize-performance/size-shards
- OpenSearch index alias — https://docs.opensearch.org/latest/im-plugin/index-alias/
- Anserini MS MARCO v2 — https://github.com/castorini/anserini/blob/master/docs/experiments-msmarco-v2.md
- ClueWeb22 Lucene, arXiv 2402.04357（2024-02-06）
- Peng & Dabek, Percolator, OSDI 2010 — https://www.usenix.org/legacy/event/osdi10/tech/full_papers/Peng.pdf
- Google Caffeine（2010-06-08）— https://developers.google.com/search/blog/2010/06/our-new-search-index-caffeine
- Risvik et al., Maguro, WSDM 2013 — https://www.microsoft.com/en-us/research/wp-content/uploads/2016/12/Maguro.pdf
- Cho & Garcia-Molina, TOIT 2003 — http://oak.cs.ucla.edu/cho/papers/cho-freq.pdf
- Pandey & Olston, WWW 2005 — http://i.stanford.edu/~olston/publications/userCentricCrawling.pdf
- Fetterly, Craswell, Vinay, SIGIR 2009 — https://www.microsoft.com/en-us/research/publication/the-impact-of-crawl-policy-on-web-search-effectiveness/
- Dong et al., WSDM 2010 — http://www.wsdm-conference.org/2010/proceedings/docs/p11.pdf
- Lefortier et al., CIKM 2013 — arXiv 1307.6080
- Boldi & Vigna, arXiv 1308.2140；Grinsztajn et al., arXiv 2207.08815；Kiryo et al., arXiv 1703.00593
- 其他 DOI 見各節表格
