#!/usr/bin/env bash
set -euo pipefail
OPENCLAW="${OPENCLAW_BIN:-$(command -v openclaw)}"
systemctl --user disable --now rss-v52-freshrss-fetch.timer rss-v52-morning.timer rss-v52-afternoon.timer rss-v52-review.timer >/dev/null 2>&1 || true
systemctl --user disable --now rss-v51-freshrss-fetch.timer rss-v51-morning.timer rss-v51-afternoon.timer rss-v51-review.timer >/dev/null 2>&1 || true
sudo systemctl enable --now freshrss-actualize.timer
"$OPENCLAW" cron enable 5b758d06-5857-5070-abd6-79ffb622e472
"$OPENCLAW" cron enable 8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634
"$OPENCLAW" cron enable f57c34f5-4333-5156-9efc-d60ec0547010
echo V52_MANUAL_ROLLBACK_OK
