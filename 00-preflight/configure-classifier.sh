#!/usr/bin/env bash
set -euo pipefail
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
CONFIG="$($OPENCLAW_BIN config file)"
CONFIG="${CONFIG/#\~/$HOME}"
AGENT_ID=rss-v52-classifier
WORKSPACE="${RSS_CLASSIFIER_WORKSPACE:-/home/ubuntu/.openclaw/workspace-rss-v52-classifier}"
if ! "$OPENCLAW_BIN" agents list | grep -q "^- ${AGENT_ID}"; then
  "$OPENCLAW_BIN" agents add "$AGENT_ID" --non-interactive --workspace "$WORKSPACE" \
    --model "${RSS_DAILY_FLASH_MODEL:-opencode-go/deepseek-v4-flash}" --json >/dev/null
fi
index="$(jq -r --arg id "$AGENT_ID" '.agents.list|to_entries[]|select(.value.id==$id)|.key' "$CONFIG")"
[[ "$index" =~ ^[0-9]+$ ]] || { echo CLASSIFIER_AGENT_INDEX_INVALID >&2; exit 1; }
"$OPENCLAW_BIN" config set "agents.list[$index].skills" '[]' --strict-json >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].sandbox.workspaceAccess" none >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].sandbox.scope" agent >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].tools.allow" '["session_status"]' --strict-json >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].tools.deny" '["exec","write","edit","read","web_fetch","web_search","browser","rss_daily_finalize_save_v52","freshrss_list_subscriptions","freshrss_recent_articles","rss_report_save","rss_review_save"]' --strict-json >/dev/null
"$OPENCLAW_BIN" config validate >/dev/null
echo CLASSIFIER_CONFIGURED_RESTART_GATEWAY
