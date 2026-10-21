#!/usr/bin/env bash
set -euo pipefail

staged='/tmp/rss-source-relay.py'
expected='c56455b1638478b87a90e5849a0d7f5987239fdd2ff7338d90bb2c344d7f9889'
program='/usr/local/libexec/rss-source-relay.py'
unit='/etc/systemd/system/rss-source-relay.service'
apache_conf='/etc/apache2/rss-example-v51/20-source-relay.conf'
state_dir='/var/lib/rss-source-relay'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/source-relay-${stamp}"
committed=0
backup_ready=0
test_dir=''
relay_enabled_before='not-found'
relay_active_before='inactive'
apache_active_before='inactive'
relay_user_existed=0
relay_group_existed=0
state_dir_existed=0
files=("$program" "$unit" "$apache_conf")

backup_one() {
  local path="$1" key
  key="$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
  if [ -e "$path" ] || [ -L "$path" ]; then cp -a -- "$path" "$backup/$key"; else : >"$backup/$key.missing"; fi
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
  local service="$1" previous="$2"
  systemctl disable "$service" >/dev/null 2>&1 || true
  case "$previous" in
    enabled) systemctl enable "$service" >/dev/null 2>&1 || true ;;
    enabled-runtime) systemctl enable --runtime "$service" >/dev/null 2>&1 || true ;;
  esac
}

restore_activity() {
  local service="$1" previous="$2"
  case "$previous" in
    active|activating|reloading) systemctl restart "$service" >/dev/null 2>&1 || true ;;
    *) systemctl stop "$service" >/dev/null 2>&1 || true ;;
  esac
}

cleanup_test_dir() {
  [ -n "$test_dir" ] || return 0
  case "$test_dir" in
    /tmp/rss-source-relay-test.*) ;;
    *) echo "refusing to clean unexpected test path: $test_dir" >&2; return 1 ;;
  esac
  if [ -d "$test_dir" ] && [ ! -L "$test_dir" ]; then
    find "$test_dir" -mindepth 1 -maxdepth 1 -type f -delete
    rmdir -- "$test_dir"
  fi
  test_dir=''
}

finish() {
  rc=$?
  cleanup_test_dir || true
  if [ "$rc" -ne 0 ] && [ "$committed" -eq 0 ] && [ "$backup_ready" -eq 1 ]; then
    echo 'source relay deployment failed; restoring previous files' >&2
    systemctl stop rss-source-relay.service >/dev/null 2>&1 || true
    for path in "${files[@]}"; do restore_one "$path" || true; done
    rm -f -- /etc/systemd/system/multi-user.target.wants/rss-source-relay.service
    systemctl daemon-reload || true
    restore_enablement rss-source-relay.service "$relay_enabled_before"
    restore_activity rss-source-relay.service "$relay_active_before"
    if [ "$apache_active_before" = 'active' ] || [ "$apache_active_before" = 'activating' ] || [ "$apache_active_before" = 'reloading' ]; then
      apache2ctl configtest >/dev/null 2>&1 && systemctl reload apache2 || true
    fi
    if [ "$state_dir_existed" -eq 0 ] && [ -d "$state_dir" ] && [ ! -L "$state_dir" ]; then
      find "$state_dir" -xdev -depth -mindepth 1 -delete || true
      rmdir -- "$state_dir" >/dev/null 2>&1 || true
    fi
    if [ "$relay_user_existed" -eq 0 ] && getent passwd rss-relay >/dev/null; then
      userdel rss-relay >/dev/null 2>&1 || true
    fi
    if [ "$relay_group_existed" -eq 0 ] && getent group rss-relay >/dev/null; then
      groupdel rss-relay >/dev/null 2>&1 || true
    fi
  fi
  trap - EXIT
  exit "$rc"
}
trap finish EXIT

if [ ! -f "$staged" ]; then echo "missing staged relay: $staged" >&2; exit 1; fi
actual="$(sha256sum "$staged" | awk '{print $1}')"
if [ "$actual" != "$expected" ]; then echo "relay checksum mismatch: $actual" >&2; exit 1; fi
test_dir="$(mktemp -d /tmp/rss-source-relay-test.XXXXXXXX)"
chmod 0700 "$test_dir"
python3 - "$staged" "$test_dir/rss-source-relay.pyc" <<'PY'
import py_compile
import sys

py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)
PY
cleanup_test_dir

if getent passwd rss-relay >/dev/null; then relay_user_existed=1; fi
if getent group rss-relay >/dev/null; then relay_group_existed=1; fi
if [ "$relay_user_existed" -eq 1 ] && [ "$relay_group_existed" -eq 0 ]; then
  echo 'rss-relay user exists without the required rss-relay group' >&2
  exit 1
fi
if [ -L "$state_dir" ] || { [ -e "$state_dir" ] && [ ! -d "$state_dir" ]; }; then
  echo "unsafe relay state path: $state_dir" >&2
  exit 1
fi
if [ -d "$state_dir" ]; then state_dir_existed=1; fi

existing_listener="$(ss -H -lntp 'sport = :8091' 2>/dev/null || true)"
if [ -n "$existing_listener" ]; then
  existing_main_pid="$(systemctl show rss-source-relay.service -p MainPID --value 2>/dev/null || true)"
  listener_pids="$(printf '%s' "$existing_listener" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u | paste -sd, - || true)"
  if ! [[ "$existing_main_pid" =~ ^[1-9][0-9]*$ ]] \
    || ! systemctl is-active --quiet rss-source-relay.service \
    || [ "$listener_pids" != "$existing_main_pid" ]; then
    echo 'port 8091 is already used by a process not owned by rss-source-relay.service' >&2
    exit 1
  fi
fi

