#!/usr/bin/env bash
# Round-3 main batch on Host3: all lanes at once, a warm-up check on F0a, then analyze3.
# Results: /data/r14725023/r3/main/{warmup_check.txt,analysis.md}.
set -u
R=/data/r14725023/r3/main
SHM=/dev/shm/r14725023-r3/main
S=$HOME/r3/scripts
export PATH=$HOME/.local/bin:$PATH PYVER=3.12.14 CPU0=37 HELPER_PIN=58-65,67-71 PORT0=18080
PY="uv run --no-project --python 3.12.14 python"
"$S/lanes3.sh" "$HOME/r3/main3.lanes" "$R" "$SHM" &
LANES=$!
# Normal-only rate and crawler CPU over minutes 5-14 of each lane's warm-up.
sleep 845
for d in "$R"/*/; do
  n=$(basename "$d"); t0=$(cat "$d/t0"); a=$((t0 + 300)); b=$((t0 + 840))
  rate=$(awk -v a=$a -v b=$b '$3 == "n" && $1 >= a && $1 < b {n++} END {printf "%.1f", n / (b - a)}' "$d/access.log")
  cpu=$(awk -v a=$a -v b=$b 'NF > 3 && $1 >= a && $1 < b {if (!f) {f = $1; c = $4} l = $1; e = $4}
        END {printf "%.0f", (l > f) ? 100 * (e - c) / (l - f) : -1}' "$d/cpu.log")
  echo "$n $rate req/s, cpu $cpu %"
done > "$R/warmup_check.txt"
if ! awk '$1 == "F0a" {exit !($2 >= 31 && $2 <= 37 && $5 >= 95)}' "$R/warmup_check.txt"; then
  echo "ABORT: F0a is outside 31-37 req/s or below 95 % CPU" >> "$R/warmup_check.txt"
  pkill -INT -f "html_spider -a crawler_id=0 .*$R"
  sleep 8
  pkill -f "$R"
  wait
  rm -rf "$SHM"
  exit 1
fi
wait "$LANES"
{
  echo "# Round-3 main batch, $(date -u +%FT%TZ)"
  echo; echo "lanes3 exit status: $(cat "$R/done")"
  echo; echo "## Warm-up check (minutes 5-14)"; echo; sed 's/^/    /' "$R/warmup_check.txt"
  echo; echo "## C1-C4, first 59 min after submit + 60 s, against F0a"; echo
  $PY "$S/analyze3.py" --ref "$R/F0a" --minutes 59 "$R/F0a" "$R/F0b" "$R/F1" "$R/F2" "$R/F3" "$R/F4"
  echo; echo "## F5 against F5_0, 59 min"; echo
  $PY "$S/analyze3.py" --ref "$R/F5_0" --minutes 59 "$R/F5_0" "$R/F5"
  echo; echo "## F6 against F6_0, 59 min"; echo
  $PY "$S/analyze3.py" --ref "$R/F6_0" --minutes 59 "$R/F6_0" "$R/F6"
  echo; echo "## F2, F3 full 104 min (F7)"; echo
  $PY "$S/analyze3.py" --ref "$R/F0a" --minutes 104 "$R/F2" "$R/F3"
  echo; echo "## Drift: minutes 0-52"; echo
  $PY "$S/analyze3.py" --ref "$R/F0a" --minutes 52 "$R/F0a" "$R/F2" "$R/F3"
  echo; echo "## Drift: minutes 52-104"; echo
  $PY "$S/analyze3.py" --ref "$R/F0a" --from 52 --minutes 52 "$R/F0a" "$R/F2" "$R/F3"
} > "$R/analysis.md" 2>&1
rm -rf "$SHM"
echo finished > "$R/main3.finished"
