"""Index size and load speed of the crawl_result corpus under several settings.

Usage: c50k_build.py TEXT_ROOT WORK_DIR OUT.json
Builds each variant with isel.textindex.build under its own alias, records the build
manifest, and drops the index. `icu` is config/text_index/icu.json; it also runs on
about 1/4 and 1/2 of the files to see how bytes per doc change with size, and once
without waiting for the retention leases, to measure what the wait saves.
"""
import copy
import dataclasses
import json
import sys
from pathlib import Path

from isel.config import load_config
from isel.textindex import OpenSearch, build

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
root, work, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ICU = json.loads((V / "config/text_index/icu.json").read_text())


def spec(name: str) -> dict:
    s = copy.deepcopy(ICU)
    if name == "freqs":
        s["mappings"]["properties"]["body"]["index_options"] = "freqs"
    elif name == "zstd":
        s["settings"]["codec"] = "zstd"
    elif name == "source_body":
        del s["mappings"]["_source"]
    return s


files = sorted(root.glob("*.parquet"))
subsets = {}
for share, step in (("q1", 4), ("q2", 2)):
    d = work / "subsets" / share
    d.mkdir(parents=True, exist_ok=True)
    for f in files[::step]:
        link = d / f.name
        if not link.exists():
            link.symlink_to(f)
    subsets[share] = d

base = load_config([V / "config/isel.toml"])
client = OpenSearch(base.text_index.url, 600.0, 5, 2.0)
runs = [("icu", root, None), ("icu_q1", subsets["q1"], None), ("icu_q2", subsets["q2"], None),
        ("icu_nowait", root, 0.001), ("freqs", root, None), ("zstd", root, None),
        ("source_body", root, None)]
results = {}
for name, text_root, lease in runs:
    settings_file = work / "settings" / f"{name}.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(spec(name.split("_q")[0].replace("icu_nowait", "icu"))))
    t = dataclasses.replace(base.text_index, alias=f"c50k-{name.replace('_', '-')}",
                            settings_file=str(settings_file), keep_prev=False,
                            lease_wait_s=lease or base.text_index.lease_wait_s)
    cfg = dataclasses.replace(base, text_index=t,
                              run=dataclasses.replace(base.run, work_dir=str(work)))
    m = build(cfg, client, text_root)
    client.call("DELETE", f"/{m['index']}")
    results[name] = {k: m[k] for k in ("rows", "docs", "text_bytes", "store_bytes",
                                       "segment_bytes", "bytes_per_doc",
                                       "bytes_per_text_byte", "segments",
                                       "recovery_source_pruned", "load_s", "total_s",
                                       "docs_per_s_load")}
    print(json.dumps({name: results[name]}), flush=True)
out.write_text(json.dumps(results, indent=1))
