#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
CONFIG="$($OPENCLAW_BIN config file)"
CONFIG="${CONFIG/#\~/$HOME}"
AGENT_ID=rss-v53-translator
WORKSPACE="${RSS_TRANSLATOR_WORKSPACE:-/home/ubuntu/.openclaw/workspace-rss-v53-translator}"

if ! "$OPENCLAW_BIN" agents list | grep -q "^- ${AGENT_ID}"; then
  "$OPENCLAW_BIN" agents add "$AGENT_ID" --non-interactive \
    --workspace "$WORKSPACE" --model opencode-go/deepseek-v4-flash --json >/dev/null
fi

index="$(jq -r --arg id "$AGENT_ID" '.agents.list|to_entries[]|select(.value.id==$id)|.key' "$CONFIG")"
[[ "$index" =~ ^[0-9]+$ ]] || { echo 'TRANSLATOR_AGENT_INDEX_INVALID' >&2; exit 1; }
classifier_index="$(jq -r '.agents.list|to_entries[]|select(.value.id=="rss-v52-classifier")|.key' "$CONFIG")"
[[ "$classifier_index" =~ ^[0-9]+$ ]] || { echo 'CLASSIFIER_AGENT_INDEX_INVALID' >&2; exit 1; }
deny="$(jq -c ".agents.list[$classifier_index].tools.deny" "$CONFIG")"
[ "$deny" != null ] || { echo 'CLASSIFIER_AGENT_DENY_MISSING' >&2; exit 1; }

"$OPENCLAW_BIN" config set "agents.list[$index].skills" '[]' --strict-json >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].sandbox.workspaceAccess" none >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].sandbox.scope" agent >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].tools.allow" '["session_status"]' --strict-json >/dev/null
"$OPENCLAW_BIN" config set "agents.list[$index].tools.deny" "$deny" --strict-json >/dev/null
"$OPENCLAW_BIN" config validate >/dev/null

jq -e --arg id "$AGENT_ID" '
  .agents.list[] | select(.id==$id) |
  .skills==[] and .sandbox.workspaceAccess=="none" and .sandbox.scope=="agent" and
  .tools.allow==["session_status"] and
  (.tools.deny|index("web_fetch")!=null) and
  (.tools.deny|index("exec")!=null) and
  (.tools.deny|index("rss_daily_finalize_save_v52")!=null)
' "$CONFIG" >/dev/null
echo TRANSLATOR_AGENT_CONFIGURED
