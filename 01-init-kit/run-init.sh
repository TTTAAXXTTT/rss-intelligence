#!/usr/bin/env bash
set -euo pipefail
PKG=/home/ubuntu/rss-intelligence-v5.2
KIT=/home/ubuntu/.openclaw/workspace/rss-init-kit-v52
ROOT=/home/ubuntu/.openclaw/workspace/rss-init-v52
REVIEWS=/home/ubuntu/.openclaw/rss-intelligence/reviews
OPENCLAW="$(command -v openclaw)"
EXTRACT="$PKG/00-preflight/extract-agent-text.py"
FLASH=opencode-go/deepseek-v4-flash
PRO=opencode-go/deepseek-v4-pro
MAX_ATTEMPTS=3; WAIT=180
mkdir -p "$ROOT"/{discovery,logs,final}
chmod 700 "$ROOT" "$ROOT/discovery" "$ROOT/logs" "$ROOT/final" 2>/dev/null || true
LOG="$ROOT/logs/runner.log"
log(){ printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }
json_ok(){ python3 - "$1" <<'PY'
import json,sys
json.load(open(sys.argv[1],encoding='utf-8')); print('JSON_OK')
PY
}
review_exists(){
  local id="$1"
  python3 - "$REVIEWS" "$id" <<'PY' >/dev/null 2>&1
import json,re,sys
from pathlib import Path
root=Path(sys.argv[1]); wanted=sys.argv[2]
pat=re.compile(r'(?m)^INIT_V52_BATCH_ID='+re.escape(wanted)+r'\s*$')
for f in root.glob('*.json'):
 try:
  d=json.load(open(f,encoding='utf-8'))
  if d.get('status')!='pending': continue
  s=str(d.get('summaryMarkdown',''))
 except Exception: continue
 if pat.search(s): raise SystemExit(0)
raise SystemExit(1)
PY
}

call_agent(){
  local model="$1" timeout_sec="$2" session="$3" msgfile="$4" outfile="$5" raw err rc
  raw="$(mktemp)"; err="$(mktemp)"
  set +e
  timeout --signal=TERM --kill-after=20s "$((timeout_sec+60))s" "$OPENCLAW" agent --agent main \
    --session-key "$session" --model "$model" --timeout "$timeout_sec" --json --message-file "$msgfile" >"$raw" 2>"$err"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    python3 "$EXTRACT" <"$raw" >"$outfile" 2>>"$err" || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then cat "$err" >>"$LOG"; fi
  rm -f "$raw" "$err"
  return "$rc"
}

baseline(){
  [ -s "$ROOT/baseline.json" ] && { log 'baseline ALREADY_DONE'; return 0; }
  local a msg out
  for a in 1 2 3; do
    msg="$(mktemp)"; out="$ROOT/baseline.attempt-${a}.tmp"
    cat >"$msg" <<'EOF'
必须且只能调用 freshrss_list_subscriptions 一次。不得调用任何写工具。输出严格 JSON：{"subscriptions":[{"title":"","feedUrl":"https://...","categories":[""]}]}。不得输出密码、Token、认证响应或 Markdown。
EOF
    log "baseline ATTEMPT $a/3"
    if call_agent "$FLASH" 120 "rss-v52:init:baseline:$a:$(date +%s%N)" "$msg" "$out" && python3 -c 'import json,sys; from urllib.parse import urlsplit; d=json.load(open(sys.argv[1],encoding="utf-8")); subs=d.get("subscriptions"); assert isinstance(subs,list); assert all((lambda u: urlsplit(u).scheme in ("http","https") and bool(urlsplit(u).hostname))(str(x.get("feedUrl","")).strip()) and isinstance(x.get("categories",[]),list) for x in subs)' "$out" >/dev/null 2>&1; then
      mv "$out" "$ROOT/baseline.json"; chmod 600 "$ROOT/baseline.json"; rm -f "$msg"; log 'baseline SUCCESS'; return 0
    fi
    rm -f "$msg" "$out"; [ "$a" -lt 3 ] && { log 'baseline RETRY_WAIT 180s'; sleep "$WAIT"; }
  done
  log 'baseline CANCEL after 3 failed attempts'; return 1
}

