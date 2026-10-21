#!/usr/bin/env bash
set -euo pipefail

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
root="/home/ubuntu/rss-intelligence-backups/source-relay-final-${stamp}"
relay_was_active=0
backup_complete=0

relay_port_is_listening() {
  ss -H -ltn 'sport = :8091' 2>/dev/null | grep -q .
}

wait_for_relay_port_gone() {
  for _ in $(seq 1 60); do
    if ! relay_port_is_listening; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_relay_ready() {
  for _ in $(seq 1 75); do
    if systemctl is-active --quiet rss-source-relay.service \
      && relay_port_is_listening \
      && curl --fail --silent --connect-timeout 2 --max-time 5 \
        --output /dev/null http://127.0.0.1:8091/healthz; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restore_relay_state() {
  if [ "$relay_was_active" -eq 1 ]; then
    if ! systemctl is-active --quiet rss-source-relay.service; then
      systemctl start rss-source-relay.service || return 1
    fi
    wait_for_relay_ready || return 1
  fi
}

finish() {
  rc=$?
  set +e
  trap - EXIT INT TERM HUP

  restore_rc=0
  restore_relay_state || restore_rc=$?
  if [ "$restore_rc" -ne 0 ]; then
    echo 'the original active rss-source-relay state could not be restored' >&2
    if [ "$rc" -eq 0 ]; then
      rc="$restore_rc"
    fi
  fi

  if [ "$rc" -eq 0 ] && [ "$backup_complete" -eq 1 ]; then
    echo '=== AWS relay final backup complete ==='
    printf 'backup=%s\n' "$root"
    cat "$root/INVENTORY.txt"
    cat "$root/SHA256SUMS"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

systemctl is-active --quiet rss-source-relay.service
relay_was_active=1
systemctl is-enabled --quiet rss-source-relay.service
apache2ctl configtest
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
  --output /dev/null http://127.0.0.1:8091/healthz

install -d -m 0700 -o ubuntu -g ubuntu "$root"

systemctl stop rss-source-relay.service
if ! wait_for_relay_port_gone; then
  echo 'rss-source-relay port 8091 remained open after service stop' >&2
  exit 1
fi
if systemctl is-active --quiet rss-source-relay.service; then
  echo 'rss-source-relay remained active after service stop' >&2
  exit 1
fi

tar --numeric-owner --acls --xattrs -czf "$root/source-relay.tar.gz" \
  /usr/local/libexec/rss-source-relay.py \
  /etc/systemd/system/rss-source-relay.service \
  /etc/apache2/rss-example-v51/20-source-relay.conf \
  /var/lib/rss-source-relay

{
  echo "createdUtc=${stamp}"
  echo "relayWasActive=${relay_was_active}"
  systemctl show rss-source-relay.service \
    -p LoadState -p UnitFileState -p ActiveState -p SubState -p FragmentPath
  find /var/lib/rss-source-relay -maxdepth 1 -type f -printf '%f %s bytes\n' | sort
} >"$root/MANIFEST.txt"

gzip -t "$root/source-relay.tar.gz"
tar -tzf "$root/source-relay.tar.gz" >/dev/null
(
  cd "$root"
  sha256sum source-relay.tar.gz MANIFEST.txt >SHA256SUMS
  sha256sum -c SHA256SUMS
  find . -maxdepth 1 -type f ! -name INVENTORY.txt ! -name .INVENTORY.txt.tmp \
    -printf '%f %s bytes\n' | sort >.INVENTORY.txt.tmp
  mv -f -- .INVENTORY.txt.tmp INVENTORY.txt
)
chmod 0600 "$root"/*
chown -R ubuntu:ubuntu "$root"
backup_complete=1
