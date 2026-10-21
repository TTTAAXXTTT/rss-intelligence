#!/usr/bin/env bash
set -euo pipefail

archive='/tmp/rss-dnsproxy-v0.84.1.tar.gz'
expected='977364197eddb5fa13c012f6f3fd61b2ce689419be8cbc2d828631da9da3e865'
service='/etc/systemd/system/rss-dnsproxy.service'
resolved_dropin='/etc/systemd/resolved.conf.d/50-rss-dnsproxy.conf'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/tencent-dns-${stamp}"
extract="$(mktemp -d /tmp/rss-dnsproxy-install.XXXXXX)"
switched=0
committed=0

cleanup() {
  rm -rf -- "$extract"
}

rollback() {
  rc=$?
  if [ "$committed" -eq 0 ] && [ "$switched" -eq 1 ]; then
    echo 'installation failed after resolver switch; restoring resolver configuration' >&2
    if [ -f "$backup/50-rss-dnsproxy.conf" ]; then
      install -D -m 0644 "$backup/50-rss-dnsproxy.conf" "$resolved_dropin"
    else
      rm -f -- "$resolved_dropin"
    fi
    systemctl restart systemd-resolved || true
    resolvectl revert eth0 || true
  fi
  cleanup
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

if [ ! -f "$archive" ]; then
  echo "missing staged archive: $archive" >&2
  exit 1
fi
actual="$(sha256sum "$archive" | awk '{print $1}')"
if [ "$actual" != "$expected" ]; then
  echo "checksum mismatch: $actual" >&2
  exit 1
fi
if tar -tzf "$archive" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo 'unsafe archive member' >&2
  exit 1
fi
tar -xzf "$archive" -C "$extract"
binary="$(find "$extract" -type f -name dnsproxy -perm -u+x -print -quit)"
if [ -z "$binary" ]; then
  echo 'dnsproxy binary missing' >&2
  exit 1
fi
if [ "$("$binary" --version)" != 'dnsproxy version v0.84.1' ]; then
  echo 'unexpected dnsproxy version' >&2
  exit 1
fi

install -d -m 0750 -o ubuntu -g ubuntu "$backup"
if [ -f "$service" ]; then cp -a "$service" "$backup/rss-dnsproxy.service"; fi
if [ -f "$resolved_dropin" ]; then cp -a "$resolved_dropin" "$backup/50-rss-dnsproxy.conf"; fi
resolvectl status --no-pager >"$backup/resolvectl-before.txt"
readlink -f /etc/resolv.conf >"$backup/resolv-conf-link.txt"
cp -a /run/systemd/resolve/resolv.conf "$backup/resolv.conf.before"

if ! getent passwd dnsproxy >/dev/null; then
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin dnsproxy
fi
install -m 0755 "$binary" /usr/local/sbin/dnsproxy

cat >"$service" <<'UNIT'
[Unit]
Description=Local encrypted DNS proxy for the RSS service
Documentation=https://github.com/AdguardTeam/dnsproxy
Wants=network-online.target
After=network-online.target
Before=docker.service

[Service]
Type=simple
User=dnsproxy
Group=dnsproxy
ExecStart=/usr/local/sbin/dnsproxy --listen=127.0.0.2 --port=53 --upstream=https://doh.pub/dns-query --bootstrap=183.60.83.19:53 --bootstrap=183.60.82.98:53 --cache --cache-size=4194304 --cache-min-ttl=30 --cache-max-ttl=86400 --cache-optimistic --pending-requests-enabled --timeout=10s --ipv6-disabled --refuse-any
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
UNIT

systemd-analyze verify "$service"
systemctl daemon-reload
systemctl enable rss-dnsproxy.service
systemctl restart rss-dnsproxy.service
for _ in $(seq 1 20); do
  if systemctl is-active --quiet rss-dnsproxy.service && ss -lntup | grep -F '127.0.0.2:53' | grep -Fq 'dnsproxy'; then break; fi
  sleep 1
done
systemctl is-active --quiet rss-dnsproxy.service
ss -lntup | grep -F '127.0.0.2:53' | grep -F 'dnsproxy'

install -d -m 0755 /etc/systemd/resolved.conf.d
cat >"$resolved_dropin" <<'RESOLVED'
[Resolve]
DNS=127.0.0.2
FallbackDNS=
Domains=~.
DNSSEC=no
DNSOverTLS=no
RESOLVED

switched=1
systemctl restart systemd-resolved
resolvectl flush-caches

for _ in $(seq 1 30); do
  if getent ahostsv4 news.ycombinator.com >/dev/null 2>&1; then break; fi
  sleep 1
done

news="$(getent ahostsv4 news.ycombinator.com | awk '{print $1}' | sort -u | paste -sd, -)"
hugging="$(getent ahostsv4 huggingface.co | awk '{print $1}' | sort -u | paste -sd, -)"
occ="$(getent ahostsv4 www.occ.gov | awk '{print $1}' | sort -u | paste -sd, -)"
oecd="$(getent ahostsv4 wp.oecd.ai | awk '{print $1}' | sort -u | paste -sd, -)"
rbi="$(getent ahostsv4 rbi.org.in | awk '{print $1}' | sort -u | paste -sd, -)"

printf 'news=%s\nhuggingface=%s\nocc=%s\noecd=%s\nrbi=%s\n' "$news" "$hugging" "$occ" "$oecd" "$rbi" | tee "$backup/resolver-after.txt"
case "$news" in *209.216.230.207*) ;; *) echo 'unexpected Hacker News DNS result' >&2; exit 1;; esac
case "$hugging" in *104.244.43.57*) echo 'poisoned Hugging Face result remains' >&2; exit 1;; '') echo 'empty Hugging Face DNS result' >&2; exit 1;; esac
case "$occ" in *199.83.40.54*) ;; *) echo 'unexpected OCC DNS result' >&2; exit 1;; esac
case "$oecd" in *82.196.10.49*) ;; *) echo 'unexpected OECD DNS result' >&2; exit 1;; esac
case "$rbi" in *150.171.110.*) ;; *) echo 'unexpected RBI DNS result' >&2; exit 1;; esac