discover(){
  local id="$1" cat="$2" topic="$3" file="$ROOT/discovery/${id}.json" a msg out
  [ -s "$file" ] && { log "$id discover ALREADY_DONE"; return 0; }
  for a in 1 2 3; do
    msg="$(mktemp)"; out="${file}.attempt-${a}.tmp"
    cat >"$msg" <<EOF
读取 /agent/rss-init-kit-v52/INIT_DISCOVERY_RULES.md 并严格执行。
Batch: $id
目标分类: $cat
主题: $topic
必须读取 /agent/rss-init-v52/baseline.json；若 /agent/rss-init-v52/final/rss-init-master.csv 存在，也读取以避免重复。
必须主动进行全球多语言 web_search，最多2次；不得 web_fetch；不得保存 Review；不得修改 FreshRSS。
只输出严格 JSON：{"batchId":"$id","category":"$cat","candidates":[{"name":"","siteUrl":"https://...","possibleFeedUrl":"","sourceType":"official|regulator|research|independent|media|other","countryOrRegion":"","language":"","why":""}]}
最多2个 candidates。URL 必须纯原始 HTTP(S) URL，不得 Markdown。
EOF
    log "$id discover ATTEMPT $a/3"
    if call_agent "$FLASH" 180 "rss-v52:init:$id:discover:$a:$(date +%s%N)" "$msg" "$out" && python3 - "$out" "$id" "$cat" <<'PY' >/dev/null 2>&1
import json,sys
from urllib.parse import urlsplit
p,b,c=sys.argv[1:]; d=json.load(open(p,encoding='utf-8'))
assert d.get('batchId')==b and d.get('category')==c
x=d.get('candidates'); assert isinstance(x,list) and len(x)<=2
def raw_http(u):
 if not u:return True
 if any(ch.isspace() for ch in u) or any(ch in u for ch in '[]<>'):return False
 z=urlsplit(u)
 return z.scheme in ('http','https') and bool(z.hostname) and z.hostname!='...'
for a in x:
 for k in ('siteUrl','possibleFeedUrl'):
  assert raw_http(str(a.get(k,'')).strip())
PY
    then
      mv "$out" "$file"; chmod 600 "$file"; rm -f "$msg"; log "$id discover SUCCESS"; return 0
    fi
    rm -f "$msg" "$out"; [ "$a" -lt 3 ] && { log "$id discover RETRY_WAIT 180s"; sleep "$WAIT"; }
  done
  log "$id discover CANCEL after 3 failed attempts"; return 1
}

validate(){
  local id="$1" cat="$2" a msg out rc
  review_exists "$id" && { log "$id validate ALREADY_DONE"; return 0; }
  for a in 1 2 3; do
    review_exists "$id" && { log "$id validate LATE_SUCCESS before attempt=$a"; return 0; }
    msg="$(mktemp)"; out="$(mktemp)"
    cat >"$msg" <<EOF
读取 /agent/rss-init-kit-v52/INIT_VALIDATE_RULES.md 并严格执行。
Batch: $id
目标分类: $cat
候选文件: /agent/rss-init-v52/discovery/${id}.json
必须在 summaryMarkdown 原样写入：
INIT_V52_BATCH_ID=$id
INIT_V52_CATEGORY=$cat
reviewDate 使用当前 Asia/Shanghai 日期。最终必须真实调用 rss_review_save 一次。不得修改 FreshRSS。
保存成功后只输出严格 JSON：{"saved":true,"batchId":"$id"}
EOF
    log "$id validate ATTEMPT $a/3"
    set +e; call_agent "$PRO" 300 "rss-v52:init:$id:validate:$a:$(date +%s%N)" "$msg" "$out"; rc=$?; set -e
    rm -f "$msg" "$out"
    if review_exists "$id"; then log "$id validate SUCCESS cli_rc=$rc"; return 0; fi
    [ "$a" -lt 3 ] && { log "$id validate RETRY_WAIT 180s"; sleep "$WAIT"; }
  done
  log "$id validate CANCEL after 3 failed attempts"; return 1
}

