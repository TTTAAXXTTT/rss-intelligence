#!/usr/bin/env bash
set -euo pipefail

staged='/tmp/rss-source-relay-page.py'
program='/usr/local/libexec/rss-source-relay.py'
expected='c56455b1638478b87a90e5849a0d7f5987239fdd2ff7338d90bb2c344d7f9889'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/source-page-relay-${stamp}"
test_dir=''
installed=0

cleanup() {
  rc=$?
  trap - EXIT INT TERM HUP
  if [ -n "$test_dir" ]; then
    case "$test_dir" in
      /tmp/rss-source-relay-page-test.*)
        if [ -d "$test_dir" ] && [ ! -L "$test_dir" ]; then
          find "$test_dir" -mindepth 1 -maxdepth 1 -type f -delete
          rmdir -- "$test_dir" || true
        fi
        ;;
    esac
  fi
  if [ "$rc" -ne 0 ] && [ "$installed" -eq 1 ]; then
    echo 'page relay upgrade failed; restoring previous program' >&2
    install -m 0755 "$backup/rss-source-relay.py.before" "$program" || true
    systemctl restart rss-source-relay.service || true
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM HUP

[ -f "$staged" ] || { echo 'staged page relay is missing' >&2; exit 1; }
[ -f "$program" ] || { echo 'installed source relay is missing' >&2; exit 1; }
actual="$(sha256sum "$staged" | awk '{print $1}')"
[ "$actual" = "$expected" ] || { echo 'staged page relay checksum mismatch' >&2; exit 1; }
python3 -m py_compile "$staged"

systemctl is-active --quiet rss-source-relay.service
main_pid="$(systemctl show rss-source-relay.service -p MainPID --value)"
listener_pids="$(ss -H -lntp 'sport = :8091' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u | paste -sd, -)"
[[ "$main_pid" =~ ^[1-9][0-9]*$ ]] && [ "$listener_pids" = "$main_pid" ] || {
  echo 'port 8091 is not exclusively owned by rss-source-relay.service' >&2
  exit 1
}

install -d -m 0750 -o ubuntu -g ubuntu "$backup"
install -m 0600 -o ubuntu -g ubuntu "$program" "$backup/rss-source-relay.py.before"
sha256sum "$backup/rss-source-relay.py.before" >"$backup/before.sha256"

install -m 0755 "$staged" "$program"
installed=1
systemctl restart rss-source-relay.service
for _ in $(seq 1 50); do
  if systemctl is-active --quiet rss-source-relay.service \
    && ss -H -lnt 'sport = :8091' | grep -Fq '127.0.0.1:8091'; then
    break
  fi
  sleep 0.2
done
systemctl is-active --quiet rss-source-relay.service
ss -H -lnt 'sport = :8091' | grep -Fq '127.0.0.1:8091'

test_dir="$(mktemp -d /tmp/rss-source-relay-page-test.XXXXXXXX)"
chmod 0700 "$test_dir"
page_path="$(python3 - <<'PY'
import base64
url = 'https://huggingface.co/blog/asr-benchmark-optimization'
token = base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')
print('/page/huggingface.co/' + token)
PY
)"
curl --fail --silent --show-error --connect-timeout 5 --max-time 60 \
  --output "$test_dir/huggingface.html" "http://127.0.0.1:8091${page_path}"
python3 - "$test_dir/huggingface.html" <<'PY'
from pathlib import Path
import sys
body = Path(sys.argv[1]).read_bytes()
assert len(body) > 10000
assert b'<html' in body[:10000].lower()
print(f'page-relay-test=ok bytes={len(body)}')
PY

sha256sum "$program" >"$backup/installed.sha256"
systemctl status rss-source-relay.service --no-pager >"$backup/service-status.txt"
chown -R ubuntu:ubuntu "$backup"
installed=0
echo '=== source page relay upgraded ==='
printf 'backup=%s\n' "$backup"
systemctl is-active rss-source-relay.service
