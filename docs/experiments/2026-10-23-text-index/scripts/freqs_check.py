"""Checks that body `index_options: freqs` keeps the product ranking on golden batch 20.

Usage: freqs_check.py TEXT_ROOT WORK_DIR OUT.json
The live `isel-text` alias holds the golden pages plus the 44k crawl pages, built with
config/text_index/icu.json. Two indices are built from the same text: `freqs` (body
without positions) and `rebuild` (the live settings again, to show what a second build
alone changes). Every batch-20 keyword runs on all three with the product query
(config/isel.toml). Against the live index, each build reports keywords with the same
top-10 ids in order, the same top-10 set and the same top-10 scores, and its size.
"""
import dataclasses
import json
import sys
from pathlib import Path

import pyarrow.compute as pc
from isel.textindex import OpenSearch, _aliased, build, query_body

from isel.config import load_config
from isel.golden import read_golden

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
root, work, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
base = load_config([V / "config/isel.toml"])
live = base.text_index
client = OpenSearch(live.url, 600.0, 5, 2.0)
golden = read_golden(V / "work").filter(pc.field("batch_id") == 20)
keywords = sorted(set(golden.column("keyword").to_pylist()))


def hits(alias: str, keyword: str) -> list[tuple[str, float]]:
    body = {**query_body(live, keyword), "size": live.k + 1}
    reply = client.call("POST", f"/{alias}/_search", body)
    return [(h["_id"], h["sort"][0]) for h in reply["hits"]["hits"]]  # sort[0] is the score


live_index = _aliased(client, live.alias)[0]
live_manifest = json.loads((V / "work/text_index/build" / f"{live_index}.json").read_text())
reference = {kw: hits(live.alias, kw) for kw in keywords}
tie_at_cutoff = sum(len(h) > live.k and h[live.k - 1][1] == h[live.k][1]
                    for h in reference.values())
result = {"keywords": len(keywords), "tie_at_cutoff": tie_at_cutoff,
          "query_type": live.query_type, "fields": live.fields,
          "live": {"index": live_index, "docs": live_manifest["docs"],
                   "segment_bytes": live_manifest["segment_bytes"],
                   "bytes_per_doc": live_manifest["bytes_per_doc"]}}
for name, index_options in (("freqs", "freqs"), ("rebuild", None)):
    spec = json.loads((V / live.settings_file).read_text())
    if index_options:
        spec["mappings"]["properties"]["body"]["index_options"] = index_options
    settings_file = work / "settings" / f"check_{name}.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(spec, ensure_ascii=False))
    t = dataclasses.replace(live, alias=f"chk-{name}", keep_prev=False,
                            settings_file=str(settings_file))
    cfg = dataclasses.replace(base, text_index=t,
                              run=dataclasses.replace(base.run, work_dir=str(work)))
    manifest = build(cfg, client, root)
    same_ids = same_set = same_scores = 0
    differ = []
    try:
        for kw in keywords:
            a, b = reference[kw][:live.k], hits(t.alias, kw)[:live.k]
            same_ids += [i for i, _ in a] == [i for i, _ in b]
            same_set += {i for i, _ in a} == {i for i, _ in b}
            same_scores += [s for _, s in a] == [s for _, s in b]
            if a != b and len(differ) < 20:
                differ.append(kw)
    finally:
        client.call("DELETE", f"/{manifest['index']}")
    result[name] = {"same_top10_ids": same_ids, "same_top10_set": same_set,
                    "same_top10_scores": same_scores, "differ_examples": differ,
                    "docs": manifest["docs"], "segment_bytes": manifest["segment_bytes"],
                    "bytes_per_doc": manifest["bytes_per_doc"],
                    "recovery_source_pruned": manifest["recovery_source_pruned"]}
out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
print(json.dumps(result, indent=1, ensure_ascii=False))