install -d -m 0750 -o ubuntu -g ubuntu "$backup"
for path in "${files[@]}"; do backup_one "$path"; done
relay_enabled_before="$(systemctl is-enabled rss-source-relay.service 2>/dev/null || true)"
relay_active_before="$(systemctl is-active rss-source-relay.service 2>/dev/null || true)"
apache_active_before="$(systemctl is-active apache2 2>/dev/null || true)"
printf 'relay_enabled=%s\nrelay_active=%s\napache_active=%s\n' \
  "$relay_enabled_before" "$relay_active_before" "$apache_active_before" \
  >"$backup/unit-state-before.txt"
apache2ctl -S >"$backup/apache-vhosts-before.txt" 2>&1
backup_ready=1

if [ "$relay_group_existed" -eq 0 ]; then
  groupadd --system rss-relay
fi
if [ "$relay_user_existed" -eq 0 ]; then
  useradd --system --gid rss-relay --home-dir /nonexistent --shell /usr/sbin/nologin rss-relay
fi
install -d -m 0755 /usr/local/libexec
install -m 0755 "$staged" "$program"
install -d -m 0750 -o rss-relay -g rss-relay "$state_dir"

cat >"$unit" <<'UNIT'
[Unit]
Description=Allow-listed RSS source egress relay
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=rss-relay
Group=rss-relay
WorkingDirectory=/var/lib/rss-source-relay
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /usr/local/libexec/rss-source-relay.py
Restart=on-failure
RestartSec=3s
TimeoutStopSec=15s
UMask=0027
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
ReadWritePaths=/var/lib/rss-source-relay
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
UNIT

cat >"$apache_conf" <<'APACHE'
ProxyPass "/.rss-source-bridge-v1/" "http://127.0.0.1:8091/" nocanon retry=0 connectiontimeout=3 timeout=75
ProxyPassReverse "/.rss-source-bridge-v1/" "http://127.0.0.1:8091/"

<Location "/.rss-source-bridge-v1/">
    Require ip 127.0.0.1 ::1 192.0.2.10
    Header always set Cache-Control "private, no-store, max-age=0"
    Header always set X-Robots-Tag "noindex, nofollow, noarchive"
    Header always set X-Content-Type-Options "nosniff"
</Location>
APACHE

systemd-analyze verify "$unit"
apache2ctl configtest
systemctl daemon-reload
systemctl enable rss-source-relay.service
systemctl restart rss-source-relay.service
for _ in $(seq 1 50); do
  if systemctl is-active --quiet rss-source-relay.service && ss -lntp | grep -F '127.0.0.1:8091' | grep -Fq 'python3'; then break; fi
  sleep 0.2
done
systemctl is-active --quiet rss-source-relay.service
ss -lntp | grep -F '127.0.0.1:8091' | grep -F 'python3'

test_dir="$(mktemp -d /tmp/rss-source-relay-test.XXXXXXXX)"
chmod 0700 "$test_dir"
for name in \
  huggingface occ oecd rbi hackernews-show \
  github-freshrss github-a2a github-mcp github-windmill github-openclaw \
  github-dify github-n8n github-immich github-portainer github-traefik github-caddy; do
  curl --fail --silent --show-error --connect-timeout 5 --max-time 60 \
    --output "$test_dir/${name}.xml" "http://127.0.0.1:8091/${name}.xml"
  python3 -c 'import sys,xml.etree.ElementTree as E; E.parse(sys.argv[1])' "$test_dir/${name}.xml"
  printf '%s bytes=%s\n' "$name" "$(stat -c '%s' "$test_dir/${name}.xml")"
done

mapfile -t page_route < <(python3 - "$test_dir/huggingface.xml" <<'PY'
import base64
import sys
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

for node in ET.parse(sys.argv[1]).getroot().iter():
    if node.tag.rsplit("}", 1)[-1].lower() != "link":
        continue
    value = (node.get("href") or node.text or "").strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        (node.get("rel") or "").lower() == "self"
        or parsed.scheme.lower() != "https"
        or host not in {"huggingface.co", "www.huggingface.co"}
        or parsed.path.lower().endswith((".xml", ".rss", ".atom"))
    ):
        continue
    canonical = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    token = base64.urlsafe_b64encode(canonical.encode("utf-8")).decode("ascii").rstrip("=")
    print(host)
    print(token)
    break
else:
    raise SystemExit("Hugging Face feed has no allow-listed page URL")
PY
)
[ "${#page_route[@]}" -eq 2 ]
curl --fail --silent --show-error --connect-timeout 5 --max-time 60 \
  --output "$test_dir/huggingface-page.html" \
  "http://127.0.0.1:8091/page/${page_route[0]}/${page_route[1]}"
[ "$(stat -c '%s' "$test_dir/huggingface-page.html")" -ge 500 ]
printf 'huggingface-page bytes=%s\n' "$(stat -c '%s' "$test_dir/huggingface-page.html")"
cleanup_test_dir

systemctl reload apache2
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
  --resolve rss.example.com:443:127.0.0.1 \
  --output /dev/null https://rss.example.com/.rss-source-bridge-v1/healthz

systemctl status rss-source-relay.service --no-pager >"$backup/relay-status.txt"
apache2ctl -S >"$backup/apache-vhosts-after.txt" 2>&1
sha256sum "$program" "$unit" "$apache_conf" >"$backup/installed-sha256.txt"
find "$state_dir" -maxdepth 1 -type f -printf '%f %s bytes\n' | sort >"$backup/cache-inventory.txt"
chown -R ubuntu:ubuntu "$backup"

committed=1
echo '=== source relay installed ==='
printf 'backup=%s\n' "$backup"
systemctl is-active rss-source-relay.service
systemctl is-enabled rss-source-relay.service