# Docker's embedded resolver forwards through systemd-resolved.  Verify the
# running FreshRSS container sees the corrected results too.
docker_news="$(docker exec freshrss getent ahostsv4 news.ycombinator.com | awk '{print $1}' | sort -u | paste -sd, -)"
docker_hugging="$(docker exec freshrss getent ahostsv4 huggingface.co | awk '{print $1}' | sort -u | paste -sd, -)"
printf 'docker_news=%s\ndocker_huggingface=%s\n' "$docker_news" "$docker_hugging" | tee -a "$backup/resolver-after.txt"
case "$docker_news" in *209.216.230.207*) ;; *) echo 'FreshRSS still has incorrect Hacker News DNS result' >&2; exit 1;; esac
case "$docker_hugging" in *104.244.43.57*) echo 'FreshRSS still has poisoned Hugging Face DNS result' >&2; exit 1;; '') echo 'FreshRSS Hugging Face DNS result is empty' >&2; exit 1;; esac

systemctl status rss-dnsproxy.service --no-pager >"$backup/rss-dnsproxy-status.txt"
resolvectl status --no-pager >"$backup/resolvectl-after.txt"
sha256sum /usr/local/sbin/dnsproxy "$service" "$resolved_dropin" >"$backup/installed-sha256.txt"
chown -R ubuntu:ubuntu "$backup"
committed=1
trap - ERR

echo '=== dnsproxy installation complete ==='
printf 'backup=%s\n' "$backup"
systemctl is-active rss-dnsproxy.service
systemctl is-enabled rss-dnsproxy.service
resolvectl status --no-pager
