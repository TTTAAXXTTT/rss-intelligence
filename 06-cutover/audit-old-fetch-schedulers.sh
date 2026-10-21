#!/usr/bin/env bash
set -euo pipefail
echo '=== USER TIMERS matching FreshRSS/RSS/actualize ==='
systemctl --user list-timers --all 2>/dev/null | grep -Ei 'fresh|rss|actual' || true
echo '=== SYSTEM TIMERS matching FreshRSS/RSS/actualize ==='
systemctl list-timers --all 2>/dev/null | grep -Ei 'fresh|rss|actual' || true
echo '=== USER UNITS matching FreshRSS/RSS/actualize ==='
systemctl --user list-unit-files 2>/dev/null | grep -Ei 'fresh|rss|actual' || true
echo '=== SYSTEM UNITS matching FreshRSS/RSS/actualize ==='
systemctl list-unit-files 2>/dev/null | grep -Ei 'fresh|rss|actual' || true
