#!/usr/bin/env bash
set -euo pipefail

staged='/tmp/host-translate-page-relay.py'
program='/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/host-translate.py'
checksums='/home/ubuntu/rss-intelligence-v5.2/checksums.sha256'
env_file='/home/ubuntu/.openclaw/.env'
expected='a8a6f97521d02cc11daae032d6219ca5a391e97fba06c9a20012fdf68dd411cc'
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/ubuntu/rss-intelligence-backups/tencent-source-page-relay-${stamp}"
lock='/home/ubuntu/.openclaw/rss-intelligence/v52-daily-global.lock'
changed=0

finish() {
  rc=$?
  trap - EXIT INT TERM HUP
  if [ "$rc" -ne 0 ] && [ "$changed" -eq 1 ]; then
    echo 'source page relay client install failed; restoring previous files' >&2
    install -m 0755 -o ubuntu -g ubuntu "$backup/host-translate.py.before" "$program" || true
    install -m 0644 -o ubuntu -g ubuntu "$backup/checksums.sha256.before" "$checksums" || true
    install -m 0600 -o ubuntu -g ubuntu "$backup/openclaw.env.before" "$env_file" || true
  fi
  exit "$rc"
}
trap finish EXIT INT TERM HUP

[ -f "$staged" ] || { echo 'staged host translator is missing' >&2; exit 1; }
actual="$(sha256sum "$staged" | awk '{print $1}')"
[ "$actual" = "$expected" ] || { echo 'host translator checksum mismatch' >&2; exit 1; }
/home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/python -m py_compile "$staged"

exec 8>"$lock"
flock -w 60 8 || { echo 'timed out waiting for daily pipeline lock' >&2; exit 1; }

install -d -m 0750 -o ubuntu -g ubuntu "$backup"
install -m 0600 -o ubuntu -g ubuntu "$program" "$backup/host-translate.py.before"
install -m 0600 -o ubuntu -g ubuntu "$checksums" "$backup/checksums.sha256.before"
install -m 0600 -o ubuntu -g ubuntu "$env_file" "$backup/openclaw.env.before"

install -m 0755 -o ubuntu -g ubuntu "$staged" "$program"
changed=1
python3 - "$checksums" "$expected" <<'PY'
from pathlib import Path
import os
import re
import sys
import tempfile

path = Path(sys.argv[1])
expected = sys.argv[2]
rows = path.read_text(encoding='utf-8').splitlines()
target = './04-daily-pipeline/host-translate.py'
matched = 0
out = []
for row in rows:
    parts = row.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip().lstrip('*') == target:
        out.append(f'{expected}  {target}')
        matched += 1
    else:
        out.append(row)
if matched != 1:
    raise SystemExit('host translator checksum entry is not unique')
fd, temp = tempfile.mkstemp(prefix='.checksums.', dir=path.parent)
try:
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(out) + '\n')
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, path)
finally:
    try: os.unlink(temp)
    except FileNotFoundError: pass
PY

python3 - "$env_file" <<'PY'
from pathlib import Path
import os
import sys
import tempfile

path = Path(sys.argv[1])
values = {
    'RSS_SOURCE_PAGE_RELAY_BASE': 'https://rss.example.com/.rss-source-bridge-v1/page',
    'RSS_SOURCE_PAGE_RELAY_HOSTS': 'huggingface.co',
}
out = []
seen = set()
for raw in path.read_text(encoding='utf-8').splitlines():
    key = raw.split('=', 1)[0].strip() if '=' in raw else ''
    if key in values:
        if key not in seen:
            out.append(f'{key}={values[key]}')
            seen.add(key)
        continue
    out.append(raw)
for key, value in values.items():
    if key not in seen:
        out.append(f'{key}={value}')
fd, temp = tempfile.mkstemp(prefix='.env.', dir=path.parent)
try:
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(out) + '\n')
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)
finally:
    try: os.unlink(temp)
    except FileNotFoundError: pass
PY
chown ubuntu:ubuntu "$checksums" "$env_file"

sudo -u ubuntu HOME=/home/ubuntu USER=ubuntu LOGNAME=ubuntu \
  /home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/python - <<'PY'
import importlib.util
path = '/home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/host-translate.py'
spec = importlib.util.spec_from_file_location('host_translate', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
text, method = module.source_relay_extract(
    'https://huggingface.co/blog/asr-benchmark-optimization',
    module.read_env(),
)
assert len(text) >= 300 and method == 'private-source-relay'
print(f'target-source-page-relay=ok extractedChars={len(text)}')
PY

sha256sum "$program" "$checksums" >"$backup/installed.sha256"
stat -c '%U:%G %a %n' "$program" "$checksums" "$env_file" >"$backup/installed-modes.txt"
chown -R ubuntu:ubuntu "$backup"
changed=0
echo '=== source page relay client installed ==='
printf 'backup=%s\n' "$backup"
