#!/usr/bin/env bash
set -euo pipefail

timers=(
  rss-v52-freshrss-fetch.timer
  rss-v52-morning.timer
  rss-v52-afternoon.timer
  rss-v52-review.timer
  rss-v53-source-health.timer
  rss-v53-translation-cleanup.timer
  rss-v53-daily-catchup-morning.timer
  rss-v53-daily-catchup-afternoon.timer
)
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/aws-core-paused-${stamp}"

sudo install -d -m 0750 -o ubuntu -g ubuntu "$backup"
{
  date --iso-8601=seconds
  systemctl --user list-timers --all --no-pager
  for timer in "${timers[@]}"; do
    printf '%s enabled=%s active=%s\n' "$timer" \
      "$(systemctl --user is-enabled "$timer" 2>/dev/null || true)" \
      "$(systemctl --user is-active "$timer" 2>/dev/null || true)"
  done
} >"$backup/before.txt"

for service in rss-v52-freshrss-fetch.service rss-v52-morning.service rss-v52-afternoon.service; do
  state="$(systemctl --user is-active "$service" 2>/dev/null || true)"
  if [ "$state" = 'active' ] || [ "$state" = 'activating' ]; then
    echo "refusing cutover pause while ${service} is ${state}" >&2
    exit 1
  fi
done

systemctl --user disable --now "${timers[@]}"
for timer in "${timers[@]}"; do
  [ "$(systemctl --user is-enabled "$timer" 2>/dev/null || true)" = 'disabled' ]
  [ "$(systemctl --user is-active "$timer" 2>/dev/null || true)" = 'inactive' ]
done

cat >"$backup/restore-aws-rss-timers.sh" <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail
systemctl --user enable --now \
  rss-v52-freshrss-fetch.timer \
  rss-v52-morning.timer \
  rss-v52-afternoon.timer \
  rss-v52-review.timer \
  rss-v53-source-health.timer \
  rss-v53-translation-cleanup.timer \
  rss-v53-daily-catchup-morning.timer \
  rss-v53-daily-catchup-afternoon.timer
systemctl --user list-timers --all --no-pager
RESTORE
chmod 0700 "$backup/restore-aws-rss-timers.sh"

{
  date --iso-8601=seconds
  systemctl --user list-timers --all --no-pager
  for timer in "${timers[@]}"; do
    printf '%s enabled=%s active=%s\n' "$timer" \
      "$(systemctl --user is-enabled "$timer" 2>/dev/null || true)" \
      "$(systemctl --user is-active "$timer" 2>/dev/null || true)"
  done
  printf 'source_relay_system_service='; systemctl is-active rss-source-relay.service
} >"$backup/after.txt"
sha256sum "$backup/before.txt" "$backup/after.txt" "$backup/restore-aws-rss-timers.sh" >"$backup/SHA256SUMS"

echo '=== AWS core schedulers paused ==='
printf 'backup=%s\n' "$backup"
grep '^rss-' "$backup/after.txt"
grep '^source_relay' "$backup/after.txt"
