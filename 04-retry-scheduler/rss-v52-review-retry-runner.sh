#!/usr/bin/env bash
set -euo pipefail

ROOT="${RSS_INTELLIGENCE_ROOT:-/home/ubuntu/.openclaw/rss-intelligence}"
STAGE="${RSS_DAILY_STAGE_CURRENT:-/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current}"
REVIEWS="${RSS_REVIEWS_DIR:-$ROOT/reviews}"
LOGDIR="$ROOT/retry-logs"
LOG="$LOGDIR/v52-review.log"
HOST_REVIEW_BIN="${HOST_REVIEW_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/host-review.py}"
MARKER="${RSS_REVIEW_RUN_FILE:-$STAGE/review-run.json}"
export RSS_REVIEWS_DIR="$REVIEWS"
export RSS_REVIEW_RUN_FILE="$MARKER"

mkdir -p "$STAGE" "$LOGDIR"
chmod 700 "$STAGE" "$LOGDIR" 2>/dev/null || true

exec 9>"$ROOT/v52-review.lock"
if ! flock -w "${RSS_REVIEW_LOCK_WAIT_SECONDS:-3600}" 9; then
  echo "$(date -Is) [review] REVIEW_LOCK_TIMEOUT_NOT_SUCCESS" | tee -a "$LOG"
  exit 75
fi

kind="${RSS_REVIEW_KIND:-semi-monthly}"
case "$kind" in
  semi-monthly|e2e) ;;
  *) echo "STOP_REVIEW_KIND_INVALID=$kind" | tee -a "$LOG"; exit 64 ;;
esac

runid="review-$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}-${RANDOM}"
reviewdate="$(TZ=Asia/Shanghai date +%F)"
python3 - "$MARKER" "$runid" "$reviewdate" "$kind" <<'PY'
import json, os, sys, tempfile
from pathlib import Path

