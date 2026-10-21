#!/usr/bin/env python3
import argparse
import json
import os
import shutil
from pathlib import Path

ROOT = Path(os.environ.get('RSS_INTELLIGENCE_ROOT', '/home/ubuntu/.openclaw/rss-intelligence'))


def copy_tree_if_needed(source, target):
    if not source.exists():
        return 'source-missing'
    if target.exists():
        return 'target-exists'
    shutil.copytree(source, target)
    for path in target.rglob('*'):
        try:
            if path.is_dir():
                os.chmod(path, 0o700)
            elif path.is_file():
                os.chmod(path, 0o600)
        except PermissionError:
            pass
    os.chmod(target, 0o700)
    return 'copied'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    pairs = [
        (ROOT / 'publisher-data-v51', ROOT / 'publisher-data-v52'),
        (ROOT / 'daily-state-v51', ROOT / 'daily-state-v52'),
    ]
    print('V52_RUNTIME_MIGRATION_PLAN')
    for source, target in pairs:
        print(f'{source} -> {target}')
    print(f'bootstrap-state-v52 exists={str((ROOT / "bootstrap-state-v52.json").exists()).lower()} (never copied from V5.1)')

    if not args.apply:
        print('CHECK_ONLY=true')
        return

    results = []
    for source, target in pairs:
        result = copy_tree_if_needed(source, target)
        results.append((source, target, result))
        print(f'MIGRATE {source.name}->{target.name} result={result}')

    # A copied V5.1 daily state remains readable as recovery input; V5.2 will write schemaVersion 2 on first success.
    bootstrap = ROOT / 'bootstrap-state-v52.json'
    if bootstrap.exists():
        try:
            state = json.loads(bootstrap.read_text(encoding='utf-8'))
            if (
                state.get('schemaVersion') != 2
                or not isinstance(state.get('feeds'), dict)
                or not isinstance(state.get('quarantinedArticleIds', {}), dict)
            ):
                raise RuntimeError('existing bootstrap-state-v52.json is invalid')
        except Exception as exc:
            raise SystemExit(f'STOP_BOOTSTRAP_STATE_INVALID: {exc}')
    print('V52_RUNTIME_MIGRATION_OK')


if __name__ == '__main__':
    main()
