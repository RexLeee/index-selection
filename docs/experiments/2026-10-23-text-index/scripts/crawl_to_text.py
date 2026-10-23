"""Streams one crawl_result jsonl from stdin into a TEXT_SCHEMA Parquet file.

Usage: ssh ntu-sa2 'cat F' | crawl_to_text.py OUT.parquet MAX_BODY_CHARS
Prints the stream's sha256 and counts as JSON, so the caller can compare the hash with
the server's `sha256sum F`. crawl_result has no HTTP status: "ok" is a 2xx HTML page,
recorded as 200. `url` is the canonical URL, as the recrawl writes the manifest URL.
"""
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pyarrow as pa

from isel.extract import extract
from isel.io import write_parquet_atomic
from isel.recrawl import TEXT_SCHEMA
from isel.urlnorm import canonicalize, md5_number_lower

out, max_body = Path(sys.argv[1]), int(sys.argv[2])
digest = hashlib.sha256()
stats: Counter[str] = Counter()
rows = []
for raw in sys.stdin.buffer:
    digest.update(raw)
    stats["lines"] += 1
    rec = json.loads(raw)
    if rec.get("status") != "ok" or not rec.get("content"):
        stats["not_ok"] += 1
        continue
    payload = rec["content"].encode("utf-8")
    try:
        doc = extract(payload, "utf-8", max_body)
    except Exception:  # one bad page must not stop the stream
        stats["extract_error"] += 1
        continue
    url = canonicalize(rec["url"])
    rows.append({
        "doc_id": md5_number_lower(url), "batch_id": 0, "url": url, "final_url": url,
        "fetched_at": datetime.fromisoformat(rec["fetched_at"]), "http_status": 200,
        "lang": doc.lang, "title": doc.title, "description": doc.description,
        "body": doc.body, "text_len": len(doc.body),
        "payload_sha1": hashlib.sha1(payload).hexdigest()})
write_parquet_atomic(pa.Table.from_pylist(rows, schema=TEXT_SCHEMA), out)
print(json.dumps({"sha256": digest.hexdigest(), **stats, "rows": len(rows)}))
