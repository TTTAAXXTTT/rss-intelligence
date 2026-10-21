#!/usr/bin/env bash
set -euo pipefail
OPENCLAW="${OPENCLAW_BIN:-$(command -v openclaw)}"
ROOT="${RSS_INTELLIGENCE_ROOT:-/home/ubuntu/.openclaw/rss-intelligence}"
READY="$ROOT/v52-e2e-ready.json"
[ -s "$READY" ] || { echo 'STOP_V52_E2E_READY_MISSING'; exit 65; }
python3 - "$READY" <<'PYREADY'
import json,sys
from datetime import datetime
from zoneinfo import ZoneInfo
d=json.load(open(sys.argv[1],encoding='utf-8'))
today=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
if d.get('schemaVersion')!=2 or d.get('status')!='ok' or d.get('dateKey')!=today:
    raise SystemExit('STOP_V52_E2E_READY_STALE_OR_INVALID')
PYREADY
OLD_FETCH=freshrss-actualize.timer
OLD_IDS=(5b758d06-5857-5070-abd6-79ffb622e472 8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634 f57c34f5-4333-5156-9efc-d60ec0547010)
V51_TIMERS=(rss-v51-freshrss-fetch.timer rss-v51-morning.timer rss-v51-afternoon.timer rss-v51-review.timer)
NEW_TIMERS=(rss-v52-freshrss-fetch.timer rss-v52-morning.timer rss-v52-afternoon.timer rss-v52-review.timer)
SNAP="$ROOT/v52-cutover-snapshot-$(date +%Y%m%d-%H%M%S).json"
V51TMP="$(mktemp)"

"$OPENCLAW" cron list --all --json >/tmp/v52-crons-before.json
for t in "${V51_TIMERS[@]}"; do
  printf '%s\t%s\n' "$t" "$(systemctl --user is-enabled "$t" 2>/dev/null || true)" >>"$V51TMP"
done
python3 - "$SNAP" /tmp/v52-crons-before.json "$V51TMP" "$(systemctl is-enabled "$OLD_FETCH" 2>/dev/null || true)" <<'PY'
import json,sys,datetime
out,src,v51file,fetch=sys.argv[1:]
d=json.load(open(src,encoding='utf-8'))
ids={'5b758d06-5857-5070-abd6-79ffb622e472','8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634','f57c34f5-4333-5156-9efc-d60ec0547010'}
v51={}
for raw in open(v51file,encoding='utf-8'):
    name,state=(raw.rstrip('\n').split('\t',1)+[''])[:2]
    v51[name]=state
obj={
 'schemaVersion':2,
 'createdAt':datetime.datetime.now().astimezone().isoformat(),
 'oldFetchEnabledState':fetch,
 'oldCrons':{j['id']:bool(j.get('enabled')) for j in d.get('jobs',[]) if j.get('id') in ids},
 'v51TimerStates':v51,
}
if set(obj['oldCrons'])!=ids: raise SystemExit('STOP_OLD_CRON_SNAPSHOT_INCOMPLETE')
open(out,'w',encoding='utf-8').write(json.dumps(obj,indent=2)+'\n')
PY
chmod 600 "$SNAP"; rm -f /tmp/v52-crons-before.json "$V51TMP"

rollback(){
  echo 'CUTOVER_FAILED_ROLLBACK_START'
  systemctl --user disable --now "${NEW_TIMERS[@]}" >/dev/null 2>&1 || true
  fetch_state="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["oldFetchEnabledState"])' "$SNAP")"
  if [ "$fetch_state" = enabled ] || [ "$fetch_state" = enabled-runtime ]; then sudo systemctl enable --now "$OLD_FETCH" >/dev/null 2>&1 || true; else sudo systemctl disable --now "$OLD_FETCH" >/dev/null 2>&1 || true; fi
  for id in "${OLD_IDS[@]}"; do
    enabled="$(python3 -c 'import json,sys;print(str(json.load(open(sys.argv[1]))["oldCrons"][sys.argv[2]]).lower())' "$SNAP" "$id")"
    if [ "$enabled" = true ]; then "$OPENCLAW" cron enable "$id" >/dev/null 2>&1 || true; else "$OPENCLAW" cron disable "$id" >/dev/null 2>&1 || true; fi
  done
  for t in "${V51_TIMERS[@]}"; do
    state="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("v51TimerStates",{}).get(sys.argv[2],""))' "$SNAP" "$t")"
    if [ "$state" = enabled ] || [ "$state" = enabled-runtime ]; then systemctl --user enable --now "$t" >/dev/null 2>&1 || true; else systemctl --user disable --now "$t" >/dev/null 2>&1 || true; fi
  done
  echo "CUTOVER_ROLLED_BACK snapshot=$SNAP"
}
trap 'rc=$?; rollback; exit $rc' ERR

# No production scheduler may overlap the new V5.2 timers.
sudo systemctl disable --now "$OLD_FETCH"
for id in "${OLD_IDS[@]}"; do "$OPENCLAW" cron disable "$id" >/dev/null; done
systemctl --user disable --now "${V51_TIMERS[@]}" >/dev/null 2>&1 || true
systemctl --user enable --now "${NEW_TIMERS[@]}"

[ "$(systemctl is-enabled "$OLD_FETCH" 2>/dev/null || true)" = disabled ]
for t in "${V51_TIMERS[@]}"; do
  state="$(systemctl --user is-enabled "$t" 2>/dev/null || true)"
  [ "$state" != enabled ] && [ "$state" != enabled-runtime ]
done
for t in "${NEW_TIMERS[@]}"; do [ "$(systemctl --user is-enabled "$t")" = enabled ]; done
"$OPENCLAW" cron list --all --json >/tmp/v52-crons-after.json
python3 - /tmp/v52-crons-after.json <<'PY'
import json,sys
ids={'5b758d06-5857-5070-abd6-79ffb622e472','8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634','f57c34f5-4333-5156-9efc-d60ec0547010'}
d=json.load(open(sys.argv[1],encoding='utf-8')); x={j.get('id'):j for j in d.get('jobs',[])}
for i in ids: assert x[i].get('enabled') is False
PY
rm -f /tmp/v52-crons-after.json
trap - ERR
echo "V52_CUTOVER_OK snapshot=$SNAP"