path = Path(sys.argv[1])
run_id, review_date, review_kind = sys.argv[2:]
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump({
            'schemaVersion': 2,
            'runId': run_id,
            'reviewDate': review_date,
            'reviewKind': review_kind,
        }, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY

validate_host_review(){
  python3 - "$1" "$REVIEWS" "$MARKER" <<'PY'
import json, os, re, stat, sys
from pathlib import Path

artifact_arg, reviews_arg, marker_arg = sys.argv[1:]
artifact = Path(artifact_arg)
reviews = Path(reviews_arg)
marker_path = Path(marker_arg)

if not artifact.is_absolute():
    raise SystemExit('HOST_REVIEW_PATH_NOT_ABSOLUTE')
try:
    reviews_real = reviews.resolve(strict=True)
    artifact_real = artifact.resolve(strict=True)
except (OSError, RuntimeError) as exc:
    raise SystemExit(f'HOST_REVIEW_PATH_INVALID: {exc}')
if artifact_real.parent != reviews_real:
    raise SystemExit('HOST_REVIEW_PATH_OUTSIDE_REVIEWS')

reviews_info = reviews.lstat()
if not stat.S_ISDIR(reviews_info.st_mode) or reviews.is_symlink():
    raise SystemExit('HOST_REVIEW_DIRECTORY_INVALID')
if reviews_info.st_uid != os.geteuid() or stat.S_IMODE(reviews_info.st_mode) != 0o700:
    raise SystemExit('HOST_REVIEW_DIRECTORY_PERMISSION_INVALID')

artifact_info = artifact.lstat()
if not stat.S_ISREG(artifact_info.st_mode) or artifact.is_symlink():
    raise SystemExit('HOST_REVIEW_ARTIFACT_NOT_REGULAR')
if artifact_info.st_uid != os.geteuid() or stat.S_IMODE(artifact_info.st_mode) != 0o600:
    raise SystemExit('HOST_REVIEW_ARTIFACT_PERMISSION_INVALID')

try:
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    data = json.loads(artifact_real.read_text(encoding='utf-8'))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f'HOST_REVIEW_JSON_INVALID: {exc}')
if not isinstance(marker, dict) or not isinstance(data, dict):
    raise SystemExit('HOST_REVIEW_JSON_NOT_OBJECT')
if (
    marker.get('schemaVersion') != 2
    or not isinstance(marker.get('runId'), str)
    or not marker['runId']
    or not isinstance(marker.get('reviewDate'), str)
    or marker.get('reviewKind') not in ('semi-monthly', 'e2e')
):
    raise SystemExit('HOST_REVIEW_RUN_MARKER_INVALID')

if (
    data.get('schemaVersion') != 1
    or data.get('status') != 'pending'
    or data.get('producer') != 'host-deterministic-review-v1'
    or data.get('runId') != marker['runId']
    or data.get('reviewDate') != marker['reviewDate']
    or data.get('reviewKind') != marker['reviewKind']
    or data.get('changes') != []
):
    raise SystemExit('HOST_REVIEW_IDENTITY_OR_SAFETY_INVALID')

summary = data.get('summaryMarkdown')
if not isinstance(summary, str):
    raise SystemExit('HOST_REVIEW_SUMMARY_INVALID')
run_marker = re.compile(rf'(?m)^REVIEW_RUN_ID={re.escape(marker["runId"])}$')
kind_marker = re.compile(rf'(?m)^REVIEW_KIND={re.escape(marker["reviewKind"])}$')
host_marker = re.compile(r'(?m)^HOST_DETERMINISTIC_REVIEW=true$')
e2e_marker = re.compile(r'(?m)^E2E_TEST_ONLY=true$')
deferred_marker = re.compile(r'(?m)^DISCOVERY_DEFERRED=true$')
if (
    len(run_marker.findall(summary)) != 1
    or len(kind_marker.findall(summary)) != 1
    or len(host_marker.findall(summary)) != 1
    or (marker['reviewKind'] == 'e2e' and len(e2e_marker.findall(summary)) != 1)
    or (marker['reviewKind'] == 'semi-monthly' and len(deferred_marker.findall(summary)) != 1)
):
    raise SystemExit('HOST_REVIEW_MARKER_COUNT_INVALID')

matching_artifacts = []
for candidate in reviews.glob('*.json'):
    try:
        candidate_data = json.loads(candidate.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        continue
    candidate_summary = candidate_data.get('summaryMarkdown') if isinstance(candidate_data, dict) else None
    if (
        isinstance(candidate_data, dict)
        and (
            candidate_data.get('runId') == marker['runId']
            or (isinstance(candidate_summary, str) and run_marker.search(candidate_summary))
        )
    ):
        matching_artifacts.append(candidate.resolve())
if matching_artifacts != [artifact_real]:
    raise SystemExit('HOST_REVIEW_RUN_MARKER_NOT_UNIQUE')

print(artifact_real)
PY
}

backoffs=(0 10 30)
for attempt in 1 2 3; do
  delay="${backoffs[$((attempt - 1))]}"
  if [ "$delay" -gt 0 ]; then
    echo "$(date -Is) [review] RETRY_WAIT ${delay}s attempt=$attempt/3 runId=$runid kind=$kind" | tee -a "$LOG"
    sleep "$delay"
  fi
  echo "$(date -Is) [review] HOST_REVIEW_ATTEMPT $attempt/3 runId=$runid kind=$kind" | tee -a "$LOG"
  set +e
  host_review_output="$(python3 "$HOST_REVIEW_BIN" "$kind" --reuse 2>&1)"
  host_review_rc=$?
  set -e
  printf '%s\n' "$host_review_output" >>"$LOG"
  if [ "$host_review_rc" -ne 0 ]; then
    echo "$(date -Is) [review] HOST_REVIEW_NETWORK_OR_EXEC_FAIL attempt=$attempt/3 rc=$host_review_rc" | tee -a "$LOG"
    continue
  fi

  mapfile -t host_review_paths < <(printf '%s\n' "$host_review_output" | sed -n 's/^HOST_REVIEW_PATH=//p')
  if [ "${#host_review_paths[@]}" -ne 1 ] || [ -z "${host_review_paths[0]}" ]; then
    echo "$(date -Is) [review] HOST_REVIEW_PATH_CONTRACT_INVALID count=${#host_review_paths[@]}" | tee -a "$LOG"
    exit 65
  fi
  HOST_REVIEW_PATH="${host_review_paths[0]}"
  set +e
  verified_review="$(validate_host_review "$HOST_REVIEW_PATH" 2>>"$LOG")"
  verify_rc=$?
  set -e
  if [ "$verify_rc" -ne 0 ] || [ -z "$verified_review" ]; then
    echo "$(date -Is) [review] HOST_REVIEW_ARTIFACT_VERIFY_FAIL rc=$verify_rc" | tee -a "$LOG"
    exit 65
  fi
  HOST_REVIEW_PATH="$verified_review"
  echo "$(date -Is) [review] HOST_REVIEW_SUCCESS attempt=$attempt/3 runId=$runid reviewDate=$reviewdate kind=$kind file=$(basename -- "$HOST_REVIEW_PATH")" | tee -a "$LOG"
  exit 0
done

echo "$(date -Is) [review] CANCEL current review after 3 failed network/execution attempts runId=$runid kind=$kind" | tee -a "$LOG"
exit 1
