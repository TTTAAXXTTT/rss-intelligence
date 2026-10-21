#!/usr/bin/env bash
set -euo pipefail
edition="${1:-}"
[ "$edition" = morning ] || [ "$edition" = afternoon ] || { echo 'Usage: rss-v52-daily-retry-runner.sh morning|afternoon'; exit 64; }
ROOT="${RSS_INTELLIGENCE_ROOT:-/home/ubuntu/.openclaw/rss-intelligence}"
LOGDIR="$ROOT/retry-logs"
mkdir -p "$LOGDIR"
chmod 700 "$LOGDIR" 2>/dev/null || true
LOG="$LOGDIR/v52-${edition}.log"
DAILY_LOCK="$ROOT/v52-daily-global.lock"
SOURCE_LOCK="$ROOT/v52-source-dataset.lock"
SNAPSHOT_BIN="${SNAPSHOT_DAILY_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/snapshot-daily.py}"
PREP="${PREPARE_DAILY_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/prepare-daily.py}"
HOST_FINALIZE_BIN="${HOST_FINALIZE_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/host-finalize.py}"
HOST_TRANSLATE_BIN="${HOST_TRANSLATE_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/host-translate.py}"
TRANSLATE_PYTHON_BIN="${TRANSLATE_PYTHON_BIN:-/home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/python}"
PUBLISH="${HOST_PUBLISH_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/host-publish.py}"
CANDROOT="${RSS_DAILY_STAGE_CURRENT:-/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current}"
FINAL_STAGE_ROOT="${RSS_FINALIZER_STAGE_ROOT:-$ROOT/finalizer-stage-v52}"
export RSS_FINALIZER_STAGE_ROOT="$FINAL_STAGE_ROOT"
TRANSLATION_ROOT="${RSS_TRANSLATION_ROOT:-$ROOT/translation-v53}"
export RSS_TRANSLATION_ROOT="$TRANSLATION_ROOT"
ENV="${OPENCLAW_ENV_FILE:-/home/ubuntu/.openclaw/.env}"

# Queue Morning/Afternoon rather than silently returning success when another daily run is active.
exec 8>"$DAILY_LOCK"
if ! flock -w "${RSS_DAILY_LOCK_WAIT_SECONDS:-10800}" 8; then
  echo "$(date -Is) [$edition] DAILY_LOCK_TIMEOUT_NOT_SUCCESS" | tee -a "$LOG"
  exit 75
fi

candidate_run_id(){
  python3 - "$CANDROOT/${edition}-candidates.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
assert d.get('schemaVersion')==2 and d.get('allPagesComplete') is True
print(d['runId'])
PY
}
watermark_matches(){
  python3 - "$ROOT/daily-state-v52/last-success.json" "$edition" "$1" <<'PY' >/dev/null 2>&1
import json,sys
p,e,r=sys.argv[1:]; d=json.load(open(p,encoding='utf-8'))
assert d.get('schemaVersion')==2 and d.get('status')=='ok' and d.get('edition')==e and d.get('candidateRunId')==r
PY
}
validate_host_stage(){
  python3 - "$1" "$FINAL_STAGE_ROOT" "$edition" "$2" <<'PY'
import json, os, stat, sys
from pathlib import Path

stage_arg, root_arg, edition, run_id = sys.argv[1:]
root = Path(root_arg)
edition_root = root / edition
stage = Path(stage_arg)

try:
    root_real = root.resolve(strict=True)
    edition_real = edition_root.resolve(strict=True)
    stage_real = stage.resolve(strict=True)
except (OSError, RuntimeError) as exc:
    raise SystemExit(f'HOST_FINAL_STAGE_PATH_INVALID: {exc}')

if edition_real.parent != root_real or stage_real.parent != edition_real:
    raise SystemExit('HOST_FINAL_STAGE_OUTSIDE_EXPECTED_ROOT')

for directory, label in ((root, 'root'), (edition_root, 'edition-root')):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
        raise SystemExit(f'HOST_FINAL_STAGE_{label.upper()}_INVALID')
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit(f'HOST_FINAL_STAGE_{label.upper()}_PERMISSION_INVALID')

info = stage.lstat()
if not stat.S_ISREG(info.st_mode) or stage.is_symlink():
    raise SystemExit('HOST_FINAL_STAGE_FILE_INVALID')
if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit('HOST_FINAL_STAGE_FILE_PERMISSION_INVALID')

try:
    data = json.loads(stage_real.read_text(encoding='utf-8'))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f'HOST_FINAL_STAGE_JSON_INVALID: {exc}')

