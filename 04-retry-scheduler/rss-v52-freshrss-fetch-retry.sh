#!/usr/bin/env bash
set -euo pipefail
ROOT="${RSS_INTELLIGENCE_ROOT:-/home/ubuntu/.openclaw/rss-intelligence}"
DOCKER_BIN="${DOCKER_BIN:-/usr/bin/docker}"
STATE="$ROOT/fetch-state-v52"
LOGDIR="$ROOT/retry-logs"
mkdir -p "$STATE" "$LOGDIR"
chmod 700 "$STATE" "$LOGDIR" 2>/dev/null || true
LOG="$LOGDIR/v52-freshrss-fetch.log"
FETCH_LOCK="$ROOT/v52-freshrss-fetch.lock"
SOURCE_LOCK="$ROOT/v52-source-dataset.lock"

exec 9>"$FETCH_LOCK"
if ! flock -n 9; then
  echo "$(date -Is) FETCH_ALREADY_RUNNING_NOT_SUCCESS" | tee -a "$LOG"
  exit 75
fi

# Snapshot and actualize may never mutate/read the live dataset concurrently.
exec 8>"$SOURCE_LOCK"
if ! flock -w "${RSS_SOURCE_LOCK_WAIT_SECONDS:-1800}" 8; then
  echo "$(date -Is) FETCH_SOURCE_LOCK_TIMEOUT" | tee -a "$LOG"
  exit 75
fi

runid="fetch-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
attempts=0

prewarm_relay(){
  python3 - <<'PY'
import concurrent.futures
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

base = os.environ.get('RSS_SOURCE_RELAY_BASE', '').strip()
if not base:
    print('RELAY_PREWARM_SKIPPED_NOT_CONFIGURED')
    sys.exit(0)
base = base.rstrip('/') + '/'
names = (
    'huggingface', 'occ', 'oecd', 'rbi', 'hackernews-show',
    'github-freshrss', 'github-a2a', 'github-mcp', 'github-windmill',
    'github-openclaw', 'github-dify', 'github-n8n', 'github-immich',
    'github-portainer', 'github-traefik', 'github-caddy',
)

def fetch(name):
    request = urllib.request.Request(
        base + name + '.xml',
        headers={'User-Agent': 'rss-freshrss-prewarm/1.0', 'Accept': 'application/xml'},
    )
    with urllib.request.urlopen(request, timeout=75) as response:
        body = response.read(6 * 1024 * 1024 + 1)
        warning = str(response.headers.get('Warning') or '')
        status = response.status
    if status != 200 or not body or len(body) > 6 * 1024 * 1024 or '110' in warning:
        raise RuntimeError('relay response invalid or stale')
    root = ET.fromstring(body).tag.rsplit('}', 1)[-1].lower()
    if root not in {'rss', 'rdf', 'feed'}:
        raise RuntimeError('relay XML root invalid')
    return name, len(body)

errors = []
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(fetch, name): name for name in names}
    for future in concurrent.futures.as_completed(futures):
        try:
            future.result()
        except Exception as exc:
            errors.append((futures[future], type(exc).__name__))
if errors:
    for name, error_class in sorted(errors):
        print(f'RELAY_PREWARM_FAIL name={name} errorClass={error_class}')
    raise SystemExit(1)
print(f'RELAY_PREWARM_OK feeds={len(names)}')
PY
}

database_error_count(){
  "$DOCKER_BIN" exec --user www-data freshrss php -r '
$database = new PDO("sqlite:/var/www/FreshRSS/data/users/rssowner/db.sqlite");
$database->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
echo (int)$database->query("SELECT COUNT(*) FROM feed WHERE error != 0")->fetchColumn();
'
}

write_state(){
  python3 - "$1" "$runid" "$started" "$attempts" "$ROOT" <<'PY'
import json,os,sys
from datetime import datetime,timezone
status,runid,started,attempts,base=sys.argv[1:]
root=os.path.join(base,'fetch-state-v52'); os.makedirs(root,exist_ok=True)
d={'schemaVersion':2,'runId':runid,'status':status,'startedAt':started,'finishedAt':None if status=='running' else datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'attempts':int(attempts)}
for name in ['last-run.json']+(['last-success.json'] if status=='ok' else []):
    path=os.path.join(root,name); tmp=path+'.tmp'
    open(tmp,'w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
    os.chmod(tmp,0o600); os.replace(tmp,path); os.chmod(path,0o600)
PY
}

write_state running
for attempt in 1 2 3; do
  attempts=$attempt
  echo "$(date -Is) FETCH ATTEMPT $attempt/3 runId=$runid" | tee -a "$LOG"
  tmp="$(mktemp)"
  set +e
  prewarm_relay >"$tmp" 2>&1
  prewarm_rc=$?
  if [ "$prewarm_rc" -eq 0 ]; then
    timeout --signal=TERM --kill-after=15s 600s "$DOCKER_BIN" exec --user www-data freshrss php /var/www/FreshRSS/app/actualize_script.php >>"$tmp" 2>&1
    rc=$?
  else
    rc=$prewarm_rc
  fi
  set -e
  cat "$tmp" >>"$LOG"
  db_errors='invalid'
  if [ "$rc" -eq 0 ]; then
    set +e
    db_errors="$(database_error_count 2>>"$tmp")"
    db_rc=$?
    set -e
    if [ "$db_rc" -ne 0 ] || ! [[ "$db_errors" =~ ^[0-9]+$ ]]; then db_errors='invalid'; fi
  fi
  if [ "$rc" -eq 0 ] && [ "$db_errors" = '0' ] \
    && ! grep -Eqi 'PHP Fatal error|Uncaught (Exception|Error)|Fatal error:' "$tmp"; then
    rm -f "$tmp"
    write_state ok
    echo "$(date -Is) FETCH SUCCESS attempt=$attempt runId=$runid" | tee -a "$LOG"
    exit 0
  fi
  rm -f "$tmp"
  echo "$(date -Is) FETCH FAIL attempt=$attempt rc=$rc databaseErrors=$db_errors" | tee -a "$LOG"
  if [ "$attempt" -lt 3 ]; then
    echo "$(date -Is) FETCH RETRY_WAIT 180s" | tee -a "$LOG"
    sleep 180
  fi
done
write_state failed
echo "$(date -Is) FETCH CANCEL after 3 failed attempts" | tee -a "$LOG"
exit 1
