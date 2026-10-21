#!/usr/bin/env bash
set -euo pipefail

staged_updater='/tmp/rss-dns-override-update.py'
updater_hash='7347c9d226590ebe7a5389ebd6e3539ca3e7bd11bd60e52202a298eafbe77244'
dns_unit='/etc/systemd/system/rss-dnsproxy.service'
update_unit='/etc/systemd/system/rss-dns-override-update.service'
update_timer='/etc/systemd/system/rss-dns-override-update.timer'
resolved_dropin='/etc/systemd/resolved.conf.d/50-rss-dnsproxy.conf'
updater='/usr/local/sbin/rss-dns-override-update'
state_dir='/var/lib/rss-dns-overrides'
hosts_file="${state_dir}/hosts"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/tencent-dns-final-${stamp}"
committed=0
backup_ready=0
dns_enabled_before='not-found'
dns_active_before='inactive'
timer_enabled_before='not-found'
timer_active_before='inactive'
state_dir_existed=0

files=("$dns_unit" "$update_unit" "$update_timer" "$resolved_dropin" "$updater" "$hosts_file")

backup_one() {
  local path="$1" key
  key="$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
  if [ -e "$path" ] || [ -L "$path" ]; then
    cp -a -- "$path" "$backup/$key"
  else
    : >"$backup/$key.missing"
  fi
}

restore_one() {
  local path="$1" key
  key="$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
  if [ -e "$backup/$key" ] || [ -L "$backup/$key" ]; then
    mkdir -p -- "$(dirname "$path")"
    rm -f -- "$path"
    cp -a -- "$backup/$key" "$path"
  elif [ -e "$backup/$key.missing" ]; then
    rm -f -- "$path"
  fi
}

restore_enablement() {
  local unit="$1" previous="$2"
  systemctl disable "$unit" >/dev/null 2>&1 || true
  case "$previous" in
    enabled) systemctl enable "$unit" >/dev/null 2>&1 || true ;;
    enabled-runtime) systemctl enable --runtime "$unit" >/dev/null 2>&1 || true ;;
  esac
}

restore_activity() {
  local unit="$1" previous="$2"
  case "$previous" in
    active|activating|reloading) systemctl restart "$unit" >/dev/null 2>&1 || true ;;
    *) systemctl stop "$unit" >/dev/null 2>&1 || true ;;
  esac
}

finish() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$committed" -eq 0 ] && [ "$backup_ready" -eq 1 ]; then
    echo 'DNS configuration failed; restoring the previous resolver state' >&2
    systemctl stop rss-dns-override-update.timer rss-dns-override-update.service rss-dnsproxy.service >/dev/null 2>&1 || true
    for path in "${files[@]}"; do restore_one "$path" || true; done
    rm -f -- \
      /etc/systemd/system/multi-user.target.wants/rss-dnsproxy.service \
      /etc/systemd/system/timers.target.wants/rss-dns-override-update.timer
    systemctl daemon-reload || true
    restore_enablement rss-dnsproxy.service "$dns_enabled_before"
    restore_enablement rss-dns-override-update.timer "$timer_enabled_before"
    restore_activity rss-dnsproxy.service "$dns_active_before"
    systemctl restart systemd-resolved || true
    resolvectl flush-caches || true
    restore_activity rss-dns-override-update.timer "$timer_active_before"
    if [ "$state_dir_existed" -eq 0 ] && [ -d "$state_dir" ] && [ ! -L "$state_dir" ]; then
      find "$state_dir" -mindepth 1 -maxdepth 1 -type f -delete || true
      rmdir -- "$state_dir" >/dev/null 2>&1 || true
    fi
  fi
  trap - EXIT
  exit "$rc"
}

if [ ! -x /usr/local/sbin/dnsproxy ] || [ "$(/usr/local/sbin/dnsproxy --version)" != 'dnsproxy version v0.84.1' ]; then
  echo 'verified dnsproxy v0.84.1 is not installed' >&2
  exit 1
fi
if [ ! -f "$staged_updater" ]; then
  echo "missing staged updater: $staged_updater" >&2
  exit 1
fi
actual_updater_hash="$(sha256sum "$staged_updater" | awk '{print $1}')"
if [ "$actual_updater_hash" != "$updater_hash" ]; then
  echo "updater checksum mismatch: $actual_updater_hash" >&2
  exit 1
fi
if [ -L "$state_dir" ] || { [ -e "$state_dir" ] && [ ! -d "$state_dir" ]; }; then
  echo "unsafe DNS override state path: $state_dir" >&2
  exit 1
fi
if [ -d "$state_dir" ]; then state_dir_existed=1; fi

install -d -m 0750 -o ubuntu -g ubuntu "$backup"
for path in "${files[@]}"; do backup_one "$path"; done
dns_enabled_before="$(systemctl is-enabled rss-dnsproxy.service 2>/dev/null || true)"
dns_active_before="$(systemctl is-active rss-dnsproxy.service 2>/dev/null || true)"
timer_enabled_before="$(systemctl is-enabled rss-dns-override-update.timer 2>/dev/null || true)"
timer_active_before="$(systemctl is-active rss-dns-override-update.timer 2>/dev/null || true)"
printf 'dns_enabled=%s\ndns_active=%s\ntimer_enabled=%s\ntimer_active=%s\n' \
  "$dns_enabled_before" "$dns_active_before" "$timer_enabled_before" "$timer_active_before" \
  >"$backup/unit-state-before.txt"
