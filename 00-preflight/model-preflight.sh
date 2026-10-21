#!/usr/bin/env bash
set -euo pipefail
PKG=/home/ubuntu/rss-intelligence-v5.2
OPENCLAW="$(command -v openclaw)"
EXTRACT="$PKG/00-preflight/extract-agent-text.py"
run_one(){
  local label="$1" model="$2" tmp out err text rc
  tmp="$(mktemp)"; err="$(mktemp)"
  set +e
  timeout --signal=TERM --kill-after=15s 90s "$OPENCLAW" agent \
    --agent main --session-key "rss-v52-model-${label}-$(date +%s%N)" \
    --model "$model" --timeout 60 --json --message '只回复 OK' >"$tmp" 2>"$err"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    text="$(python3 "$EXTRACT" <"$tmp" 2>/dev/null || true)"
    if [ "$(printf '%s' "$text" | tr -d '\r\n ' )" = "OK" ]; then
      echo "PASS $label model=$model"; rm -f "$tmp" "$err"; return 0
    fi
  fi
  echo "FAIL $label model=$model rc=$rc"
  sed -n '1,60p' "$err"
  rm -f "$tmp" "$err"
  return 1
}
fail=0
run_one FLASH opencode-go/deepseek-v4-flash || fail=1
run_one PRO opencode-go/deepseek-v4-pro || fail=1
[ "$fail" -eq 0 ] && { echo MODEL_PREFLIGHT_OK; exit 0; }
echo MODEL_PREFLIGHT_FAILED; exit 1