if not isinstance(data, dict):
    raise SystemExit('HOST_FINAL_STAGE_JSON_NOT_OBJECT')
expected_producer = 'host-deterministic-v1'
producer = data.get('producer')
generator = data.get('generator')
if (
    data.get('schemaVersion') != 2
    or data.get('status') != 'staged'
    or expected_producer not in (producer, generator)
    or (producer is not None and producer != expected_producer)
    or (generator is not None and generator != expected_producer)
    or data.get('edition') != edition
    or data.get('candidateRunId') != run_id
):
    raise SystemExit('HOST_FINAL_STAGE_IDENTITY_INVALID')

print(stage_real)
PY
}
verify_public(){
  local marker="$1" token pubroot f
  mapfile -t publish_config < <(python3 - "$ENV" <<'PY'
import os,re,sys
values={}
path=sys.argv[1]
if os.path.isfile(path):
    for raw in open(path,encoding='utf-8'):
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key,value=line.split('=',1); value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"): value=value[1:-1]
        values[key.strip()]=value
values.update(os.environ)
print(values.get('RSS_PUBLISH_TOKEN',''))
print(values.get('RSS_PUBLISH_PUBLIC_ROOT','/var/www/rss-intelligence'))
PY
)
  [ "${#publish_config[@]}" -eq 2 ] || return 1
  token="${publish_config[0]}"; pubroot="${publish_config[1]}"
  [[ "$token" =~ ^[A-Fa-f0-9]{64}$ ]] || return 1
  for f in all.xml priority.xml reports.xml brief.xml; do
    [ -f "$pubroot/$token/$f" ] && [ "$pubroot/$token/$f" -nt "$marker" ] || return 1
  done
  python3 - "$pubroot/$token" <<'PY' >/dev/null 2>&1
import pathlib,re,sys,xml.etree.ElementTree as ET
root=pathlib.Path(sys.argv[1])
content='{http://purl.org/rss/1.0/modules/content/}encoded'
pattern=re.compile(r'https?://[^\s"<>]+/translations/([a-f0-9]{64}\.html)')
for name in ('all.xml','priority.xml','reports.xml','brief.xml'):
    doc=ET.parse(root/name); assert doc.getroot().tag=='rss'
    for item in doc.findall('./channel/item'):
        body=item.findtext(content) or item.findtext('description') or ''
        links=pattern.findall(body)
        if name in ('all.xml','priority.xml'):
            assert len(links)==1
            assert '/translations/' not in (item.findtext('link') or '')
        else:
            assert len(links)>=1
        for page in links:
            target=root/'translations'/page
            assert target.is_file() and not target.is_symlink()
PY
}
record_failure(){
  local stage="$1" rc="$2"
  failures=$((failures+1))
  echo "$(date -Is) [$edition] FAILURE $failures/3 stage=$stage rc=$rc" | tee -a "$LOG"
  if [ "$failures" -ge 3 ]; then
    echo "$(date -Is) [$edition] CANCEL current update after 3 true failures; next schedule remains" | tee -a "$LOG"
    exit 1
  fi
  local retry_wait="${RSS_DAILY_RETRY_WAIT_SECONDS:-45}"
  echo "$(date -Is) [$edition] RETRY_WAIT ${retry_wait}s" | tee -a "$LOG"
  sleep "$retry_wait"
}

failures=0
snapshot="$CANDROOT/${edition}-snapshot.json"

# Phase A: immutable Host-only snapshot. Hold the source lock only while reading the live FreshRSS dataset.
while :; do
  exec 7>"$SOURCE_LOCK"
  if ! flock -w "${RSS_SOURCE_LOCK_WAIT_SECONDS:-1800}" 7; then
    record_failure source-lock 75
    continue
  fi
  echo "$(date -Is) [$edition] SNAPSHOT_START" | tee -a "$LOG"
  set +e
  python3 "$SNAPSHOT_BIN" "$edition" --reuse >>"$LOG" 2>&1
  snap_rc=$?
  set -e
  flock -u 7 || true
  if [ "$snap_rc" -eq 0 ] && [ -s "$snapshot" ]; then
    echo "$(date -Is) [$edition] SNAPSHOT_READY file=$snapshot" | tee -a "$LOG"
    break
  fi
  if [ "$snap_rc" -eq 66 ]; then
    echo "$(date -Is) [$edition] SNAPSHOT_FATAL_CAPACITY rc=$snap_rc; no model calls were started" | tee -a "$LOG"
    exit 66
  fi
  record_failure snapshot "$snap_rc"
