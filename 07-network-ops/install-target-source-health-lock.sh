#!/usr/bin/env bash
set -euo pipefail

dropin_dir='/home/ubuntu/.config/systemd/user/rss-v53-source-health.service.d'
dropin="${dropin_dir}/10-source-dataset-lock.conf"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/source-health-lock-${stamp}"
dropin_dir_existed=0
dropin_changed=0
tmp=''

if [ -d "$dropin_dir" ]; then
  dropin_dir_existed=1
fi

rollback_on_failure() {
  rc=$?
  set +e
  trap - EXIT INT TERM HUP
  if [ -n "$tmp" ]; then
    rm -f -- "$tmp"
  fi
  if [ "$rc" -ne 0 ] && [ "$dropin_changed" -eq 1 ]; then
    if [ -f "$backup/10-source-dataset-lock.conf" ]; then
      cp -a "$backup/10-source-dataset-lock.conf" "$dropin"
    else
      rm -f -- "$dropin"
      if [ "$dropin_dir_existed" -eq 0 ]; then
        rmdir -- "$dropin_dir" 2>/dev/null || true
      fi
    fi
    systemctl --user daemon-reload || true
    systemctl --user reset-failed rss-v53-source-health.service || true
    echo 'source-health lock installation failed; previous drop-in restored' >&2
  fi
  exit "$rc"
}
trap rollback_on_failure EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

sudo install -d -m 0750 -o ubuntu -g ubuntu "$backup"
if [ -f "$dropin" ]; then cp -a "$dropin" "$backup/10-source-dataset-lock.conf"; else : >"$backup/dropin.missing"; fi
systemctl --user cat rss-v53-source-health.service >"$backup/service-before.txt"

install -d -m 0755 "$dropin_dir"
tmp="$(mktemp "${dropin}.tmp.XXXXXX")"
cat >"$tmp" <<'UNIT'
[Unit]
After=network-online.target rss-v52-freshrss-fetch.service

[Service]
ExecStart=
ExecStart=/usr/bin/flock -w 1800 /home/ubuntu/.openclaw/rss-intelligence/v52-source-dataset.lock /home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/python /home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/source-health.py
TimeoutStartSec=55min
UNIT
chmod 0644 "$tmp"
dropin_changed=1
mv -f -- "$tmp" "$dropin"
tmp=''

systemd-analyze --user verify /home/ubuntu/.config/systemd/user/rss-v53-source-health.service
systemctl --user daemon-reload
systemctl --user reset-failed rss-v53-source-health.service
systemctl --user cat rss-v53-source-health.service >"$backup/service-after.txt"
sha256sum "$dropin" >"$backup/SHA256SUMS"

echo '=== source-health scheduling lock installed ==='
printf 'backup=%s\n' "$backup"
systemctl --user show rss-v53-source-health.service -p ExecStart -p TimeoutStartUSec -p After
trap - EXIT INT TERM HUP
