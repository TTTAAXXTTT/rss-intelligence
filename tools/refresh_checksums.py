"""Rebuild hashes after configuring a deployment copy; excludes private/runtime files."""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'data', 'outputs', 'backups', '.tools'}


def sources():
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and not set(p.relative_to(ROOT).parts) & EXCLUDED
                  and not (p.name.startswith('.env') and p.name != '.env.example')
                  and p.suffix not in {'.pyc', '.pem', '.key', '.db', '.sqlite', '.log', '.zip'})


def main():
    relationships = {
        'install-aws-source-relay.sh': '07-network-ops/rss-source-relay.py',
        'upgrade-aws-source-page-relay.sh': '07-network-ops/rss-source-relay.py',
        'apply-target-freshrss-relay-cutover.sh': '07-network-ops/update-target-freshrss-relay.py',
        'install-target-fetch-prewarm.sh': '04-retry-scheduler/rss-v52-freshrss-fetch-retry.sh',
        'install-target-source-page-relay-client.sh': '04-daily-pipeline/host-translate.py',
    }
    for script, source in relationships.items():
        path = ROOT / '07-network-ops' / script
        value = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
        text, count = re.subn(r"(?m)^expected='[a-f0-9]{64}'$", "expected='" + value + "'", path.read_text(encoding='utf-8'))
        if count != 1:
            raise RuntimeError('CHECKSUM_RELATIONSHIP_INVALID: ' + script)
        path.write_text(text, encoding='utf-8', newline='\n')
    rows = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ./' + p.relative_to(ROOT).as_posix()
            for p in sources() if p.name != 'checksums.sha256']
    (ROOT / 'checksums.sha256').write_text('\n'.join(rows) + '\n', encoding='utf-8', newline='\n')
    print('CHECKSUMS_REFRESHED files=' + str(len(rows)))


if __name__ == '__main__':
    main()
