#!/usr/bin/env bash
# Round-5c batch on Host3: main5.lanes at once, a warm-up check on R0, then the analysis.
# Results: /data/r14725023/r3/main5c/{warmup_check.txt,analysis.md}. Rounds 5a, 5b: main5/, main5b/.
set -u
R=/data/r14725023/r3/main5c
SHM=/dev/shm/r14725023-r3/main5
S=$HOME/r3/scripts
Z=$HOME/r3/sizes
export PATH=$HOME/.local/bin:$PATH PYVER=3.12.14 CPU0=36 HELPER_PIN=58-65,67-69,71 PORT0=18080
PY="uv run --no-project --python 3.12.14 python"
"$S/lanes3.sh" "$HOME/r3/main5.lanes" "$R" "$SHM" &
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
if ! awk '$1 == "R0" {exit !($2 >= 31 && $2 <= 38 && $5 >= 95)}' "$R/warmup_check.txt"; then
  echo "ABORT: R0 is outside 31-38 req/s or below 95 % CPU" >> "$R/warmup_check.txt"
  pkill -INT -f "html_spider -a crawler_id=0 .*$R/"
  sleep 8
  pkill -f "$R/"
  wait
  rm -rf "$SHM"
  exit 1
fi
wait "$LANES"
{
  echo "# Round-5c batch, $(date -u +%FT%TZ)"
  echo; echo "lanes3 exit status: $(cat "$R/done")"
  echo; echo "## Warm-up check (minutes 5-14)"; echo; sed 's/^/    /' "$R/warmup_check.txt"
  echo; echo "## C1-C4, 59 min after submit + 60 s, against R0"; echo
  $PY "$S/analyze3.py" --ref "$R/R0" --minutes 59 "$R/R0" "$R/A1" "$R/A2" "$R/A3" "$R/A4" "$R/A5"
  echo; echo "## Big hosts (C3)"; echo; echo '```'
  for n in A1 A2 A3 A4 A5; do $PY "$S/diag4_bighost.py" "$R/$n" "$Z/w03_sizes.txt" 40; echo; done
  echo '```'
  echo; echo "## Interval at send time (C2)"; echo; echo '```'
  $PY "$S/diag5_pace.py" "$R/A1" 5
  $PY "$S/diag5_pace.py" "$R/A2" 5
  $PY "$S/diag5_pace.py" "$R/A3" 4.8
  $PY "$S/diag5_pace.py" "$R/A4" 4.8
  $PY "$S/diag5_pace.py" "$R/A5" 5
  echo '```'
} > "$R/analysis.md" 2>&1
rm -rf "$SHM"
echo finished > "$R/main5c.finished"
