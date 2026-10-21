#!/usr/bin/env bash
set -euo pipefail

staged='/tmp/rss-v52-freshrss-fetch-retry.sh'
program='/home/ubuntu/rss-intelligence-v5.2/04-retry-scheduler/rss-v52-freshrss-fetch-retry.sh'
checksums='/home/ubuntu/rss-intelligence-v5.2/checksums.sha256'
expected='35877ee8345b6b41ff06d5c55b03a6617b79baf36eb31946ab8b54c84b194c49'
lock='/home/ubuntu/.openclaw/rss-intelligence/v52-source-dataset.lock'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/tencent-fetch-prewarm-${stamp}"
changed=0

finish() {
  rc=$?
  trap - EXIT INT TERM HUP
  if [ "$rc" -ne 0 ] && [ "$changed" -eq 1 ]; then
    echo 'fetch prewarm install failed; restoring previous files' >&2
    install -m 0755 -o ubuntu -g ubuntu "$backup/fetch-retry.sh.before" "$program" || true
    install -m 0644 -o ubuntu -g ubuntu "$backup/checksums.sha256.before" "$checksums" || true
  fi
  exit "$rc"
}
trap finish EXIT INT TERM HUP

[ -f "$staged" ] || { echo 'staged fetch wrapper is missing' >&2; exit 1; }
actual="$(sha256sum "$staged" | awk '{print $1}')"
[ "$actual" = "$expected" ] || { echo 'staged fetch wrapper checksum mismatch' >&2; exit 1; }
bash -n "$staged"

exec 8>"$lock"
flock -w 1800 8 || { echo 'timed out waiting for source dataset lock' >&2; exit 1; }
if systemctl --user is-active --quiet rss-v52-freshrss-fetch.service; then
  echo 'FreshRSS refresh is active; refusing concurrent install' >&2
  exit 1
fi

sudo install -d -m 0750 -o ubuntu -g ubuntu "$backup"
install -m 0600 "$program" "$backup/fetch-retry.sh.before"
install -m 0600 "$checksums" "$backup/checksums.sha256.before"
install -m 0755 "$staged" "$program"
changed=1

python3 - "$checksums" "$expected" <<'PY'
from pathlib import Path
import os
import sys
import tempfile

path = Path(sys.argv[1])
expected = sys.argv[2]
target = './04-retry-scheduler/rss-v52-freshrss-fetch-retry.sh'
rows = path.read_text(encoding='utf-8').splitlines()
out = []
matched = 0
for row in rows:
    parts = row.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip().lstrip('*') == target:
        out.append(f'{expected}  {target}')
        matched += 1
    else:
        out.append(row)
if matched != 1:
    raise SystemExit('fetch wrapper checksum entry is not unique')
fd, temporary = tempfile.mkstemp(prefix='.checksums.', dir=path.parent)
try:
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(out) + '\n')
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
finally:
    try: os.unlink(temporary)
    except FileNotFoundError: pass
PY
chown ubuntu:ubuntu "$program" "$checksums"
bash -n "$program"
sha256sum "$program" "$checksums" >"$backup/installed.sha256"
chown -R ubuntu:ubuntu "$backup"
changed=0
echo '=== FreshRSS relay prewarm installed ==='
printf 'backup=%s\n' "$backup"
