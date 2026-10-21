#!/usr/bin/env bash
set -euo pipefail

edition="${1:-}"
case "$edition" in
  morning|afternoon) ;;
  *) echo 'Usage: rss-v53-daily-catchup.sh morning|afternoon' >&2; exit 64 ;;
esac

ROOT="${RSS_INTELLIGENCE_ROOT:-/home/ubuntu/.openclaw/rss-intelligence}"
STATE="${RSS_DAILY_SUCCESS_STATE:-$ROOT/daily-state-v52/last-success.json}"
RUNNER="${RSS_DAILY_RUNNER_BIN:-/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/rss-v52-daily-retry-runner.sh}"
DAILY_LOCK="${RSS_DAILY_GLOBAL_LOCK:-$ROOT/v52-daily-global.lock}"
CATCHUP_LOCK="${RSS_CATCHUP_LOCK:-$ROOT/v53-catchup-${edition}.lock}"
LOCK_WAIT="${RSS_CATCHUP_DAILY_LOCK_WAIT_SECONDS:-10800}"

mkdir -p -- "$ROOT"
chmod 700 -- "$ROOT" 2>/dev/null || true

# One catch-up process per edition.  The normal runner has its own global lock;
# this outer lock prevents duplicate catch-up instances from being queued.
exec 7>"$CATCHUP_LOCK"
if ! flock -n 7; then
  printf 'CATCHUP_SKIP edition=%s reason=catchup-already-running\n' "$edition"
  exit 0
fi

today_shanghai="$(TZ=Asia/Shanghai date +%F)"

already_succeeded() {
  python3 - "$STATE" "$edition" "$today_shanghai" <<'PY' >/dev/null 2>&1
import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

path = Path(sys.argv[1])
edition = sys.argv[2]
today = sys.argv[3]

try:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or not (1 <= info.st_size <= 65536):
        raise ValueError("unsafe state file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state is not an object")
    if data.get("schemaVersion") != 2 or data.get("status") != "ok":
        raise ValueError("state identity mismatch")
    if data.get("edition") != edition or data.get("reportDateKey") != today:
        raise ValueError("different edition or day")
    if not str(data.get("candidateRunId") or "").strip() or not str(data.get("stageId") or "").strip():
        raise ValueError("success identity missing")
    published = datetime.fromisoformat(str(data.get("publishedAt") or "").replace("Z", "+00:00"))
    if published.tzinfo is None:
        raise ValueError("publishedAt has no timezone")
    if published.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() != today:
        raise ValueError("publishedAt is not today")
except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit(1)
PY
}

if already_succeeded; then
  printf 'CATCHUP_SKIP edition=%s date=%s reason=already-successful\n' "$edition" "$today_shanghai"
  exit 0
fi

if [ ! -f "$RUNNER" ] || [ ! -x "$RUNNER" ]; then
  printf 'CATCHUP_ERROR edition=%s reason=runner-not-executable\n' "$edition" >&2
  exit 66
fi

# Wait for any scheduled daily run to finish, then re-read the authoritative
# success state.  This avoids queueing a duplicate when the normal run merely
# needed a few extra minutes.
exec 8>"$DAILY_LOCK"
if ! flock -w "$LOCK_WAIT" 8; then
  printf 'CATCHUP_ERROR edition=%s reason=daily-lock-timeout\n' "$edition" >&2
  exit 75
fi

if already_succeeded; then
  flock -u 8 || true
  printf 'CATCHUP_SKIP edition=%s date=%s reason=completed-while-waiting\n' "$edition" "$today_shanghai"
  exit 0
fi

flock -u 8 || true
exec 8>&-
printf 'CATCHUP_RUN edition=%s date=%s\n' "$edition" "$today_shanghai"
exec "$RUNNER" "$edition"
