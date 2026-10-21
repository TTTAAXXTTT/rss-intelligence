#!/usr/bin/env bash
set -euo pipefail
fail=0
pass(){ echo "PASS $*"; }
bad(){ echo "FAIL $*"; fail=1; }
info(){ echo "INFO $*"; }
help_has(){ local pattern="$1"; shift; local out; out="$($@ --help 2>&1 || true)"; grep -q -- "$pattern" <<<"$out" && pass "$* supports $pattern" || bad "$* missing $pattern"; }
need(){ command -v "$1" >/dev/null 2>&1 && pass "$1 available" || bad "$1 missing"; }
OPENCLAW="$(command -v openclaw || true)"
[ -n "$OPENCLAW" ] && pass "openclaw=$OPENCLAW" || bad "openclaw not found"
if [ -n "$OPENCLAW" ]; then
  info "openclaw_version=$($OPENCLAW --version 2>&1 | head -n1 || true)"
  help_has '--session-key' "$OPENCLAW" agent; help_has '--model' "$OPENCLAW" agent; help_has '--timeout' "$OPENCLAW" agent; help_has '--json' "$OPENCLAW" agent; help_has '--message-file' "$OPENCLAW" agent
  help_has '--provider' "$OPENCLAW" models list; help_has '--json' "$OPENCLAW" models list
  help_has '--all' "$OPENCLAW" cron list; help_has '--json' "$OPENCLAW" cron list
  for x in --disabled --declaration-key --cron --tz --exact --session --timeout-seconds --tools --no-deliver --json --model --fallbacks; do help_has "$x" "$OPENCLAW" cron create; done
  for x in --model --fallbacks --message --tools; do help_has "$x" "$OPENCLAW" cron edit; done
  for x in --wait --wait-timeout --poll-interval --expect-final --timeout; do help_has "$x" "$OPENCLAW" cron run; done
  "$OPENCLAW" cron disable --help 2>&1 | grep -qi disable && pass "cron disable available" || bad "cron disable unavailable"
  "$OPENCLAW" cron enable --help 2>&1 | grep -qi enable && pass "cron enable available" || bad "cron enable unavailable"
  help_has '--link' "$OPENCLAW" plugins install; help_has '--force' "$OPENCLAW" plugins install; help_has '--runtime' "$OPENCLAW" plugins inspect; help_has '--entry' "$OPENCLAW" plugins build; help_has '--entry' "$OPENCLAW" plugins validate
  help_has '--strict-json' "$OPENCLAW" config set; help_has '--agent' "$OPENCLAW" sandbox recreate; help_has '--force' "$OPENCLAW" sandbox recreate
  models="$($OPENCLAW models list --provider opencode-go --json 2>/dev/null || true)"
  grep -q 'opencode-go/deepseek-v4-pro' <<<"$models" && pass 'OpenCode Go DeepSeek V4 Pro catalog' || bad 'missing opencode-go/deepseek-v4-pro'
  grep -q 'opencode-go/deepseek-v4-flash' <<<"$models" && pass 'OpenCode Go DeepSeek V4 Flash catalog' || bad 'missing opencode-go/deepseek-v4-flash'
  "$OPENCLAW" cron list --all --json >/tmp/rss-v52-preflight-crons.json 2>/dev/null || true
  python3 - /tmp/rss-v52-preflight-crons.json <<'PY' || fail=1
import json,sys
ids={'5b758d06-5857-5070-abd6-79ffb622e472','8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634','f57c34f5-4333-5156-9efc-d60ec0547010'}
try:d=json.load(open(sys.argv[1],encoding='utf-8'))
except Exception as e: print('FAIL cron JSON unreadable',e); raise SystemExit(1)
x={j.get('id'):j for j in d.get('jobs',[])}; bad=False
for i in ids:
 j=x.get(i)
 if not j: print('FAIL old RSS cron missing',i); bad=True; continue
 p=j.get('payload') or {}
 if j.get('enabled') is not True: print('FAIL old RSS cron not enabled before V5.2 cutover',i); bad=True
 elif p.get('model')!='opencode-go/deepseek-v4-pro' or p.get('fallbacks')!=['opencode-go/deepseek-v4-flash']:
  print('FAIL old RSS cron model/fallback not migrated to OpenCode Go',i,p.get('model'),p.get('fallbacks')); bad=True
 else: print('PASS old RSS cron ready',i)
raise SystemExit(1 if bad else 0)
PY
  rm -f /tmp/rss-v52-preflight-crons.json
  runtime="$($OPENCLAW plugins inspect rss-curator --runtime --json 2>/dev/null || true)"
  grep -q '"status"[[:space:]]*:[[:space:]]*"loaded"' <<<"$runtime" && pass 'rss-curator loaded' || bad 'rss-curator not loaded'
  for tool in freshrss_list_subscriptions freshrss_recent_articles rss_report_save rss_report_get_latest rss_review_save rss_review_get_pending; do grep -q "$tool" <<<"$runtime" && pass "rss-curator tool $tool" || bad "rss-curator missing $tool"; done
  sbx="$($OPENCLAW sandbox explain --agent main 2>/dev/null || true)"
  grep -q '/home/ubuntu/.openclaw/workspace -> /agent ro' <<<"$sbx" && pass 'workspace mounted /agent ro' || bad 'expected /agent ro mount missing'
