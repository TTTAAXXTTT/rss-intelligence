#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
PKG="${1:-/home/ubuntu/rss-intelligence-v5.2}"
[ -d "$PKG" ] || { echo "PACKAGE_SELFTEST_FAIL missing=$PKG"; exit 1; }
fail=0
TSLIB="${RSS_TYPESCRIPT_LIB:-/home/ubuntu/openclaw-plugins/rss-curator/node_modules/typescript/lib/typescript.js}"
V53_PYTHON="${RSS_V53_PYTHON_BIN:-/home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/python}"
if [ ! -f "$TSLIB" ]; then TSLIB="$(npm root -g 2>/dev/null)/typescript/lib/typescript.js"; fi
[ -f "$TSLIB" ] || { echo "PACKAGE_SELFTEST_FAIL TypeScript runtime library missing"; exit 1; }
[ -x "$V53_PYTHON" ] || { echo "PACKAGE_SELFTEST_FAIL V5.3 Python runtime missing"; exit 1; }
ok(){ echo "PASS $*"; }
bad(){ echo "FAIL $*"; fail=1; }

while IFS= read -r -d '' f; do bash -n "$f" && ok "bash $(basename "$f")" || bad "bash $f"; done < <(find "$PKG" -type d \( -name node_modules -o -name .git -o -name .venv \) -prune -o -type f -name '*.sh' -print0)
python3 - "$PKG" <<'PY' && echo 'PASS python compile' || { echo 'FAIL python compile'; exit 1; }
import pathlib,sys
root=pathlib.Path(sys.argv[1])
for p in root.rglob('*.py'):
 if not set(p.relative_to(root).parts) & {'.git','.venv','node_modules','__pycache__'}: compile(p.read_text(encoding='utf-8'),str(p),'exec')
PY
node - "$PKG" "$TSLIB" <<'JS' && echo 'PASS TypeScript syntax transpile' || { echo 'FAIL TypeScript syntax'; exit 1; }
const fs=require('fs'),path=require('path'); const ts=require(process.argv[3]);
function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){const p=path.join(d,e.name);if(e.isDirectory()){if(!['node_modules','.git','.venv','dist'].includes(e.name))walk(p);}else if(p.endsWith('.ts')){const s=fs.readFileSync(p,'utf8');const o=ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.NodeNext},reportDiagnostics:true});const errs=(o.diagnostics||[]).filter(x=>x.category===ts.DiagnosticCategory.Error);if(errs.length){console.error(p,errs.map(x=>x.messageText));process.exit(1);}}}} walk(process.argv[2]);
JS

grep -q $'A_XCP_01\tA-AIxCryptoPayment\t' "$PKG/01-init-kit/batches.tsv" && ok 'canonical A-AIxCryptoPayment' || bad 'canonical A category missing'
grep -q $'B_POLICY_01\tB-政策监管\t' "$PKG/01-init-kit/batches.tsv" && ok 'canonical B-政策监管' || bad 'canonical B policy category missing'
if grep -R -n -E 'volcengine-ark|deepseek-v4-(pro|flash)-260425' "$PKG" --exclude='checksums.sha256' --exclude='package-selftest.sh' --exclude='*.bak' --exclude='*.pre-*' --exclude-dir='__pycache__' --exclude-dir='node_modules' --exclude-dir='.git' --exclude-dir='.venv' >/tmp/v52-stale.txt; then cat /tmp/v52-stale.txt; bad 'stale Volcengine references'; else ok 'no stale Volcengine refs'; fi
if grep -nE '^  (all|retry-missing|gaps)\)' "$PKG/01-init-kit/run-init.sh" >/dev/null; then bad 'unbounded init command remains'; else ok 'no unbounded init command'; fi

