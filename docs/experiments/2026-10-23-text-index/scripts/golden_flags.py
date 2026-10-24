"""Batch 20's official coverage flags, read once from metricdb (read-only).

Usage: golden_flags.py OUT.parquet
One row per metric_url row of batch 20: query, keyword, tags, raw and canonical URL,
the doc id of the canonical URL, and the flags CrawlerAllMetricMeasure wrote when it
measured the batch (`is_crawled`, `is_indexed`). Needs ISEL_METRICDB_DSN (.env).
"""
import sys
from pathlib import Path

import pyarrow as pa
from isel.config import load_config
from isel.io import write_parquet_atomic
from isel.pg import open_metricdb
from isel.urlnorm import canonicalize, md5_number_lower

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
SQL = ("SELECT q.id, q.keyword, q.tags, u.url, u.is_crawled, u.is_indexed "
       "FROM metric_url u JOIN metric_queries q ON q.id = u.query_id "
       "WHERE q.batch_id = 20 AND u.url IS NOT NULL")

session = open_metricdb(load_config([V / "config/isel.toml"]))
try:
    rows = session.fetchall(SQL)
finally:
    session.close()
table = pa.Table.from_pylist([{
    "query_id": int(q), "keyword": kw, "tags": list(tags or []), "raw_url": url,
    "canonical_url": canonicalize(url), "doc_id": str(md5_number_lower(canonicalize(url))),
    "is_crawled": bool(crawled), "is_indexed": bool(indexed)}
    for q, kw, tags, url, crawled, indexed in rows])
write_parquet_atomic(table, Path(sys.argv[1]))
print(f"{len(rows)} rows")