resolvectl status --no-pager >"$backup/resolvectl-before.txt"
systemctl status rss-dnsproxy.service --no-pager >"$backup/dnsproxy-before.txt" 2>&1 || true
backup_ready=1
trap finish EXIT

install -d -m 0755 "$state_dir"
install -m 0755 "$staged_updater" "$updater"
"$updater" --output "$hosts_file"

cat >"$dns_unit" <<'DNSUNIT'
[Unit]
Description=Local validated DNS resolver for the RSS service
Documentation=https://github.com/AdguardTeam/dnsproxy
Wants=network-online.target
After=network-online.target
Before=docker.service

[Service]
Type=simple
User=dnsproxy
Group=dnsproxy
ExecStart=/usr/local/sbin/dnsproxy --listen=127.0.0.2 --port=53 --upstream=183.60.83.19:53 --upstream=183.60.82.98:53 --upstream-mode=parallel --hosts-file-enabled --hosts-files=/var/lib/rss-dns-overrides/hosts --cache --cache-size=4194304 --cache-min-ttl=30 --cache-max-ttl=86400 --cache-optimistic --pending-requests-enabled --timeout=8s --ipv6-disabled --refuse-any
Restart=on-failure
RestartSec=3s
TimeoutStopSec=15s
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHome=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
DNSUNIT

cat >"$update_unit" <<'UPDATEUNIT'
[Unit]
Description=Refresh validated DNS overrides for RSS sources
Wants=network-online.target
After=network-online.target rss-dnsproxy.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rss-dns-override-update
ExecStartPost=/usr/bin/systemctl restart rss-dnsproxy.service
User=root
Group=root
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/rss-dns-overrides
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
SystemCallArchitectures=native
UPDATEUNIT

cat >"$update_timer" <<'UPDATETIMER'
[Unit]
Description=Refresh RSS DNS overrides before scheduled source pulls

[Timer]
OnCalendar=*-*-* 06:35:00 Asia/Shanghai
OnCalendar=*-*-* 12:35:00 Asia/Shanghai
OnBootSec=2min
Persistent=true
AccuracySec=30s
RandomizedDelaySec=60s
Unit=rss-dns-override-update.service

[Install]
WantedBy=timers.target
UPDATETIMER

install -d -m 0755 /etc/systemd/resolved.conf.d
cat >"$resolved_dropin" <<'RESOLVED'
[Resolve]
DNS=127.0.0.2
FallbackDNS=
Domains=~.
DNSSEC=no
DNSOverTLS=no
RESOLVED

systemd-analyze verify "$dns_unit" "$update_unit" "$update_timer"
systemctl daemon-reload
systemctl enable rss-dnsproxy.service rss-dns-override-update.timer
systemctl restart rss-dnsproxy.service
for _ in $(seq 1 30); do
  if systemctl is-active --quiet rss-dnsproxy.service && ss -lntup | grep -F '127.0.0.2:53' | grep -Fq 'dnsproxy'; then break; fi
  sleep 0.2
done
systemctl is-active --quiet rss-dnsproxy.service
ss -lntup | grep -F '127.0.0.2:53' | grep -F 'dnsproxy'

systemctl restart systemd-resolved
resolvectl flush-caches
for _ in $(seq 1 30); do
  if getent ahostsv4 news.ycombinator.com >/dev/null 2>&1; then break; fi
  sleep 0.5
done

validated_addresses() {
  local host="$1"
  awk -v host="$host" '$2 == host {print $1}' "$hosts_file" | sort -u | paste -sd, -
}

assert_host_resolution() {
  local host="$1" expected addresses
  expected="$(validated_addresses "$host")"
  addresses="$(getent ahostsv4 "$host" | awk '{print $1}' | sort -u | paste -sd, -)"
  printf 'host_%s=%s\n' "$host" "$addresses"
  [ -n "$expected" ] && [ "$addresses" = "$expected" ]
}

assert_container_resolution() {
  local host="$1" expected addresses
  expected="$(validated_addresses "$host")"
  addresses="$(docker exec freshrss getent ahostsv4 "$host" | awk '{print $1}' | sort -u | paste -sd, -)"
  printf 'container_%s=%s\n' "$host" "$addresses"
  [ -n "$expected" ] && [ "$addresses" = "$expected" ]
}

{
  for host in news.ycombinator.com huggingface.co www.occ.gov wp.oecd.ai rbi.org.in; do
    assert_host_resolution "$host"
    assert_container_resolution "$host"
  done
} | tee "$backup/resolver-after.txt"

systemctl start rss-dns-override-update.service
systemctl is-active --quiet rss-dnsproxy.service
systemctl is-enabled --quiet rss-dns-override-update.timer
systemctl start rss-dns-override-update.timer

resolvectl status --no-pager >"$backup/resolvectl-after.txt"
systemctl status rss-dnsproxy.service --no-pager >"$backup/dnsproxy-after.txt"
systemctl status rss-dns-override-update.timer --no-pager >"$backup/dns-update-timer.txt"
sha256sum /usr/local/sbin/dnsproxy "$updater" "$hosts_file" "$dns_unit" "$update_unit" "$update_timer" "$resolved_dropin" >"$backup/installed-sha256.txt"
chown -R ubuntu:ubuntu "$backup"

committed=1
echo '=== validated DNS configuration installed ==='
printf 'backup=%s\n' "$backup"
systemctl is-active rss-dnsproxy.service
systemctl is-enabled rss-dnsproxy.service
systemctl is-enabled rss-dns-override-update.timer
systemctl list-timers rss-dns-override-update.timer --no-pager
resolvectl status --no-pager
