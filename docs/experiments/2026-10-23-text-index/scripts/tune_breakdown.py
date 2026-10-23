"""Per-pair hits of two golden_tune.py runs, by text length, site and tag, with examples.

Usage: tune_breakdown.py TUNE.json WORK FETCH_LOG BASELINE FINAL OUT.json

Re-searches every batch-20 keyword with both runs' settings on their kept indexes and
compares, for each present (keyword, golden URL) pair, whether it is in the top 10.
"""
import dataclasses
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq
from isel.textindex import OpenSearch, search

from isel.config import load_config
from isel.urlnorm import md5_number_lower

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
EXAMPLES = 15


def main() -> None:
    tune = json.loads(Path(sys.argv[1]).read_text())
    work, log_path = Path(sys.argv[2]), Path(sys.argv[3])
    runs = {"baseline": sys.argv[4], "final": sys.argv[5]}
    out_path = Path(sys.argv[6])
    g = pq.read_table(V / "work/golden/golden.parquet").filter(pc.field("batch_id") == 20)
    judged, keyword, tags = defaultdict(set), {}, {}
    for r in g.to_pylist():
        judged[r["query_id"]].add(str(md5_number_lower(r["canonical_url"])))
        keyword[r["query_id"]], tags[r["query_id"]] = r["keyword"], r["tags"] or []
    present = {str(i) for f in (work / "roots" / "bg44k").glob("*.parquet")
               for i in pq.read_table(f, columns=["doc_id"]).column("doc_id").to_pylist()}
    log = {}
    for line in log_path.open():
        rec = json.loads(line)
        log[rec["doc_id"]] = rec

    base = load_config([V / "config/isel.toml"])
    client = OpenSearch(base.text_index.url, 120.0, 3, 1.0)
    ranked = {}
    for name, run in runs.items():
        r = tune[run]
        t = dataclasses.replace(base.text_index, alias=f"tune-{run.split(':')[0]}",
                                query_type=r["query_type"], fields=r["fields"])
        texts = sorted(set(keyword.values()))
        ranked[name] = {text: search(client, t, text)[0] for text in texts}

    groups = defaultdict(lambda: defaultdict(Counter))
    changes = {"fixed": [], "broken": []}
    for q in sorted(judged):
        for d in judged[q] & present:
            hit = {n: d in ranked[n][keyword[q]] for n in runs}
            rec = log.get(d, {})
            n = rec.get("text_len") or 0
            length = "<200" if n < 200 else "200-2k" if n < 2000 else ">=2k"
            host = rec.get("host", "background")
            site = host if host in ("www.youtube.com", "en.wikipedia.org") else "other"
            for dim, key in (("text_len", length), ("site", site),
                             ("tag", "+".join(sorted(tags[q]))), ("all", "all")):
                for name in runs:
                    groups[dim][key][name] += hit[name]
                groups[dim][key]["pairs"] += 1
            if hit["baseline"] != hit["final"]:
                kind = "fixed" if hit["final"] else "broken"
                changes[kind].append({"keyword": keyword[q], "url": rec.get("url"),
                                      "text_len": n})
    report = {"runs": runs, "breakdown": {
        dim: {k: {"pairs": c["pairs"], **{name: round(c[name] / c["pairs"], 3) for name in runs}}
              for k, c in sorted(g.items(), key=lambda kv: -kv[1]["pairs"])}
        for dim, g in groups.items()},
        "fixed": len(changes["fixed"]), "broken": len(changes["broken"]),
        "examples": {k: v[:EXAMPLES] for k, v in changes.items()}}
    out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