done

# Phase B onward: consume only the immutable snapshot. FreshRSS may actualize later without changing this run.
while :; do
  echo "$(date -Is) [$edition] PREPARE_START trueFailures=$failures/3" | tee -a "$LOG"
  set +e
  python3 "$PREP" "$edition" --snapshot "$snapshot" --reuse >>"$LOG" 2>&1
  prep_rc=$?
  set -e
  if [ "$prep_rc" -eq 75 ]; then
    echo "$(date -Is) [$edition] PREPARE_YIELD_CONTINUE trueFailures=$failures/3" | tee -a "$LOG"
    continue
  fi
  if [ "$prep_rc" -ne 0 ]; then
    record_failure prepare "$prep_rc"
    continue
  fi

  runid="$(candidate_run_id)" || { record_failure candidate-id 65; continue; }
  echo "$(date -Is) [$edition] HOST_FINALIZER_START candidateRunId=$runid" | tee -a "$LOG"
  set +e
  host_final_output="$(python3 "$HOST_FINALIZE_BIN" "$edition" 2>&1)"
  host_final_rc=$?
  set -e
  printf '%s\n' "$host_final_output" >>"$LOG"
  if [ "$host_final_rc" -ne 0 ]; then
    record_failure host-finalizer "$host_final_rc"
    continue
  fi

  HOST_FINAL_STAGE_PATH="$(printf '%s\n' "$host_final_output" | sed -n 's/^HOST_FINAL_STAGE_PATH=//p' | tail -n1)"
  if [ -z "$HOST_FINAL_STAGE_PATH" ] || [ "$HOST_FINAL_STAGE_PATH" != "${HOST_FINAL_STAGE_PATH//$'\n'/}" ]; then
    echo "$(date -Is) [$edition] HOST_FINAL_STAGE_PATH_MISSING_OR_UNSAFE" | tee -a "$LOG"
    record_failure host-final-stage-path 65
    continue
  fi
  set +e
  verified_stage="$(validate_host_stage "$HOST_FINAL_STAGE_PATH" "$runid" 2>>"$LOG")"
  verify_stage_rc=$?
  set -e
  if [ "$verify_stage_rc" -ne 0 ] || [ -z "$verified_stage" ]; then
    echo "$(date -Is) [$edition] HOST_FINAL_STAGE_VERIFY_FAIL rc=$verify_stage_rc" | tee -a "$LOG"
    record_failure host-final-stage-verify "$verify_stage_rc"
    continue
  fi
  HOST_FINAL_STAGE_PATH="$verified_stage"
  echo "$(date -Is) [$edition] HOST_FINAL_STAGE_READY candidateRunId=$runid file=$(basename -- "$HOST_FINAL_STAGE_PATH")" | tee -a "$LOG"

  echo "$(date -Is) [$edition] HOST_TRANSLATION_START candidateRunId=$runid" | tee -a "$LOG"
  translation_timeout="${RSS_TRANSLATION_TOTAL_TIMEOUT_SECONDS:-2400}"
  [[ "$translation_timeout" =~ ^[0-9]+$ ]] && [ "$translation_timeout" -ge 60 ] && [ "$translation_timeout" -le 3600 ] || {
    echo "$(date -Is) [$edition] HOST_TRANSLATION_TIMEOUT_CONFIG_INVALID" | tee -a "$LOG"
    record_failure host-translation-timeout-config 64
    continue
  }
  set +e
  host_translation_output="$(timeout --signal=TERM --kill-after=30s "${translation_timeout}s" "$TRANSLATE_PYTHON_BIN" "$HOST_TRANSLATE_BIN" "$HOST_FINAL_STAGE_PATH" --reuse 2>&1)"
  host_translation_rc=$?
  set -e
  printf '%s\n' "$host_translation_output" >>"$LOG"
  if [ "$host_translation_rc" -ne 0 ]; then
    echo "$(date -Is) [$edition] HOST_TRANSLATION_INCOMPLETE rc=$host_translation_rc; edition remains pending" | tee -a "$LOG"
    record_failure host-translation "$host_translation_rc"
    continue
  fi
  mapfile -t translation_markers < <(printf '%s\n' "$host_translation_output" | sed -n 's/^HOST_TRANSLATION_MANIFEST=//p')
  if [ "${#translation_markers[@]}" -ne 1 ] || [ -z "${translation_markers[0]}" ]; then
    echo "$(date -Is) [$edition] HOST_TRANSLATION_MANIFEST_MARKER_INVALID" | tee -a "$LOG"
    record_failure host-translation-manifest-marker 65
    continue
  fi
  HOST_TRANSLATION_MANIFEST="${translation_markers[0]}"
  set +e
  verified_manifest="$(python3 - "$HOST_TRANSLATION_MANIFEST" "$TRANSLATION_ROOT" "$edition" "$runid" "$HOST_FINAL_STAGE_PATH" <<'PY'