fi
for c in timeout flock unzip openssl python3 systemd-analyze systemctl loginctl docker curl apache2ctl readlink awk grep sed sha256sum node npm stat find install sudo; do need "$c"; done
systemctl --user is-active --quiet openclaw-gateway.service && pass 'OpenClaw Gateway active' || bad 'OpenClaw Gateway inactive'
docker inspect freshrss >/dev/null 2>&1 && pass 'FreshRSS container exists' || bad 'FreshRSS container missing'
[ "$(docker inspect -f '{{.State.Running}}' freshrss 2>/dev/null || true)" = true ] && pass 'FreshRSS container running' || bad 'FreshRSS container not running'
docker exec freshrss test -f /var/www/FreshRSS/app/actualize_script.php >/dev/null 2>&1 && pass 'FreshRSS actualize script exists' || bad 'FreshRSS actualize script missing'
systemctl is-active --quiet apache2 && pass 'Apache active' || bad 'Apache inactive'
sudo -n true >/dev/null 2>&1 && pass 'passwordless sudo available' || bad 'sudo -n unavailable'
[ "$(systemctl is-enabled freshrss-actualize.timer 2>/dev/null || true)" = enabled ] && pass 'existing freshrss-actualize.timer enabled before cutover' || bad 'existing freshrss-actualize.timer not enabled'
for t in rss-v51-freshrss-fetch.timer rss-v51-morning.timer rss-v51-afternoon.timer rss-v51-review.timer; do
  state="$(systemctl --user is-enabled "$t" 2>/dev/null || true)"
  if [ "$state" != enabled ] && [ "$state" != enabled-runtime ]; then pass "legacy V5.1 timer not enabled: $t"; else bad "legacy V5.1 timer unexpectedly enabled: $t state=$state"; fi
done
for s in rss-v51-freshrss-fetch.service rss-v51-morning.service rss-v51-afternoon.service rss-v51-review.service; do
  pid="$(systemctl --user show "$s" -p MainPID --value 2>/dev/null || echo 0)"
  [ -z "$pid" ] && pid=0
  [ "$pid" = 0 ] && pass "legacy V5.1 service not running: $s" || bad "legacy V5.1 service still running: $s pid=$pid"
done
cronmin="$(docker inspect freshrss --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n 's/^CRON_MIN=//p' | tail -n1)"
[ -z "$cronmin" ] && pass 'FreshRSS container CRON_MIN disabled' || bad "FreshRSS container CRON_MIN unexpectedly set"
for p in /home/ubuntu/.openclaw/workspace/RSS_POLICY.md /home/ubuntu/.openclaw/workspace/PROMPT_MORNING.md /home/ubuntu/.openclaw/workspace/PROMPT_AFTERNOON.md /home/ubuntu/.openclaw/workspace/PROMPT_REVIEW.md; do [ -f "$p" ] && pass "$p exists" || bad "$p missing"; done
for d in /home/ubuntu/.openclaw/rss-intelligence/reports /home/ubuntu/.openclaw/rss-intelligence/reviews; do [ -d "$d" ] && pass "$d exists" || bad "$d missing"; done
# FreshRSS API credentials: prefer rss-curator/.env, fall back to global .env. Never print values.
CRED=''
for f in /home/ubuntu/openclaw-plugins/rss-curator/.env /home/ubuntu/.openclaw/.env; do
  if [ -f "$f" ] && grep -q '^FRESHRSS_API_USER=.' "$f" && grep -q '^FRESHRSS_API_PASSWORD=.' "$f"; then CRED="$f"; break; fi
done
if [ -n "$CRED" ]; then
  pass "FreshRSS API credential file found: $CRED"
  mode="$(stat -c '%a' "$CRED" 2>/dev/null || true)"; [ "$mode" = 600 ] && pass 'FreshRSS credential mode=600' || bad "FreshRSS credential mode=$mode expected 600"
else bad 'FreshRSS API credentials not found in curator/global env'; fi
[ -f /home/ubuntu/.openclaw/.env ] && pass 'global OpenClaw .env exists' || bad 'global OpenClaw .env missing'
if [ -f /home/ubuntu/.openclaw/.env ]; then mode="$(stat -c '%a' /home/ubuntu/.openclaw/.env 2>/dev/null || true)"; [ "$mode" = 600 ] && pass 'global .env mode=600' || bad "global .env mode=$mode expected 600"; fi
CUR=/home/ubuntu/openclaw-plugins/rss-curator
[ -d "$CUR/node_modules" ] && pass 'rss-curator node_modules exists' || bad 'rss-curator node_modules missing'
for p in "$CUR/node_modules/.bin/tsc" "$CUR/node_modules/.bin/vitest" "$CUR/node_modules/typebox" "$CUR/node_modules/@types/node"; do [ -e "$p" ] && pass "dependency $p" || bad "dependency missing $p"; done
if [ -e "$CUR/node_modules/openclaw" ] || [ -d /usr/lib/node_modules/openclaw ]; then pass 'OpenClaw module source available'; else bad 'OpenClaw module source missing'; fi
if [ "$fail" -eq 0 ]; then echo PREFLIGHT_OK; else echo PREFLIGHT_FAILED; exit 1; fi
