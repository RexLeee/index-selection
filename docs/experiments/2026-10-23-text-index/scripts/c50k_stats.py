"""Profile of the 50k-page corpus as the index sees it: text bytes per doc, fields, languages.

Usage: c50k_stats.py TEXT_ROOT OUT.json
Reads the newest row of each doc through isel.textindex.latest_docs, so the profile
covers exactly the documents a build indexes.
"""
import json
import sys
from pathlib import Path

import duckdb
from isel.textindex import latest_docs

root, out = Path(sys.argv[1]), Path(sys.argv[2])
con = duckdb.connect()
rows, reader = latest_docs(con, root, 10_000)
docs = reader.read_all()
con.register("docs", docs)
cols = ["docs", "mean", "p50", "p90", "p99", "max", "with_title", "with_description",
        "body_cut"]
size = con.execute("""
    SELECT count(*), avg(t), quantile_cont(t, 0.5), quantile_cont(t, 0.9),
           quantile_cont(t, 0.99), max(t), avg((title IS NOT NULL)::int),
           avg((description IS NOT NULL)::int), avg((length(body) >= 200000)::int)
    FROM (SELECT *, strlen(coalesce(title, '')) + strlen(coalesce(description, ''))
                    + strlen(body) AS t FROM docs)""").fetchone()
langs = con.execute("""SELECT coalesce(lang, 'unknown'), count(*) FROM docs
                       GROUP BY 1 ORDER BY 2 DESC LIMIT 15""").fetchall()
files = sorted(root.glob("*.parquet"))
input_rows = con.execute(
    f"SELECT count(*) FROM read_parquet('{root}/*.parquet')").fetchone()[0]
parquet_bytes = sum(f.stat().st_size for f in files)
result = {"text_bytes_per_doc": dict(zip(cols, size, strict=True)),
          "langs": dict(langs), "files": len(files), "input_rows": input_rows,
          "docs": rows, "parquet_bytes": parquet_bytes,
          "parquet_bytes_per_row": parquet_bytes / input_rows}
out.write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