run_batch(){ local id="$1" cat="$2" topic="$3"; discover "$id" "$cat" "$topic" && validate "$id" "$cat"; }
find_row(){ awk -F '\t' -v id="$1" 'NR>1 && $1==id{print;exit}' "$KIT/batches.tsv"; }

missing_ids(){
python3 - "$KIT/batches.tsv" "$REVIEWS" <<'PY'
import csv,json,re,sys
from pathlib import Path
b=list(csv.DictReader(open(sys.argv[1],encoding='utf-8'),delimiter='\t')); ids=[x['id'] for x in b]
val=set(); pat=re.compile(r'(?m)^INIT_V52_BATCH_ID=([^\s]+)\s*$')
for p in Path(sys.argv[2]).glob('*.json'):
 try:
  d=json.load(open(p,encoding='utf-8'))
  if d.get('status')!='pending': continue
  s=str(d.get('summaryMarkdown',''))
 except:continue
 m=pat.search(s)
 if m: val.add(m.group(1))
for i in ids:
 if i not in val: print(i)
PY
}

status(){
python3 - "$KIT/batches.tsv" "$ROOT/discovery" "$REVIEWS" <<'PY'
import csv,json,re,sys
from pathlib import Path
b=list(csv.DictReader(open(sys.argv[1],encoding='utf-8'),delimiter='\t')); ids={x['id'] for x in b}
disc={p.stem for p in Path(sys.argv[2]).glob('*.json')}
val=set(); pat=re.compile(r'(?m)^INIT_V52_BATCH_ID=([^\s]+)\s*$')
for p in Path(sys.argv[3]).glob('*.json'):
 try:
  d=json.load(open(p,encoding='utf-8'))
  if d.get('status')!='pending': continue
  s=str(d.get('summaryMarkdown',''))
 except:continue
 m=pat.search(s)
 if m: val.add(m.group(1))
print(f'TOTAL_BASE_BATCHES={len(ids)}'); print(f'DISCOVERY_DONE={len(ids&disc)}'); print(f'VALIDATION_DONE={len(ids&val)}')
missing=sorted(ids-val); print(f'BASE_BATCHES_REMAINING={len(missing)}')
if missing: print('REMAINING_IDS='+','.join(missing))
PY
}

gap_status(){
  [ -s "$ROOT/coverage-gaps.tsv" ] || { echo 'STOP_NO_COVERAGE_GAPS_FILE'; return 66; }
  python3 - "$ROOT/coverage-gaps.tsv" "$ROOT/discovery" "$REVIEWS" <<'PY'
import csv,json,re,sys
from pathlib import Path
b=list(csv.DictReader(open(sys.argv[1],encoding='utf-8'),delimiter='\t')); ids={x['id'] for x in b}
disc={p.stem for p in Path(sys.argv[2]).glob('*.json')}
val=set(); pat=re.compile(r'(?m)^INIT_V52_BATCH_ID=([^\s]+)\s*$')
for p in Path(sys.argv[3]).glob('*.json'):
 try:
  d=json.load(open(p,encoding='utf-8'))
  if d.get('status')!='pending': continue
  s=str(d.get('summaryMarkdown',''))
 except:continue
 m=pat.search(s)
 if m: val.add(m.group(1))
missing=sorted(ids-val)
print(f'TOTAL_GAP_BATCHES={len(ids)}'); print(f'GAP_DISCOVERY_DONE={len(ids&disc)}'); print(f'GAP_VALIDATION_DONE={len(ids&val)}'); print(f'GAP_BATCHES_REMAINING={len(missing)}')
if missing: print('GAP_REMAINING_IDS='+','.join(missing))
PY
}

