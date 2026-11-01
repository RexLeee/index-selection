#!/usr/bin/env bash
# Round 3: one crawler process with Host2's settings as of 2026-10-30 (AutoThrottle,
# Scrapy 2.19.0), normal feed with hot hosts, then the isel planner emulator.
# Usage: run3.sh <run dir> <policy> <warm s> <after s>
# Env: WC (WebCrawler tree), SHARE (20), HEADROOM (1), CONC (256), SCRAPY (2.19.0),
#      PYVER (3.12), TARGET (AutoThrottle target, 2.0), MEDIAN_S (2.0), SIGMA (0.44), NORMAL_HOSTS (3000), FILES (180), HOT (20),
#      HOT_PERIOD (400), SIZES (isel host sizes; empty = no recrawl), PORT (18080),
#      RESULT_ROOT (crawl_result parent; default the run dir), PIN / HELPER_PIN (taskset CPU
#      lists for the crawler / the other processes; unset = no pinning). MOCK_* and the
#      iselsim.py variables pass through.
set -euo pipefail
SIM=$(cd "$(dirname "$0")" && pwd)
WC=$(cd "${WC:?set WC to a WebCrawler tree}" && pwd)
RUN=$1 POLICY=$2 WARM=$3 AFTER=$4
PORT=${PORT:-18080}
RESULT_ROOT=${RESULT_ROOT:-$RUN}
rm -rf "$RUN" "$RESULT_ROOT/crawl_result"
mkdir -p "$RUN/queue/crawler_00" "$RUN/mb/pending" "$RUN/mb/results" "$RESULT_ROOT"
PY="uv run --no-project --python ${PYVER:-3.12}"
CRAWLER_DEPS="--with scrapy==${SCRAPY:-2.19.0} --with twisted==26.4.0 --with tldextract --with pqdict==1.5.1 --with PyYAML==6.0.2 --with python-json-logger==2.0.7"
CPIN=() HPIN=()
if [ -n "${PIN:-}" ]; then CPIN=(taskset -c "$PIN"); fi
if [ -n "${HELPER_PIN:-}" ]; then HPIN=(taskset -c "$HELPER_PIN"); fi
PIDS=()
stop() {
  pkill -INT -f "html_spider -a crawler_id=0 .*$RUN/queue/" || true
  sleep 8
  pkill -KILL -f "html_spider -a crawler_id=0 .*$RUN/queue/" || true
  kill "${PIDS[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}

MOCK_DEPART_LOG="$RUN/depart.log" ${HPIN[@]+"${HPIN[@]}"} $PY --with aiohttp python "$SIM/mockweb3.py" "$PORT" "$RUN/access.log" \
  "${MEDIAN_S:-2.0}" "${SIGMA:-0.44}" > "$RUN/mockweb.out" 2>&1 &
PIDS+=($!)
${HPIN[@]+"${HPIN[@]}"} $PY python "$SIM/feeder3.py" "$RUN/queue/crawler_00" "$PORT" "${NORMAL_HOSTS:-3000}" \
  "${FILES:-180}" "${HOT:-20}" "${HOT_PERIOD:-400}" > "$RUN/feeder.out" 2>&1 &
PIDS+=($!)
sleep 3
if ! curl -s -o /dev/null -H "Host: check.sim" "http://127.0.0.1:$PORT/robots.txt"; then
  echo "mock web is not up; see $RUN/mockweb.out" >&2
  stop
  exit 1
fi
(
  cd "$WC/containers/crawler"
  export PYTHONPATH="$WC:$SIM" MANUAL_BULK_CRAWL_SCHEDULING_POLICY=$POLICY \
    MANUAL_BULK_CRAWL_SHARE_PERCENT=${SHARE:-20} \
    CRAWLER_MANUAL_BULK_CRAWL_HOST_HEADROOM=${HEADROOM:-1} \
    CRAWLER_MANUAL_BULK_CRAWL_QUEUE_DIR="$RUN/mb/pending" \
    CRAWLER_USE_AUTOTHROTTLE=true CRAWLER_CONCURRENT_REQUESTS=${CONC:-256} \
    CRAWLER_CONCURRENT_REQUESTS_PER_DOMAIN=16 CRAWLER_DOWNLOAD_DELAY=0.25 \
    CRAWLER_AUTOTHROTTLE_START_DELAY=0.5 CRAWLER_AUTOTHROTTLE_MAX_DELAY=10.0 \
    CRAWLER_AUTOTHROTTLE_TARGET_CONCURRENCY=${TARGET:-2.0} CRAWLER_AUTOTHROTTLE_DEBUG=false
  exec ${CPIN[@]+"${CPIN[@]}"} $PY $CRAWLER_DEPS scrapy crawl html_spider -a crawler_id=0 --loglevel WARNING \
    -s "URL_QUEUE_TEMPLATE=$RUN/queue/crawler_{id:02d}" \
    -s "RESULT_DIR_TEMPLATE=$RESULT_ROOT/crawl_result/crawler_{id:02d}" \
    -s DNS_RESOLVER=simdns.LoopbackResolver -s TELNETCONSOLE_ENABLED=False
) > "$RUN/crawler.log" 2>&1 &
PIDS+=($!)
T0=$(date +%s)
echo "$T0" > "$RUN/t0"
for _ in $(seq 60); do grep -q '"crawler.config"' "$RUN/crawler.log" 2>/dev/null && break; sleep 1; done
if ! grep -q '"throttle_mode": "autothrottle"' "$RUN/crawler.log"; then
  echo "crawler is not in AutoThrottle mode; see $RUN/crawler.log" >&2
  stop
  exit 1
fi
cpu() { pgrep -f "html_spider -a crawler_id=0 .*$RUN/queue/" | tail -1; }
TICK=0
sample() {
  # Linux ps %cpu is a lifetime average, so also log cumulative CPU seconds (`times`, Linux only).
  local pid
  pid=$(cpu)
  echo "$(date +%s) $(ps -o %cpu=,rss=,times= -p "$pid" 2>/dev/null || ps -o %cpu=,rss= -p "$pid" 2>/dev/null \
    || echo NA NA)" >> "$RUN/cpu.log"
  TICK=$(( TICK + 1 ))
  # Pages are written at full size; keep only the last minute or two on disk.
  if [ $(( TICK % 6 )) -eq 0 ]; then
    find "$RESULT_ROOT/crawl_result" -name '*.jsonl' -mmin +1 -delete 2>/dev/null || true
    find "$RUN/mb/results" -name '*.html' -mmin +1 -delete 2>/dev/null || true
  fi
}
while [ $(( $(date +%s) - T0 )) -lt "$WARM" ]; do sample; sleep 5; done
date +%s > "$RUN/t_submit"
if [ -n "${SIZES:-}" ]; then
  ${HPIN[@]+"${HPIN[@]}"} $PY python "$SIM/iselsim.py" "$WC" "$RUN/mb/pending" "$RUN/mb/results" "$PORT" \
    "$SIZES" "$AFTER" > "$RUN/jobs.txt" 2> "$RUN/iselsim.err" &
  PIDS+=($!)
fi
while [ $(( $(date +%s) - T0 )) -lt $(( WARM + AFTER )) ]; do sample; sleep 5; done
date +%s > "$RUN/t_end"
stop
rm -rf "$RESULT_ROOT/crawl_result"
find "$RUN/mb/results" -name '*.html' -delete
echo done