import json, os, stat, sys
from pathlib import Path

manifest_arg, root_arg, edition, run_id, stage_arg = sys.argv[1:]
raw_root = Path(root_arg)
raw_manifests = raw_root / 'manifests'
raw_edition = raw_manifests / edition
raw_path = Path(manifest_arg)
for directory, label in ((raw_root,'ROOT'),(raw_manifests,'MANIFESTS_ROOT'),(raw_edition,'EDITION_ROOT')):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or directory.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit(f'HOST_TRANSLATION_{label}_INVALID')
root = raw_root.resolve(strict=True)
manifests_root = raw_manifests.resolve(strict=True)
edition_root = raw_edition.resolve(strict=True)
raw_info = raw_path.lstat()
if not stat.S_ISREG(raw_info.st_mode) or raw_path.is_symlink() or raw_info.st_uid != os.geteuid() or stat.S_IMODE(raw_info.st_mode) != 0o600:
    raise SystemExit('HOST_TRANSLATION_MANIFEST_FILE_INVALID')
path = raw_path.resolve(strict=True)
stage = json.loads(Path(stage_arg).read_text(encoding='utf-8'))
if manifests_root.parent != root or edition_root.parent != manifests_root or path.parent != edition_root:
    raise SystemExit('HOST_TRANSLATION_MANIFEST_OUTSIDE_ROOT')
data = json.loads(path.read_text(encoding='utf-8'))
if (
    data.get('schemaVersion') != 1
    or data.get('producer') != 'host-translation-v1'
    or data.get('status') != 'complete'
    or data.get('coveragePolicy') != 'all-stage-items-v1'
    or data.get('edition') != edition
    or data.get('candidateRunId') != run_id
    or data.get('stageId') != stage.get('stageId')
):
    raise SystemExit('HOST_TRANSLATION_MANIFEST_IDENTITY_INVALID')
stage_keys={str(item.get('eventKey') or '') for item in stage.get('items') or []}
rows=data.get('items')
if (
    not isinstance(rows,list)
    or data.get('itemCount') != len(stage_keys)
    or data.get('stageItemCount') != len(stage_keys)
    or len(rows) != len(stage_keys)
    or {str(row.get('eventKey') or '') for row in rows if isinstance(row,dict)} != stage_keys
    or any(not isinstance(row,dict) or row.get('status')!='complete' or row.get('sourceCompleteness')!='full' or row.get('truncated') is not False for row in rows)
):
    raise SystemExit('HOST_TRANSLATION_MANIFEST_COVERAGE_INVALID')
print(path)
PY
)"
  verify_translation_rc=$?
  set -e
  if [ "$verify_translation_rc" -ne 0 ] || [ -z "$verified_manifest" ]; then
    echo "$(date -Is) [$edition] HOST_TRANSLATION_MANIFEST_VERIFY_FAIL rc=$verify_translation_rc" | tee -a "$LOG"
    record_failure host-translation-manifest "$verify_translation_rc"
    continue
  fi
  HOST_TRANSLATION_MANIFEST="$verified_manifest"
  echo "$(date -Is) [$edition] HOST_TRANSLATION_READY candidateRunId=$runid file=$(basename -- "$HOST_TRANSLATION_MANIFEST")" | tee -a "$LOG"

  marker="$(mktemp "$LOGDIR/.v52-${edition}-publish.XXXXXX")"
  touch "$marker"
  set +e
  python3 "$PUBLISH" "$HOST_FINAL_STAGE_PATH" --translation-manifest "$HOST_TRANSLATION_MANIFEST" >>"$LOG" 2>&1
  pub_rc=$?
  set -e
  if [ "$pub_rc" -eq 0 ] && verify_public "$marker" && watermark_matches "$runid"; then
    rm -f "$marker"
    echo "$(date -Is) [$edition] SUCCESS trueFailures=$failures candidateRunId=$runid" | tee -a "$LOG"
    exit 0
  fi
  rm -f "$marker"
  echo "$(date -Is) [$edition] PUBLISH_OR_VERIFY_FAIL pub_rc=$pub_rc candidateRunId=$runid" | tee -a "$LOG"
  record_failure publish "$pub_rc"
done
