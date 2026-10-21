#!/usr/bin/env bash
set -euo pipefail

db='/var/lib/docker/volumes/freshrss_data/_data/users/rssowner/db.sqlite'
updater='/tmp/update-target-freshrss-relay.py'
expected='2502137122b901ed0c901cd7a9d728de485d0bd4955c7c48654d5fcdeafc9096'
lock='/home/ubuntu/.openclaw/rss-intelligence/v52-source-dataset.lock'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/tencent-relay-cutover-${stamp}"
container_stopped=0
database_changed=0

finish() {
  rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ] && [ "$database_changed" -eq 1 ] && [ -f "$backup/freshrss-rssowner-before.sqlite" ]; then
    echo 'cutover failed after database commit; restoring the verified SQLite backup' >&2
    if [ "$container_stopped" -eq 0 ]; then
      docker stop --time 30 freshrss >/dev/null || true
      container_stopped=1
    fi
    rm -f -- "${db}-wal" "${db}-shm"
    install -m 0664 -o www-data -g www-data "$backup/freshrss-rssowner-before.sqlite" "$db"
    restored="$(python3 - "$db" <<'PY'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(db.execute("PRAGMA integrity_check").fetchone()[0])
PY
)"
    if [ "$restored" != 'ok' ]; then
      echo 'restored database failed integrity check' >&2
    fi
  fi
  if [ "$container_stopped" -eq 1 ]; then
    docker start freshrss >/dev/null || true
  fi
  exit "$rc"
}
trap finish EXIT

if [ ! -f "$db" ]; then echo 'FreshRSS database is missing' >&2; exit 1; fi
if [ ! -f "$updater" ]; then echo 'relay updater is missing' >&2; exit 1; fi
actual="$(sha256sum "$updater" | awk '{print $1}')"
if [ "$actual" != "$expected" ]; then echo "relay updater checksum mismatch: $actual" >&2; exit 1; fi
python3 -m py_compile "$updater"

exec 9>"$lock"
if ! flock -w 1800 9; then echo 'timed out waiting for the RSS pipeline lock' >&2; exit 1; fi

install -d -m 0750 -o ubuntu -g ubuntu "$backup"
python3 - "$db" "$backup/freshrss-rssowner-before.sqlite" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
destination = sqlite3.connect(sys.argv[2])
source.backup(destination)
result = destination.execute("PRAGMA integrity_check").fetchone()[0]
destination.close()
source.close()
if result != "ok":
    raise SystemExit(f"backup integrity check failed: {result}")
print("backup-integrity=ok")
PY

python3 - "$db" >"$backup/feeds-before.json" <<'PY'
import json, sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
ids = (1, 3, 5, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21, 38, 41, 47)
placeholders = ",".join("?" for _ in ids)
rows = db.execute(f"SELECT id, name, url, lastUpdate, error FROM feed WHERE id IN ({placeholders}) ORDER BY id", ids).fetchall()
print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2, sort_keys=True))
PY

docker stop --time 30 freshrss >/dev/null
container_stopped=1
database_changed=1
python3 "$updater" | tee "$backup/update-result.json"
for sidecar in "$db" "${db}-wal" "${db}-shm"; do
  if [ -e "$sidecar" ]; then chown www-data:www-data "$sidecar"; fi
done
docker start freshrss >/dev/null
container_stopped=0
for _ in $(seq 1 60); do
  health="$(docker inspect freshrss --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
  if [ "$health" = 'healthy' ]; then break; fi
  sleep 1
done
if [ "$(docker inspect freshrss --format '{{.State.Health.Status}}')" != 'healthy' ]; then
  echo 'FreshRSS did not return to healthy state' >&2
  exit 1
fi

python3 - "$db" >"$backup/feeds-after.json" <<'PY'
import json, sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
ids = (1, 3, 5, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21, 38, 41, 47)
placeholders = ",".join("?" for _ in ids)
rows = db.execute(f"SELECT id, name, url, lastUpdate, error FROM feed WHERE id IN ({placeholders}) ORDER BY id", ids).fetchall()
expected = {
  1: "https://rss.example.com/.rss-source-bridge-v1/github-freshrss.xml",
  3: "https://rss.example.com/.rss-source-bridge-v1/github-a2a.xml",
  5: "https://rss.example.com/.rss-source-bridge-v1/github-mcp.xml",
  10: "https://rss.example.com/.rss-source-bridge-v1/github-windmill.xml",
  11: "https://rss.example.com/.rss-source-bridge-v1/huggingface.xml",
  12: "https://rss.example.com/.rss-source-bridge-v1/github-openclaw.xml",
  13: "https://rss.example.com/.rss-source-bridge-v1/github-dify.xml",
  14: "https://rss.example.com/.rss-source-bridge-v1/github-n8n.xml",
  15: "https://rss.example.com/.rss-source-bridge-v1/github-immich.xml",
  16: "https://rss.example.com/.rss-source-bridge-v1/github-portainer.xml",
  17: "https://rss.example.com/.rss-source-bridge-v1/github-traefik.xml",
  18: "https://rss.example.com/.rss-source-bridge-v1/github-caddy.xml",
  21: "https://rss.example.com/.rss-source-bridge-v1/oecd.xml",
  38: "https://rss.example.com/.rss-source-bridge-v1/hackernews-show.xml",
  41: "https://rss.example.com/.rss-source-bridge-v1/rbi.xml",
  47: "https://rss.example.com/.rss-source-bridge-v1/occ.xml",
}
if len(rows) != 16 or any(row["url"] != expected[row["id"]] for row in rows):
    raise SystemExit("post-cutover URL verification failed")
print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2, sort_keys=True))
PY

sha256sum "$backup/freshrss-rssowner-before.sqlite" "$updater" >"$backup/SHA256SUMS"
chown -R ubuntu:ubuntu "$backup"
database_changed=0

echo '=== FreshRSS relay cutover complete ==='
printf 'backup=%s\n' "$backup"
stat -c 'db-owner=%U:%G mode=%a bytes=%s' "$db"
trap - EXIT
