#!/usr/bin/env bash
set -euo pipefail
OPENCLAW="${OPENCLAW_BIN:-$(command -v openclaw)}"
STATE="${RSS_INTELLIGENCE_ROOT:-/home/ubuntu/.openclaw/rss-intelligence}"
JOBFILE="$STATE/v52-finalizer-jobs.env"
mkdir -p "$STATE"; chmod 700 "$STATE" 2>/dev/null || true
PRO=opencode-go/deepseek-v4-pro
FLASH=opencode-go/deepseek-v4-flash

find_id(){
  local key="$1" tmp
  tmp="$(mktemp)"; "$OPENCLAW" cron list --all --json >"$tmp"
  python3 - "$tmp" "$key" <<'PY'
import json,sys
p,key=sys.argv[1:]; d=json.load(open(p,encoding='utf-8'))
ids=[j.get('id') for j in d.get('jobs',[]) if j.get('declarationKey')==key and j.get('id')]
if len(ids)>1: print('STOP_DUPLICATE_DECLARATION_KEY='+key,file=sys.stderr); raise SystemExit(2)
if ids: print(ids[0])
PY
  rm -f "$tmp"
}
create_one(){
  local key="$1" name="$2" cron="$3" timeout="$4" tools="$5" message="$6" id raw
  id="$(find_id "$key")"
  if [ -n "$id" ]; then
    echo "REUSE_V52_JOB key=$key id=$id" >&2
    "$OPENCLAW" cron edit "$id" --model "$PRO" --fallbacks "$FLASH" --tools "$tools" --message "$message" >/dev/null
    "$OPENCLAW" cron disable "$id" >/dev/null
    printf '%s\n' "$id"; return 0
  fi
  raw="$(mktemp)"
  "$OPENCLAW" cron create --name "$name" --declaration-key "$key" --agent main \
    --cron "$cron" --tz Asia/Shanghai --exact --session isolated \
    --model "$PRO" --fallbacks "$FLASH" --timeout-seconds "$timeout" \
    --tools "$tools" --no-deliver --disabled --message "$message" --json >"$raw"
  id="$(python3 - "$raw" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8')); j=d.get('job') or {}; i=j.get('id')
if not i: raise SystemExit('STOP_CREATED_JOB_ID_MISSING')
print(i)
PY
)"; rm -f "$raw"; echo "CREATED_V52_JOB key=$key id=$id" >&2; printf '%s\n' "$id"
}
MORNING="$(create_one 'rss-v52-morning-finalizer' 'RSS V5.2 Morning Finalizer' '0 8 * * *' 1500 'read,web_search,web_fetch,rss_daily_finalize_save_v52' '读取 /agent/PROMPT_MORNING_V52.md，并严格执行。只能读取宿主准备的完整候选文件，进行必要Web核验并调用 rss_daily_finalize_save_v52 保存私有最终Stage；不得直接发布公网RSS或修改FreshRSS。')"
AFTERNOON="$(create_one 'rss-v52-afternoon-finalizer' 'RSS V5.2 Afternoon Finalizer' '0 14 * * *' 1500 'read,web_search,web_fetch,rss_daily_finalize_save_v52' '读取 /agent/PROMPT_AFTERNOON_V52.md，并严格执行。必须使用宿主生成的不可变Morning baseline与完整Afternoon候选，调用 rss_daily_finalize_save_v52；不得直接发布公网RSS或修改FreshRSS。')"
REVIEW="$(create_one 'rss-v52-source-review' 'RSS V5.2 Source Review' '0 3 15,28 * *' 2400 'read,freshrss_list_subscriptions,web_search,web_fetch,rss_review_save' '读取 /agent/PROMPT_REVIEW_V52.md，并严格执行 Semi-Monthly RSS Source Review V5.2。只能生成并保存 Pending Review，绝对不得修改 FreshRSS。')"
cat >"$JOBFILE.tmp" <<EOFJ
MORNING_JOB=$MORNING
AFTERNOON_JOB=$AFTERNOON
REVIEW_JOB=$REVIEW
EOFJ
chmod 600 "$JOBFILE.tmp"; mv "$JOBFILE.tmp" "$JOBFILE"; chmod 600 "$JOBFILE"
"$OPENCLAW" cron list --all --json >"$STATE/v52-finalizer-jobs.json.tmp"
python3 - "$STATE/v52-finalizer-jobs.json.tmp" "$MORNING" "$AFTERNOON" "$REVIEW" <<'PY'
import json,sys
p,mid,aid,rid=sys.argv[1:]; d=json.load(open(p,encoding='utf-8')); jobs={j.get('id'):j for j in d.get('jobs',[])}
expect={
 mid:('rss-v52-morning-finalizer',{'read','web_search','web_fetch','rss_daily_finalize_save_v52'}),
 aid:('rss-v52-afternoon-finalizer',{'read','web_search','web_fetch','rss_daily_finalize_save_v52'}),
 rid:('rss-v52-source-review',{'read','freshrss_list_subscriptions','web_search','web_fetch','rss_review_save'}),
}
for jid,(key,tools) in expect.items():
 j=jobs.get(jid); assert j, f'missing job {jid}'; assert j.get('declarationKey')==key; assert j.get('enabled') is False
 payload=j.get('payload') or {}; assert payload.get('model')=='opencode-go/deepseek-v4-pro'; assert payload.get('fallbacks')==['opencode-go/deepseek-v4-flash']; assert set(payload.get('toolsAllow') or [])==tools
 print(f'VERIFIED_V52_JOB key={key} id={jid} disabled=true tools={",".join(sorted(tools))}')
PY
mv "$STATE/v52-finalizer-jobs.json.tmp" "$STATE/v52-finalizer-jobs.json"; chmod 600 "$STATE/v52-finalizer-jobs.json"
echo "V52_JOB_TEMPLATES_OK file=$JOBFILE"