# Core V5.2 paths and tool isolation.
grep -q "rss-daily-stage-v52" "$PKG/04-daily-pipeline/daily_common.py" && grep -q "fetch-state-v52" "$PKG/04-daily-pipeline/daily_common.py" && grep -q "rss-daily-stage-v52" "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && ok 'V5.2 runtime paths isolated' || bad 'V5.2 runtime path missing'
grep -q '"id": "rss-intelligence-stage-v52"' "$PKG/02-rss-intelligence-stage/openclaw.plugin.json" && grep -q 'rss_daily_finalize_save_v52' "$PKG/02-rss-intelligence-stage/openclaw.plugin.json" && ok 'V5.2 private stage tool isolated' || bad 'V5.2 plugin isolation missing'
python3 - "$PKG/02-rss-intelligence-stage/openclaw.plugin.json" "$PKG/02-rss-intelligence-stage/package.json" <<'PYMANIFEST' && ok 'native plugin generated metadata contract present' || bad 'native plugin generated metadata missing/invalid'
import json,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
p=json.load(open(sys.argv[2],encoding='utf-8'))
assert m.get('id')=='rss-intelligence-stage-v52'
cs=m.get('configSchema')
assert isinstance(cs,dict) and cs.get('type')=='object' and cs.get('additionalProperties') is False
assert isinstance(cs.get('properties',{}),dict)
assert m.get('activation')=={'onStartup': True}
assert m.get('contracts',{}).get('tools')==['rss_daily_finalize_save_v52']
assert m.get('toolMetadata',{}).get('rss_daily_finalize_save_v52',{}).get('optional') is True
assert 'tools' not in m, 'legacy top-level tools must not be shipped'
scripts=p.get('scripts',{})
assert 'openclaw plugins build --entry ./dist/index.js' in scripts.get('plugin:build','')
assert 'openclaw plugins build --entry ./dist/index.js --check' in scripts.get('plugin:validate','')
assert 'openclaw plugins validate --entry ./dist/index.js' in scripts.get('plugin:validate','')
assert p.get('openclaw',{}).get('extensions')==['./dist/index.js']
PYMANIFEST
if grep -R -n 'rss_intelligence_publish\|rss_report_publish' "$PKG/03-workspace-files" "$PKG/06-cutover/create-v52-cron-templates.sh" >/tmp/v52-pubtool.txt; then cat /tmp/v52-pubtool.txt; bad 'public publish tool exposed to Finalizer'; else ok 'Finalizer has no public publish tool'; fi

