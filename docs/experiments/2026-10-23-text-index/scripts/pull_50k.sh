#!/bin/bash
# Streams each file of selected.txt from Host2 (read-only: cat and sha256sum only) into
# TEXT_SCHEMA Parquet, one file at a time. A file whose local hash differs from the
# server's is dropped.
set -u
S=/private/tmp/claude-501/-Users-rex-ntu-------------------index-selection/984c8316-3083-4ffc-b6f3-51eba33aecab/scratchpad/textidx
V=/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2
OUT=$S/c50k/text
LOG=$S/c50k/log
mkdir -p "$OUT" "$LOG"
while read -r f; do
  tag=$(echo "$f" | awk -F/ '{print $5 "_" $6 "_" $8}' | sed 's/.jsonl$//')
  [ -f "$LOG/$tag.ok" ] && continue
  start=$(date +%s)
  ssh -n -C -o BatchMode=yes ntu-sa2 "cat $f" \
    | uv run --project "$V" python "$S/crawl_to_text.py" "$OUT/$tag.parquet" 200000 > "$LOG/$tag.json"
  server=$(ssh -n -o BatchMode=yes ntu-sa2 "sha256sum $f" | cut -d' ' -f1)
  local_hash=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['sha256'])" "$LOG/$tag.json" 2>/dev/null)
  if [ -n "$server" ] && [ "$server" = "$local_hash" ]; then
    touch "$LOG/$tag.ok"
    echo "$tag ok $(( $(date +%s) - start ))s $(cat "$LOG/$tag.json")"
  else
    /bin/rm -f "$OUT/$tag.parquet"
    echo "$tag HASH MISMATCH server=$server local=$local_hash"
  fi
done < "$S/selected.txt"
echo DONE
