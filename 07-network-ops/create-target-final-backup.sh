#!/usr/bin/env bash
set -euo pipefail

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
root="/home/ubuntu/rss-intelligence-backups/tencent-final-${stamp}"
container_stopped=0
cleanup_timer_stopped=0
source_dataset_lock='/home/ubuntu/.openclaw/rss-intelligence/v52-source-dataset.lock'
source_dataset_lock_fd=''
source_dataset_lock_held=0

release_source_dataset_lock() {
  if [ "$source_dataset_lock_held" -eq 1 ]; then
    flock -u "$source_dataset_lock_fd" || true
    exec {source_dataset_lock_fd}>&-
    source_dataset_lock_held=0
  fi
}

finish() {
  rc=$?
  set +e
  if [ "$container_stopped" -eq 1 ]; then
    docker start freshrss >/dev/null || true
  fi
  if [ "$cleanup_timer_stopped" -eq 1 ]; then
    sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start rss-v53-translation-cleanup.timer || true
  fi
  release_source_dataset_lock
  trap - EXIT
  exit "$rc"
}
trap finish EXIT

if [ ! -e "$source_dataset_lock" ]; then
  install -d -m 0700 -o ubuntu -g ubuntu "$(dirname "$source_dataset_lock")"
  : >>"$source_dataset_lock"
  chown ubuntu:ubuntu "$source_dataset_lock"
  chmod 0600 "$source_dataset_lock"
fi
exec {source_dataset_lock_fd}<>"$source_dataset_lock"
if ! flock -w 1800 "$source_dataset_lock_fd"; then
  echo 'timed out waiting for source dataset lock' >&2
  exit 1
fi
source_dataset_lock_held=1

for service in rss-v52-morning.service rss-v52-afternoon.service rss-v52-freshrss-fetch.service; do
  state="$(sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active "$service" 2>/dev/null || true)"
  if [ "$state" = 'active' ] || [ "$state" = 'activating' ]; then
    echo "refusing backup while ${service} is ${state}" >&2
    exit 1
  fi
done

cleanup_timer_state="$(sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active rss-v53-translation-cleanup.timer 2>/dev/null || true)"
if [ "$cleanup_timer_state" = 'active' ] || [ "$cleanup_timer_state" = 'activating' ]; then
  cleanup_timer_stopped=1
  sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user stop rss-v53-translation-cleanup.timer
fi
for _ in $(seq 1 60); do
  cleanup_state="$(sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active rss-v53-translation-cleanup.service 2>/dev/null || true)"
  if [ "$cleanup_state" = 'inactive' ] || [ "$cleanup_state" = 'failed' ]; then break; fi
  sleep 1
done
if [ "$(sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active rss-v53-translation-cleanup.service 2>/dev/null || true)" = 'active' ]; then
  echo 'translation cleanup did not become idle for backup' >&2
  exit 1
fi

install -d -m 0700 -o ubuntu -g ubuntu "$root"

{
  echo "createdUtc=${stamp}"
  uname -a
  lsb_release -ds 2>/dev/null || true
  docker --version
  docker image inspect freshrss/freshrss:1.29.1 --format 'imageId={{.Id}} repoDigests={{json .RepoDigests}} size={{.Size}}'
  df -h /
  sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user list-timers --all --no-pager
  systemctl is-active docker apache2 rss-dnsproxy || true
  sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active openclaw-gateway.service || true
} >"$root/MANIFEST.txt"

if [ "$(docker inspect freshrss --format '{{.State.Running}}' 2>/dev/null || true)" != 'true' ]; then
  echo 'refusing backup while FreshRSS container is not running' >&2
  exit 1
fi
container_stopped=1
docker stop --time 30 freshrss >/dev/null
tar --numeric-owner --acls --xattrs \
  -C /var/lib/docker/volumes \
  -czf "$root/freshrss-volumes.tar.gz" \
  freshrss_data/_data freshrss_extensions/_data
docker start freshrss >/dev/null
container_stopped=0
for _ in $(seq 1 75); do
  if [ "$(docker inspect freshrss --format '{{.State.Health.Status}}' 2>/dev/null || true)" = 'healthy' ]; then break; fi
  sleep 1
done
if [ "$(docker inspect freshrss --format '{{.State.Health.Status}}')" != 'healthy' ]; then
  echo 'FreshRSS did not return to healthy state after volume snapshot' >&2
  exit 1
fi

tar --numeric-owner --acls --xattrs -czf "$root/rss-runtime.tar.gz" \
  /home/ubuntu/rss-intelligence-v5.2 \
  /home/ubuntu/.openclaw/rss-intelligence \
  /home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current \
  /home/ubuntu/.config/systemd/user \
  /home/ubuntu/.local/bin/rss-v52-daily-retry-runner.sh \
  /home/ubuntu/.local/bin/rss-v52-freshrss-fetch-retry.sh \
  /home/ubuntu/.local/bin/rss-v52-review-retry-runner.sh \
  /home/ubuntu/.openclaw/.env \
  /home/ubuntu/.openclaw/openclaw.json \
  /var/www/rss-intelligence

tar --numeric-owner --acls --xattrs -czf "$root/system-config.tar.gz" \
  /opt/freshrss/compose.yaml \
  /etc/apache2/sites-available/rss.example.org.conf \
  /etc/systemd/system/rss-dnsproxy.service \
  /etc/systemd/system/rss-dns-override-update.service \
  /etc/systemd/system/rss-dns-override-update.timer \
  /etc/systemd/resolved.conf.d/50-rss-dnsproxy.conf \
  /usr/local/sbin/dnsproxy \
  /usr/local/sbin/rss-dns-override-update \
  /var/lib/rss-dns-overrides/hosts

docker save freshrss/freshrss:1.29.1 | gzip -1 >"$root/freshrss-image.tar.gz"

for archive in "$root/freshrss-volumes.tar.gz" "$root/rss-runtime.tar.gz" "$root/system-config.tar.gz"; do
  gzip -t "$archive"
  tar -tzf "$archive" >/dev/null
done
gzip -t "$root/freshrss-image.tar.gz"

(
  cd "$root"
  sha256sum freshrss-volumes.tar.gz rss-runtime.tar.gz system-config.tar.gz freshrss-image.tar.gz MANIFEST.txt >SHA256SUMS
  sha256sum -c SHA256SUMS
  find . -maxdepth 1 -type f ! -name INVENTORY.txt ! -name .INVENTORY.txt.tmp \
    -printf '%f %s bytes\n' | sort >.INVENTORY.txt.tmp
  mv -f -- .INVENTORY.txt.tmp INVENTORY.txt
)
chmod 0600 "$root"/*
chown -R ubuntu:ubuntu "$root"

if [ "$cleanup_timer_stopped" -eq 1 ]; then
  sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start rss-v53-translation-cleanup.timer
  cleanup_timer_stopped=0
fi
release_source_dataset_lock

echo '=== target final backup complete ==='
printf 'backup=%s\n' "$root"
cat "$root/INVENTORY.txt"
cat "$root/SHA256SUMS"
trap - EXIT