cmd="${1:-status}"

run_next_base(){
  local n="${1:-5}"; [[ "$n" =~ ^[1-9][0-9]*$ ]] || { echo 'STOP_NEXT_COUNT_INVALID'; return 64; }
  [ "$n" -le 10 ] || { echo 'STOP_NEXT_COUNT_MAX_10'; return 64; }
  baseline
  local fail=0 count=0 id row bid cat topic
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    [ "$count" -lt "$n" ] || break
    row="$(find_row "$id")"; [ -n "$row" ] || { log "missing row for $id"; fail=1; continue; }
    IFS=$'\t' read -r bid cat topic <<<"$row"
    run_batch "$bid" "$cat" "$topic" || fail=1
    count=$((count+1))
  done < <(missing_ids)
  echo "BASE_BATCHES_REQUESTED=$n"
  echo "BASE_BATCHES_ATTEMPTED=$count"
  status
  [ "$fail" -eq 0 ] || return 1
}

gap_missing_ids(){
python3 - "$ROOT/coverage-gaps.tsv" "$REVIEWS" <<'PYGAP'
import csv,json,re,sys
from pathlib import Path
p=Path(sys.argv[1])
if not p.exists() or p.stat().st_size==0: raise SystemExit(66)
b=list(csv.DictReader(open(p,encoding='utf-8'),delimiter='\t')); ids=[x['id'] for x in b]
val=set(); pat=re.compile(r'(?m)^INIT_V52_BATCH_ID=([^\s]+)\s*$')
for f in Path(sys.argv[2]).glob('*.json'):
    try:
        d=json.load(open(f,encoding='utf-8'))
        if d.get('status')!='pending': continue
        s=str(d.get('summaryMarkdown',''))
    except:continue
    m=pat.search(s)
    if m: val.add(m.group(1))
for i in ids:
    if i not in val: print(i)
PYGAP
}

find_gap_row(){ awk -F '\t' -v id="$1" 'NR>1 && $1==id{print;exit}' "$ROOT/coverage-gaps.tsv"; }

run_next_gaps(){
  local n="${1:-5}"; [[ "$n" =~ ^[1-9][0-9]*$ ]] || { echo 'STOP_NEXT_COUNT_INVALID'; return 64; }
  [ "$n" -le 10 ] || { echo 'STOP_NEXT_COUNT_MAX_10'; return 64; }
  [ -s "$ROOT/coverage-gaps.tsv" ] || { echo 'STOP_NO_COVERAGE_GAPS_FILE'; return 66; }
  baseline
  local fail=0 count=0 id row bid cat topic
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    [ "$count" -lt "$n" ] || break
    row="$(find_gap_row "$id")"; [ -n "$row" ] || { log "missing gap row for $id"; fail=1; continue; }
    IFS=$'\t' read -r bid cat topic <<<"$row"
    run_batch "$bid" "$cat" "$topic" || fail=1
    count=$((count+1))
  done < <(gap_missing_ids)
  echo "GAP_BATCHES_REQUESTED=$n"
  echo "GAP_BATCHES_ATTEMPTED=$count"
  gap_status
  [ "$fail" -eq 0 ] || return 1
}

case "$cmd" in
  status) status ;;
  gap-status) gap_status ;;
  one)
    [ $# -eq 2 ] || { echo 'Usage: run-init.sh one BATCH_ID'; exit 64; }
    baseline
    row="$(find_row "$2")"; [ -n "$row" ] || { echo "STOP_UNKNOWN_BATCH=$2"; exit 65; }
    IFS=$'\t' read -r id cat topic <<<"$row"; run_batch "$id" "$cat" "$topic"; status ;;
  next) run_next_base "${2:-5}" ;;
  gaps-next) run_next_gaps "${2:-5}" ;;
  *) echo 'Usage: run-init.sh status|gap-status|next [1-10]|gaps-next [1-10]|one BATCH_ID'; exit 64 ;;
esac
