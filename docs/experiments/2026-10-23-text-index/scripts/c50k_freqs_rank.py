"""Checks that indexing the body without positions keeps the ranking (TEXT_INDEX.md, S).

Usage: c50k_freqs_rank.py TEXT_ROOT WORK_DIR OUT.json
Builds two indices next to the live `isel-text` alias from the same text: `freqs` (body
`index_options: freqs`) and `rebuild` (the live settings again). Every batch-20 golden
keyword goes to all three with the product query. Against the live index, each build
reports keywords with the same top-10 ids in order, the same top-10 set, and the same
top-10 scores. `rebuild` shows what a second build alone changes. `tie_at_cutoff` counts
keywords whose 10th and 11th live hits have equal scores, so the top-10 set depends on
how ties are broken.
"""
import dataclasses
import json
import sys
from pathlib import Path

import pyarrow.compute as pc
from isel.textindex import OpenSearch, build, query_body

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


reference = {kw: hits(live.alias, kw) for kw in keywords}
tie_at_cutoff = sum(len(h) > live.k and h[live.k - 1][1] == h[live.k][1]
                    for h in reference.values())
result = {"keywords": len(keywords), "tie_at_cutoff": tie_at_cutoff}
for name, index_options in (("freqs", "freqs"), ("rebuild", None)):
    spec = json.loads((V / live.settings_file).read_text())
    if index_options:
        spec["mappings"]["properties"]["body"]["index_options"] = index_options
    settings_file = work / "settings" / f"rank_{name}.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(spec))
    t = dataclasses.replace(live, alias=f"c50k-rank-{name}", keep_prev=False,
                            settings_file=str(settings_file))
    cfg = dataclasses.replace(base, text_index=t,
                              run=dataclasses.replace(base.run, work_dir=str(work)))
    manifest = build(cfg, client, root)
    same_ids = same_set = same_scores = 0
    try:
        for kw in keywords:
            a, b = reference[kw][:live.k], hits(t.alias, kw)[:live.k]
            same_ids += [i for i, _ in a] == [i for i, _ in b]
            same_set += {i for i, _ in a} == {i for i, _ in b}
            same_scores += [s for _, s in a] == [s for _, s in b]
    finally:
        client.call("DELETE", f"/{manifest['index']}")
    result[name] = {"same_top10_ids": same_ids, "same_top10_set": same_set,
                    "same_top10_scores": same_scores, "store_bytes": manifest["store_bytes"]}
out.write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