# Snapshot must be Host-only; prepare must never return to live FreshRSS.
if grep -nE 'OPENCLAW|subprocess\.run.*agent' "$PKG/04-daily-pipeline/snapshot-daily.py" >/dev/null; then bad 'snapshot script contains model transport'; else ok 'snapshot is Host-only'; fi
if grep -nE 'fresh_config\(|login_freshrss\(|get_json\(' "$PKG/04-daily-pipeline/prepare-daily.py" >/dev/null; then bad 'prepare can access live FreshRSS'; else ok 'prepare consumes immutable snapshot only'; fi
grep -q 'MAX_SNAPSHOT_PAGES' "$PKG/04-daily-pipeline/snapshot-daily.py" && grep -q 'SNAPSHOT_CAPACITY_EXCEEDED' "$PKG/04-daily-pipeline/snapshot-daily.py" && ok 'capacity checked before model phase' || bad 'snapshot capacity guard missing'
grep -q 'MAX_CLASSIFIER_INPUT_CHARS' "$PKG/04-daily-pipeline/prepare-daily.py" && grep -q 'MAX_CLASSIFIER_ARTICLES' "$PKG/04-daily-pipeline/prepare-daily.py" && ok 'dynamic dual classifier limits present' || bad 'dynamic classifier limit missing'
grep -q 'PREPARE_YIELD_CONTINUE' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && grep -q 'prep_rc" -eq 75' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && ok 'yield is separate from failure retry' || bad 'yield semantics missing'
grep -q 'DAILY_LOCK_TIMEOUT_NOT_SUCCESS' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && grep -q 'FETCH_ALREADY_RUNNING_NOT_SUCCESS' "$PKG/04-retry-scheduler/rss-v52-freshrss-fetch-retry.sh" && ok 'lock conflicts are not false success' || bad 'lock false-success guard missing'
grep -q 'v52-source-dataset.lock' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && grep -q 'v52-source-dataset.lock' "$PKG/04-retry-scheduler/rss-v52-freshrss-fetch-retry.sh" && ok 'snapshot/fetch share source dataset lock' || bad 'source lock not shared'
grep -q 'Stable across continuation page/ordinal movement' "$PKG/04-daily-pipeline/daily_common.py" && ok 'synthetic article IDs use pagination-stable seed' || bad 'stable synthetic article ID guard missing'
grep -q 'bootstrap-quarantine.json' "$PKG/04-daily-pipeline/prepare-daily.py" && grep -q 'bootstrap-state-v52.json' "$PKG/04-daily-pipeline/host-publish.py" && ok 'bootstrap quarantine and commit state present' || bad 'bootstrap lifecycle missing'
grep -q 'quarantinedArticleIds' "$PKG/04-daily-pipeline/prepare-daily.py" && grep -q 'quarantinedArticleIds' "$PKG/04-daily-pipeline/host-publish.py" && ok 'persistent bootstrap quarantine prevents cross-schedule replay' || bad 'persistent quarantine state missing'
timer_persistence_ok=1
for timer in "$PKG"/04-retry-scheduler/*.timer; do
  timer_name="$(basename "$timer")"
  if [ "$timer_name" = rss-v53-translation-cleanup.timer ] || [[ "$timer_name" = rss-v53-daily-catchup-*.timer ]]; then
    grep -q '^Persistent=true$' "$timer" || timer_persistence_ok=0
  else
    grep -q '^Persistent=false$' "$timer" || timer_persistence_ok=0
  fi
done
[ "$timer_persistence_ok" -eq 1 ] && ok 'scheduled reports stay non-persistent; guarded catch-up and expiry cleanup persist' || bad 'timer persistence policy'
grep -q 'fetch-state-v52' "$PKG/06-cutover/verify-v52-e2e.py" && grep -q "'schemaVersion':2" "$PKG/06-cutover/verify-v52-e2e.py" && ok 'E2E gate uses V5.2 state/schema' || bad 'E2E gate stale path/schema'
grep -q 'V51_TIMERS=' "$PKG/06-cutover/cutover-v52.sh" && grep -q 'rss-v51-morning.timer' "$PKG/06-cutover/rollback-v52.sh" && ok 'cutover/rollback prevent V5.1 double scheduling' || bad 'legacy V5.1 scheduler guard missing'

# Fixtures.
python3 "$PKG/00-preflight/init-fixture-test.py" "$PKG" && ok 'init merge/dedup/already-subscribed/category fixture' || bad 'init merge fixture'
python3 "$PKG/00-preflight/snapshot-fixture-test.py" "$PKG" && ok 'immutable snapshot/reuse/capacity fixture' || bad 'snapshot fixture'
python3 "$PKG/00-preflight/daily-fixture-test.py" "$PKG" && ok 'bootstrap/dynamic-chunk/yield/accounting fixture' || bad 'daily fixture'
python3 "$PKG/00-preflight/extract-agent-envelope-fixture-test.py" && ok 'agent envelope extraction/normalization fixture' || bad 'agent envelope fixture'
python3 "$PKG/00-preflight/host-finalize-fixture-test.py" && ok 'deterministic Host Finalizer/accounting/baseline/cap fixture' || bad 'Host Finalizer fixture'
python3 "$PKG/00-preflight/host-review-fixture-test.py" && ok 'deterministic read-only Host Review fixture' || bad 'Host Review fixture'
"$V53_PYTHON" "$PKG/00-preflight/host-publish-v53-fixture-test.py" && ok 'presentation/translation/XML publisher fixture' || bad 'V5.3 publisher fixture'
"$V53_PYTHON" "$PKG/00-preflight/host-translate-v53-fixture-test.py" && ok 'translation retention/exact-coverage/session fixture' || bad 'V5.4 translation fixture'
"$V53_PYTHON" "$PKG/00-preflight/source-health-v53-fixture-test.py" && ok 'source-health consecutive-error policy fixture' || bad 'V5.3 source-health fixture'
grep -q 'HOST_FINALIZE_BIN' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && grep -q 'HOST_FINALIZER_START' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && ok 'daily runner invokes Host Finalizer' || bad 'Host Finalizer runner integration missing'
grep -q 'HOST_TRANSLATE_BIN' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && grep -q 'HOST_TRANSLATION_START' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" && ok 'daily runner invokes bounded translation stage' || bad 'translation runner integration missing'
grep -q "COVERAGE_POLICY = 'all-stage-items-v1'" "$PKG/04-daily-pipeline/host-translate.py" && grep -q "set(rows)!=expected" "$PKG/04-daily-pipeline/host-publish.py" && ok 'all published items require exact full-translation coverage' || bad 'exact translation coverage missing'
if grep -n -- '--degraded-only' "$PKG/04-daily-pipeline/host-translate.py" "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" >/tmp/v54-degraded.txt; then cat /tmp/v54-degraded.txt; bad 'empty/partial translation fallback remains'; else ok 'no empty translation fallback'; fi
grep -q -- '--reconcile-expired' "$PKG/04-retry-scheduler/rss-v53-translation-cleanup.service" && ok 'expiry cleanup reprojects feeds before deleting pages' || bad 'expiry feed reprojection missing'
grep -q 'brief.xml' "$PKG/04-daily-pipeline/host-publish.py" && grep -q 'class="cover"' "$PKG/04-daily-pipeline/host-publish.py" && ok 'brief report and cover presentation present' || bad 'brief/cover presentation missing'
if grep -nE 'openclaw[[:space:]]+cron[[:space:]]+run|V52_JOB_FILE|FINALIZER_(MORNING|AFTERNOON)_JOB' "$PKG/04-retry-scheduler/rss-v52-daily-retry-runner.sh" >/tmp/v52-model-finalizer.txt; then cat /tmp/v52-model-finalizer.txt; bad 'daily runner still depends on model Finalizer cron'; else ok 'daily runner has no model Finalizer cron dependency'; fi
if grep -nE '(^|[[:space:]])(import|from)[[:space:]]+(subprocess|requests|urllib\.request)|cron[[:space:]]+run|FreshRSS|FRESHRSS_API|rss_daily_finalize_save' "$PKG/04-daily-pipeline/host-finalize.py" >/tmp/v52-host-finalizer-boundary.txt; then cat /tmp/v52-host-finalizer-boundary.txt; bad 'Host Finalizer crosses no-network/no-model boundary'; else ok 'Host Finalizer is local deterministic code only'; fi
grep -q 'HOST_REVIEW_BIN' "$PKG/04-retry-scheduler/rss-v52-review-retry-runner.sh" && grep -q 'HOST_REVIEW_SUCCESS' "$PKG/04-retry-scheduler/rss-v52-review-retry-runner.sh" && ok 'review runner invokes deterministic Host Review' || bad 'Host Review runner integration missing'
if grep -nE 'openclaw-runtime|cron[[:space:]]+run|V52_JOB_FILE|REVIEW_JOB' "$PKG/04-retry-scheduler/rss-v52-review-retry-runner.sh" >/tmp/v52-model-review.txt; then cat /tmp/v52-model-review.txt; bad 'review runner still depends on model cron'; else ok 'review runner has no model cron dependency'; fi
grep -q "'changes': \[\]" "$PKG/04-daily-pipeline/host-review.py" && grep -q "'modifiesFreshRss': False" "$PKG/04-daily-pipeline/host-review.py" && grep -q 'subscription/list?output=json' "$PKG/04-daily-pipeline/host-review.py" && ok 'Host Review is read-only and emits no changes' || bad 'Host Review read-only boundary missing'
grep -q 'PRAGMA query_only = ON' "$PKG/04-daily-pipeline/source-health.py" && grep -q 'subscriptionMutation.*False' "$PKG/04-daily-pipeline/source-health.py" && ! grep -nE 'subscription/edit|ac=subscribe|method=.POST' "$PKG/04-daily-pipeline/source-health.py" >/dev/null && ok 'source health monitor is read-only' || bad 'source health read-only boundary missing'
grep -q 'already_succeeded' "$PKG/04-daily-pipeline/rss-v53-daily-catchup.sh" && grep -q 'reportDateKey' "$PKG/04-daily-pipeline/rss-v53-daily-catchup.sh" && grep -q '^OnCalendar=\*-\*-\* 08:30:00 Asia/Shanghai$' "$PKG/04-retry-scheduler/rss-v53-daily-catchup-morning.timer" && grep -q '^OnCalendar=\*-\*-\* 14:30:00 Asia/Shanghai$' "$PKG/04-retry-scheduler/rss-v53-daily-catchup-afternoon.timer" && ok 'guarded daily catch-up schedule present' || bad 'daily catch-up guard/schedule missing'
grep -q 'atomic_json(current, result, compact=False)' "$PKG/04-daily-pipeline/prepare-daily.py" && ok 'Finalizer handoff is multiline-readable' || bad 'Finalizer handoff is compact single-line JSON'
grep -q 'Retain at most 24 highest-value final events' "$PKG/03-workspace-files/PROMPT_MORNING_V52.md" && grep -q 'Retain at most 16 highest-value final events' "$PKG/03-workspace-files/PROMPT_AFTERNOON_V52.md" && ok 'Finalizer terminal-call output budgets' || bad 'Finalizer output budget missing'

# Apache patch fixture remains idempotent and touches only HTTPS vhost.
TAP="$(mktemp -d)"
cat >"$TAP/site.conf" <<'APACHEFIX'
<VirtualHost *:80>
  ServerName rss.example.com
  ProxyPass / http://127.0.0.1:8080/
</VirtualHost>
<VirtualHost *:443>
  ServerName rss.example.com
  SSLEngine on
  ProxyPass / http://127.0.0.1:8080/
</VirtualHost>
APACHEFIX
apache_ok=1
APACHE_SITES_ENABLED_GLOB="$TAP/*.conf" python3 "$PKG/05-apache/patch_apache_ai_rss.py" --check >/tmp/v52-apache-check1.txt || apache_ok=0
APACHE_SITES_ENABLED_GLOB="$TAP/*.conf" python3 "$PKG/05-apache/patch_apache_ai_rss.py" --apply >/tmp/v52-apache-apply.txt || apache_ok=0
APACHE_SITES_ENABLED_GLOB="$TAP/*.conf" python3 "$PKG/05-apache/patch_apache_ai_rss.py" --check >/tmp/v52-apache-check2.txt || apache_ok=0
python3 - "$TAP/site.conf" <<'PYAPACHE' || apache_ok=0
import re,sys
s=open(sys.argv[1],encoding='utf-8').read(); blocks=re.findall(r'(?is)<VirtualHost\b[^>]*>.*?</VirtualHost>',s)
assert len(blocks)==2
assert '# BEGIN RSS INTELLIGENCE STATIC' not in blocks[0]
assert blocks[1].count('# BEGIN RSS INTELLIGENCE STATIC')==1
assert blocks[1].index('ProxyPass /intelligence/ !') < blocks[1].index('ProxyPass / http://127.0.0.1:8080/')
PYAPACHE
[ "$apache_ok" -eq 1 ] && ok 'Apache HTTPS-vhost idempotent fixture' || bad 'Apache fixture'
rm -rf "$TAP"; rm -f /tmp/v52-apache-check1.txt /tmp/v52-apache-apply.txt /tmp/v52-apache-check2.txt

# systemd unit syntax + calendar.
TUNIT="$(mktemp -d)"
cp "$PKG"/04-retry-scheduler/*.service "$PKG"/04-retry-scheduler/*.timer "$TUNIT"/
sed -i 's#^ExecStart=.*#ExecStart=/bin/true#' "$TUNIT"/*.service
if systemd-analyze verify "$TUNIT"/*.service "$TUNIT"/*.timer >/tmp/v52-systemd-verify.txt 2>&1; then ok 'systemd unit static verify'; else cat /tmp/v52-systemd-verify.txt; bad 'systemd unit static verify'; fi
cal_ok=1
for spec in '*-*-* 07:00:00 Asia/Shanghai' '*-*-* 07:03:00 Asia/Shanghai' '*-*-* 07:10:00 Asia/Shanghai' '*-*-* 13:00:00 Asia/Shanghai' '*-*-* 13:03:00 Asia/Shanghai' '*-*-* 13:10:00 Asia/Shanghai' '*-*-* *:*:00 Asia/Shanghai' '*-*-15 03:00:00 Asia/Shanghai' '*-*-28 03:00:00 Asia/Shanghai'; do systemd-analyze calendar "$spec" >/dev/null 2>&1 || cal_ok=0; done
[ "$cal_ok" -eq 1 ] && ok 'Asia/Shanghai calendar parse' || bad 'calendar parse'
rm -rf "$TUNIT"; rm -f /tmp/v52-systemd-verify.txt

# Runtime smoke for private Stage V5.2.
TSTAGE="$(mktemp -d)"
node - "$TSTAGE" "$PKG/02-rss-intelligence-stage/src/stage.ts" "$TSLIB" <<'JSSTAGE'
const fs=require('fs'),ts=require(process.argv[4]);
const out=ts.transpileModule(fs.readFileSync(process.argv[3],'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;fs.writeFileSync(process.argv[2]+'/stage.mjs',out);
JSSTAGE
mkdir -p "$TSTAGE/c"
printf '%s' '{"schemaVersion":2,"edition":"morning","runId":"r1","allPagesComplete":true,"candidates":[{"primaryArticleId":"a1"},{"primaryArticleId":"a2"}]}' > "$TSTAGE/c/morning-candidates.json"
node --input-type=module - "$TSTAGE" <<'JSSTAGE2' && ok 'private stage V5.2 exact-accounting smoke' || bad 'private stage V5.2 smoke'
const root=process.argv[2]; const {saveFinalStage}=await import('file://'+root+'/stage.mjs');
const x=await saveFinalStage({edition:'morning',candidateRunId:'r1',title:'晨报',reportMarkdown:'# 晨报',accounting:[{primaryArticleId:'a1',disposition:'retained',finalEventKey:'evt1',reason:'r'},{primaryArticleId:'a2',disposition:'merged',finalEventKey:'evt1',reason:'m'}],items:[{eventKey:'evt1',title:'T',priority:'S',category:'S-链上金融',summary:'s',whyImportant:'w',impact:'i',isHighValueOpportunity:false,sources:[{name:'n',url:'https://example.com/a'}]}]},root+'/c',root+'/s'); if(!x.saved||x.publicPublished!==false||x.itemCount!==1)process.exit(1);
JSSTAGE2
rm -rf "$TSTAGE"

# Host publisher smoke: schema2 candidate/stage, full report, XML, bootstrap commit, watermark last.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/current" "$TMP/state"
cat > "$TMP/current/morning-candidates.json" <<'JSON'
{"schemaVersion":2,"edition":"morning","runId":"morning-test-1","allPagesComplete":true,"sourceFetchRunId":"fetch-test","sourceFetchFinishedAt":"2026-08-11T00:00:00Z","normalCutoffTimestampSec":1,"effectiveCutoffTimestampSec":1,"bootstrapFeedIds":["feed/1"],"hostBootstrapBackfillArticles":1,"bootstrapQuarantineFile":"__QUARANTINE__","bootstrapQuarantineReleasedArticleIds":["released-old"],"candidates":[{"primaryArticleId":"a1"}]}
JSON
sed -i "s#__QUARANTINE__#$TMP/bootstrap-quarantine.json#" "$TMP/current/morning-candidates.json"
cat > "$TMP/bootstrap-quarantine.json" <<'JSONQ'
[{"id":"old-bootstrap-1","feedKey":"feed/1","feedTitle":"F1","contentFingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","reason":"fixture"}]
JSONQ
python3 - "$TMP/stage.json" <<'PY'
import json,sys
long='# 晨报\n\n'+'完整正文。'*1800
x={"schemaVersion":2,"status":"staged","stageId":"s1","edition":"morning","candidateRunId":"morning-test-1","generatedAt":"2026-08-11T01:00:00Z","title":"08:00 情报晨报","reportMarkdown":long,"accounting":[{"primaryArticleId":"a1","disposition":"retained","finalEventKey":"https://example.com/A","reason":"keep"}],"items":[{"eventKey":"https://example.com/A","title":"测试","priority":"S","category":"S-链上金融","summary":"s","whyImportant":"w","impact":"i","isHighValueOpportunity":False,"sources":[{"name":"x","url":"https://example.com/A"}]}]}
open(sys.argv[1],'w',encoding='utf-8').write(json.dumps(x,ensure_ascii=False))
PY
cat > "$TMP/bootstrap.json" <<'JSONBOOT'
{"schemaVersion":2,"feeds":{},"quarantinedArticleIds":{"released-old":{"feedKey":"feed/old","contentFingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}
JSONBOOT
cat > "$TMP/env" <<EOFENV
RSS_PUBLISH_TOKEN=$(printf 'a%.0s' {1..64})
RSS_PUBLISH_DATA_ROOT=$TMP/data
RSS_PUBLISH_PUBLIC_ROOT=$TMP/public
RSS_PUBLISH_BASE_URL=https://example.invalid/intelligence
RSS_BOOTSTRAP_STATE=$TMP/bootstrap.json
RSS_TRANSLATION_ROOT=$TMP/translation-v53
EOFENV
token="$(printf 'a%.0s' {1..64})"
translation_name="$(printf 'b%.0s' {1..64}).html"
mkdir -p "$TMP/public/$token/translations"
mkdir -p "$TMP/translation-v53/manifests/morning"
chmod 700 "$TMP/translation-v53" "$TMP/translation-v53/manifests" "$TMP/translation-v53/manifests/morning"
printf '%s\n' '<!doctype html><html><body>完整译文</body></html>' > "$TMP/public/$token/translations/$translation_name"
chmod 644 "$TMP/public/$token/translations/$translation_name"
python3 - "$TMP/translation-v53/manifests/morning/translation-manifest.json" "$TMP/public/$token/translations/$translation_name" "$translation_name" <<'PYTRANS'
import hashlib,json,sys
from datetime import datetime,timedelta,timezone
p,page,name=sys.argv[1:]
token='a'*64
saved=datetime.now(timezone.utc).replace(microsecond=0); expires=saved+timedelta(hours=48)
fmt=lambda x:x.isoformat().replace('+00:00','Z')
x={
 'schemaVersion':1,'producer':'host-translation-v1','candidateRunId':'morning-test-1',
 'status':'complete','coveragePolicy':'all-stage-items-v1',
 'stageId':'s1','edition':'morning','generatedAt':fmt(saved),'savedAt':fmt(saved),'expiresAt':fmt(expires),
 'itemCount':1,'stageItemCount':1,
 'expectedKeysSha256':hashlib.sha256(json.dumps(['https://example.com/A'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
 'items':[{
  'eventKey':'https://example.com/A','primaryArticleId':'a1','originalUrl':'https://example.com/A',
  'translatedUrl':f'https://example.invalid/intelligence/{token}/translations/{name}',
  'publicFile':f'translations/{name}','savedAt':fmt(saved),'expiresAt':fmt(expires),
  'status':'complete','sourceMethod':'fixture','sourceCompleteness':'full','truncated':False,
  'sourceChars':100,'translatedChars':80,
  'outputHash':hashlib.sha256(open(page,'rb').read()).hexdigest(),
 }],
}
open(p,'w',encoding='utf-8').write(json.dumps(x,ensure_ascii=False))
PYTRANS
chmod 600 "$TMP/translation-v53/manifests/morning/translation-manifest.json"
OPENCLAW_ENV_FILE="$TMP/env" RSS_DAILY_STAGE_CURRENT="$TMP/current" RSS_DAILY_STATE_ROOT="$TMP/state" python3 "$PKG/04-daily-pipeline/host-publish.py" "$TMP/stage.json" --translation-manifest "$TMP/translation-v53/manifests/morning/translation-manifest.json" >/tmp/v52-host-pub.out
python3 - "$TMP" <<'PY' && ok 'host publisher V5.2 full-content/bootstrap/watermark smoke' || bad 'host publisher V5.2 smoke'
import json,sys,xml.etree.ElementTree as ET
from pathlib import Path
r=Path(sys.argv[1]); token='a'*64; pub=r/'public'/token
for n in ('all.xml','priority.xml','reports.xml','brief.xml'): ET.parse(pub/n)
state=json.loads((r/'state/last-success.json').read_text()); assert state['schemaVersion']==2 and state['candidateRunId']=='morning-test-1'
boot=json.loads((r/'bootstrap.json').read_text()); assert boot['schemaVersion']==2 and 'feed/1' in boot['feeds'] and 'old-bootstrap-1' in boot['quarantinedArticleIds'] and 'released-old' not in boot['quarantinedArticleIds']
report=(pub/'reports.xml').read_text(); assert '内容总结' in report and '阅读中文全文' in report
assert (pub/'2026-08-11-morning.html').read_text().count('class="cover"')==1
assert (pub/'2026-08-11-morning-brief.html').read_text().count('class="cover"')==1
PY
rm -f /tmp/v52-host-pub.out
trap - EXIT; rm -rf "$TMP"

# Runtime migration is non-destructive and never copies bootstrap state.
TMIG="$(mktemp -d)"; mkdir -p "$TMIG/publisher-data-v51" "$TMIG/daily-state-v51"; echo x > "$TMIG/publisher-data-v51/a"; echo '{}' > "$TMIG/daily-state-v51/a.json"
RSS_INTELLIGENCE_ROOT="$TMIG" python3 "$PKG/04-daily-pipeline/migrate-v51-runtime.py" --apply >/tmp/v52-migrate.out
[ -f "$TMIG/publisher-data-v52/a" ] && [ -f "$TMIG/daily-state-v52/a.json" ] && [ ! -e "$TMIG/bootstrap-state-v52.json" ] && ok 'runtime migration preserves history without bootstrap contamination' || bad 'runtime migration fixture'
rm -rf "$TMIG"; rm -f /tmp/v52-migrate.out

# Optional real incident replay supplied by caller.
if [ -n "${RSS_V52_INCIDENT_ROOT:-}" ]; then
  python3 "$PKG/00-preflight/incident-replay-test.py" "$PKG" "$RSS_V52_INCIDENT_ROOT" && ok 'real 4000-article incident replay' || bad 'real incident replay'
else
  ok 'real incident replay skipped (RSS_V52_INCIDENT_ROOT not set)'
fi

rm -f /tmp/v52-stale.txt /tmp/v52-pubtool.txt /tmp/v52-persistent-bad.txt /tmp/v52-model-finalizer.txt /tmp/v52-host-finalizer-boundary.txt /tmp/v52-model-review.txt /tmp/v54-degraded.txt
[ "$fail" -eq 0 ] && echo PACKAGE_SELFTEST_OK || { echo PACKAGE_SELFTEST_FAILED; exit 1; }
