#!/bin/bash
# The Host2 OpenSearch service of deploy/compose.compute.yaml, on the local Docker VM:
# a data directory owned by 1014:1015 with mode 2770 (as `mkdir -m 2770` by the isel
# user makes it), the container's opensearch user (uid 1000) in group 1015, the same
# environment, a 512 MB heap and port 19200. Checks that the node writes its data
# directory, merges with one force_merge thread, and refuses to auto-create isel-text-*.
set -u
name=isel-os-host2-smoke
vol=isel-os-host2-smoke
url=http://127.0.0.1:19200
docker volume create "$vol" >/dev/null
docker run --rm -v "$vol":/d alpine:3.20 sh -c 'chown 1014:1015 /d && chmod 2770 /d && ls -ldn /d'
docker run -d --name "$name" --group-add 1015 \
  -v "$vol":/usr/share/opensearch/data \
  -e discovery.type=single-node -e DISABLE_SECURITY_PLUGIN=true \
  -e DISABLE_INSTALL_DEMO_CONFIG=true -e bootstrap.memory_lock=true \
  -e "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m" \
  -e thread_pool.force_merge.size=1 -e "action.auto_create_index=-isel-text-*,+*" \
  --ulimit memlock=-1:-1 --ulimit nofile=65536:65536 \
  -p 127.0.0.1:19200:9200 isel-opensearch:3.9.0 >/dev/null
for _ in $(seq 1 60); do
  curl -sf "$url/_cluster/health?wait_for_status=yellow&timeout=2s" >/dev/null && break
  sleep 3
done
echo "health: $(curl -s "$url/_cluster/health?filter_path=status")"
echo "force_merge pool: $(curl -s "$url/_cat/thread_pool/force_merge?h=name,type,size")"
echo "memory lock: $(curl -s "$url/_nodes?filter_path=nodes.*.process.mlockall")"
echo "auto-create isel-text-x: $(curl -s -o /dev/null -w '%{http_code}' -XPOST \
  "$url/isel-text-20991231t000000000000z/_doc" -H 'Content-Type: application/json' -d '{"a":1}')"
echo "auto-create other-x: $(curl -s -o /dev/null -w '%{http_code}' -XPOST \
  "$url/other-x/_doc" -H 'Content-Type: application/json' -d '{"a":1}')"
echo "data directory:"
docker exec "$name" sh -c 'ls -ln /usr/share/opensearch/data | head -5; id'
docker rm -f "$name" >/dev/null
docker volume rm "$vol" >/dev/null
echo "removed $name and $vol"
