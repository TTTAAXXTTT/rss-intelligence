"""Render a new deployment copy, leaving source and real configurations untouched."""
from pathlib import Path
import argparse
import ipaddress
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True)
    parser.add_argument('--relay-domain', required=True)
    parser.add_argument('--target-ip', required=True)
    parser.add_argument('--home', default='/home/ubuntu')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    for domain in (args.domain, args.relay_domain):
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', domain) or '..' in domain:
            parser.error('Provide a hostname without a URL scheme or path')
    ipaddress.IPv4Address(args.target_ip)
    if not re.fullmatch(r'/home/[A-Za-z0-9_-]+', args.home):
        parser.error('--home must be /home/<username>')
    dest = args.destination.resolve()
    if dest.exists() or dest == ROOT or ROOT in dest.parents:
        parser.error('Destination must be a new directory outside this checkout')
    from refresh_checksums import sources
    files = sources()
    dest.mkdir(parents=True, exist_ok=False)
    replacements = {
        r'rss\.example\.com': re.escape(args.relay_domain),
        r'rss\.example\.org': re.escape(args.domain),
        'rss.example.com': args.relay_domain,
        'rss.example.org': args.domain,
        '192.0.2.10': args.target_ip,
        '/home/ubuntu': args.home,
    }
    # The primary domain is FreshRSS/publishing. Relay templates point to relay-domain.
    for file in files:
        rel = file.relative_to(ROOT)
        text = file.read_text(encoding='utf-8')
        for before, after in replacements.items():
            if before in {'rss.example.com', r'rss\.example\.com'} and rel.parts[0] != '07-network-ops':
                after = re.escape(args.domain) if '\\' in before else args.domain
            text = text.replace(before, after)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')
    subprocess.run([sys.executable, str(dest / 'tools/refresh_checksums.py')], check=True)
    print('DEPLOYMENT_COPY_READY files=' + str(len(files)))


if __name__ == '__main__':
    main()
