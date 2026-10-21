#!/usr/bin/env python3
"""Isolated fixture test for the V5.3 host translation side branch.

This file is installed in ``00-preflight`` and locates the production module at
``../04-daily-pipeline/host-translate.py``.  It never uses the network, real
FreshRSS credentials, or OpenClaw; all mutable state lives below one temporary
directory and every external boundary is replaced before use.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path


def module_path():
    here = Path(__file__).resolve().parent
    requested = os.environ.get('HOST_TRANSLATE_UNDER_TEST')
    choices = [
        Path(requested) if requested else None,
        here.parent / '04-daily-pipeline' / 'host-translate.py',
        here / 'host-translate.py',  # source-tree convenience only
    ]
    for choice in choices:
        if choice is not None and choice.is_file():
            return choice.resolve(strict=True)
    raise RuntimeError('HOST_TRANSLATE_FIXTURE_TARGET_NOT_FOUND')


def write_json(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.chmod(path, mode)


def load_target(path):
    target_dir = str(path.parent)
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)
    spec = importlib.util.spec_from_file_location('host_translate_v53_fixture_target', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('HOST_TRANSLATE_FIXTURE_IMPORT_FAILED')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    target = module_path()
    with tempfile.TemporaryDirectory(prefix='host-translate-v53-fixture-') as temporary:
        base = Path(temporary).resolve(strict=True)
        os.chmod(base, 0o700)
        finalizer = base / 'finalizer'
        current = base / 'current'
        public = base / 'public'
        translation = base / 'translation'
        for directory, mode in (
            (finalizer, 0o700), (finalizer / 'morning', 0o700),
            (current, 0o700), (public, 0o750),
        ):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, mode)

        os.environ.update({
            'RSS_TRANSLATION_ROOT': str(translation),
            'RSS_FINALIZER_STAGE_ROOT': str(finalizer),
            'RSS_DAILY_STAGE_CURRENT': str(current),
            'RSS_PUBLISH_PUBLIC_ROOT': str(public),
            'RSS_PUBLISH_TOKEN': 'a' * 64,
            'RSS_PUBLISH_BASE_URL': 'https://example.invalid/intelligence',
            'OPENCLAW_ENV_FILE': str(base / 'absent-openclaw.env'),
            'RSS_CURATOR_ENV_FILE': str(base / 'absent-curator.env'),
            'OPENCLAW_BIN': '/fixture/never-run-openclaw',
            'RSS_TRANSLATOR_AGENT': 'rss-v53-translator',
        })
        host = load_target(target)
        real_jina = host.jina_extract
        real_sweep = host.sweep_translator_sessions

        def install_stage(run_id, stage_id, event_key, snapshot_summary=None):
            source_url = 'https://example.com/article?private=must-not-reach-jina'
            item = {
                'eventKey': event_key,
                'title': '隔离测试文章',
                'summary': '这是分类阶段保留的候选摘要。',
                'sources': [{'name': 'fixture', 'url': source_url}],
            }
            candidate = {
                'schemaVersion': 2,
                'allPagesComplete': True,
                'edition': 'morning',
                'runId': run_id,
                'candidates': [{
                    'primaryArticleId': 'article-1',
                    'eventKeyCandidate': event_key,
                    'summary': item['summary'],
                    'sources': item['sources'],
                }],
            }
            stage = {
                'schemaVersion': 2,
                'status': 'staged',
                'generator': 'host-deterministic-v1',
                'edition': 'morning',
                'candidateRunId': run_id,
                'stageId': stage_id,
                'items': [item],
                'accounting': [{
                    'primaryArticleId': 'article-1',
                    'disposition': 'retained',
                    'finalEventKey': event_key,
                }],
            }
            write_json(current / 'morning-candidates.json', candidate)
            if snapshot_summary is not None:
                run_dir = current.parent / 'runs' / run_id
                write_json(run_dir / 'combined.json', candidate)
                write_json(run_dir / 'raw-page-001.json', {
                    'schemaVersion': 2,
                    'articles': [{
                        'id': 'article-1',
                        'url': source_url,
                        'summaryText': snapshot_summary,
                        'summaryTruncated': False,
                    }],
                })
            stage_path = finalizer / 'morning' / f'{stage_id}.json'
            write_json(stage_path, stage)
            return stage_path, stage

        # No real model, session store, sandbox, HTTP, or FreshRSS boundary is
        # allowed during the pipeline fixtures.
        host.sweep_translator_sessions = lambda: 0
        host.cleanup_sessions = lambda session_key, session_ids: None
        host.translate_article = lambda text, session_key, session_ids: '这是隔离测试生成的完整译文。'
        host.freshrss_db_entry_text = lambda expected_url, env: ''
        authentication_calls = {'count': 0}

        def forbidden_authentication():
            authentication_calls['count'] += 1
            raise RuntimeError('fixture authentication unavailable')

        host.daily_common.fresh_config = forbidden_authentication

        # Feed bodies may be HTML fragments rather than full documents.  If
        # trafilatura declines such a fragment, the bounded tag-stripping
        # fallback must preserve the complete text instead of returning empty.
        real_trafilatura = host.trafilatura
        host.trafilatura = type('NoFragmentText', (), {'html2txt': staticmethod(lambda raw: '')})()
        assert host.feed_markup_text('<p>First complete paragraph.</p><p>Second complete paragraph.</p>') == (
            'First complete paragraph.\nSecond complete paragraph.'
        )
        host.trafilatura = real_trafilatura

        # Direct extraction succeeds: FreshRSS authentication must stay lazy.
        host.direct_extract = lambda url: ('Direct fixture body. ' * 40, 'web-extracted')
        host.jina_extract = lambda url: (_ for _ in ()).throw(AssertionError('Jina must not run'))
        first_path, first_stage = install_stage('fixture-run-one', 'fixture-stage-one', 'fixture-event-one')
        first_manifest_path = host.finalize(first_path, False)
        first_manifest = host.load_json(first_manifest_path)
        assert authentication_calls['count'] == 0
        assert first_manifest['status'] == 'complete'
        assert first_manifest['coveragePolicy'] == 'all-stage-items-v1'
        assert first_manifest['itemCount'] == first_manifest['stageItemCount'] == 1
        assert first_manifest['items'][0]['status'] == 'complete'
        assert first_manifest['items'][0]['sourceCompleteness'] == 'full'
        assert first_manifest['items'][0]['truncated'] is False
        fixed_expiry = first_manifest['expiresAt']
        reused = host.load_json(host.finalize(first_path, True))
        assert reused['expiresAt'] == fixed_expiry

        # An expired stage tombstone is permanent and cannot be renewed.
        first_path, first_stage = install_stage('fixture-run-one', 'fixture-stage-one', 'fixture-event-one')
        tombstone = translation / 'tombstones' / 'morning' / host.manifest_name(first_stage)
        old_first = host.now_utc().replace(microsecond=0) - timedelta(hours=49)
        write_json(tombstone, {
            'schemaVersion': 1,
            'producer': host.PRODUCER,
            'stageId': first_stage['stageId'],
            'candidateRunId': first_stage['candidateRunId'],
            'firstSavedAt': host.iso(old_first),
            'expiresAt': host.iso(old_first + timedelta(hours=48)),
        })
        try:
            host.finalize(first_path, False)
            raise AssertionError('expired tombstone was renewed')
        except host.TranslationError as exc:
            assert str(exc) == 'STAGE_TRANSLATION_WINDOW_EXPIRED'
        first_manifest_path.unlink()

        # An expired pre-tombstone manifest must be rejected before cleanup can
        # delete its only lifetime evidence and accidentally renew the stage.
        legacy_path, legacy_stage = install_stage('fixture-run-legacy', 'fixture-stage-legacy', 'fixture-event-legacy')
        legacy_first = host.now_utc().replace(microsecond=0) - timedelta(hours=49)
        legacy_manifest_path = translation / 'manifests' / 'morning' / host.manifest_name(legacy_stage)
        write_json(legacy_manifest_path, {
            'schemaVersion': 1,
            'producer': host.PRODUCER,
            'candidateRunId': legacy_stage['candidateRunId'],
            'stageId': legacy_stage['stageId'],
            'edition': 'morning',
            'generatedAt': host.iso(legacy_first),
            'savedAt': host.iso(legacy_first),
            'expiresAt': host.iso(legacy_first + timedelta(hours=48)),
            'itemCount': 0,
            'stageItemCount': 1,
            'items': [],
        })
        try:
            host.finalize(legacy_path, False)
            raise AssertionError('expired legacy manifest was renewed')
        except host.TranslationError as exc:
            assert str(exc) == 'STAGE_TRANSLATION_WINDOW_EXPIRED'
        assert legacy_manifest_path.is_file()

        # Direct and Jina extraction fail, then FreshRSS auth also fails: no
        # partial/summary page may be published and the stage remains pending.
        second_path, _ = install_stage('fixture-run-two', 'fixture-stage-two', 'fixture-event-two')
        host.direct_extract = lambda url: (_ for _ in ()).throw(RuntimeError('direct unavailable'))
        host.jina_extract = lambda url: (_ for _ in ()).throw(RuntimeError('Jina unavailable'))
        try:
            host.finalize(second_path, False)
            raise AssertionError('missing full source was silently published')
        except host.TranslationError as exc:
            assert str(exc) == 'TRANSLATION_STAGE_INCOMPLETE'
        assert authentication_calls['count'] == 1
        pending_path = translation / 'manifests' / 'morning' / host.manifest_name(host.load_json(second_path))
        pending = host.load_json(pending_path)
        assert pending['status'] == 'building' and pending['items'] == []

        # The entire non-empty FreshRSS entry is a valid last-resort source,
        # even when a feed intentionally publishes only a short entry.
        host.daily_common.fresh_config = lambda: ('https://fixture.invalid/api', 'user', 'password')
        host.daily_common.login_freshrss = lambda api, user, password: 'fixture-auth'
        host.freshrss_item_text = lambda article_id, auth, api: 'Short source entry with every character supplied by the feed.'
        second_manifest = host.load_json(host.finalize(second_path, False))
        assert second_manifest['status'] == 'complete'
        assert second_manifest['itemCount'] == second_manifest['stageItemCount'] == 1
        assert second_manifest['items'][0]['status'] == 'complete'
        assert second_manifest['items'][0]['sourceMethod'] == 'freshrss-entry'
        assert second_manifest['items'][0]['sourceCompleteness'] == 'full'
        assert second_manifest['items'][0]['truncated'] is False

        # When the GReader endpoint has evicted an entry, one exact query-only
        # database match is still the complete content FreshRSS ingested.
        db_path, _ = install_stage(
            'fixture-run-db', 'fixture-stage-db', 'fixture-event-db',
        )
        host.freshrss_item_text = lambda article_id, auth, api: ''
        host.freshrss_db_entry_text = lambda expected_url, env: 'Complete entry retained by FreshRSS SQLite.'
        db_manifest = host.load_json(host.finalize(db_path, False))
        assert db_manifest['status'] == 'complete'
        assert db_manifest['itemCount'] == db_manifest['stageItemCount'] == 1
        assert db_manifest['items'][0]['sourceMethod'] == 'freshrss-db-entry'
        assert db_manifest['items'][0]['sourceCompleteness'] == 'full'
        assert db_manifest['items'][0]['truncated'] is False

        # If FreshRSS has already evicted an item, the immutable raw snapshot
        # may supply the entire feed entry only when it was explicitly not
        # truncated and the stable article ID and URL match exactly.
        snapshot_path, _ = install_stage(
            'fixture-run-snapshot', 'fixture-stage-snapshot', 'fixture-event-snapshot',
            snapshot_summary='Complete short entry frozen by the source snapshot.',
        )
        host.freshrss_item_text = lambda article_id, auth, api: ''
        host.freshrss_db_entry_text = lambda expected_url, env: ''
        snapshot_manifest = host.load_json(host.finalize(snapshot_path, False))
        assert snapshot_manifest['status'] == 'complete'
        assert snapshot_manifest['itemCount'] == snapshot_manifest['stageItemCount'] == 1
        assert snapshot_manifest['items'][0]['sourceMethod'] == 'freshrss-snapshot-entry'
        assert snapshot_manifest['items'][0]['sourceCompleteness'] == 'full'
        assert snapshot_manifest['items'][0]['truncated'] is False

        # Jina receives exactly one absolute URL in its path and never the
        # original query or fragment.
        captured = {}

        def fake_pinned_get(url, allow_types, max_bytes=0):
            captured['url'] = url
            body = ('Markdown Content:\n' + 'Reader fixture body. ' * 30).encode()
            return body, 'text/plain', url

        real_jina.__globals__['pinned_get'] = fake_pinned_get
        real_jina('https://example.com/a/path?q=secret#fragment')
        assert captured['url'] == 'https://r.jina.ai/https://example.com/a/path'

        # Sweep only an isolated dedicated-session directory, including normal,
        # trajectory, and deletion-tombstone artifacts; no OpenClaw is called.
        agent_root = base / 'fake-agent'
        sessions = agent_root / 'sessions'
        sessions.mkdir(parents=True)
        os.chmod(agent_root, 0o775)
        os.chmod(sessions, 0o775)
        write_json(sessions / 'sessions.json', {
            'agent:rss-v53-translator:fixture': {'sessionId': '12345678-abcd'},
        })
        (sessions / '12345678-abcd.jsonl').write_text('untrusted source', encoding='utf-8')
        (sessions / '12345678-abcd.trajectory.jsonl').write_text('untrusted source', encoding='utf-8')
        (sessions / '.deleted.fixture-artifact').write_text('untrusted source', encoding='utf-8')
        host.translator_sessions_root = lambda: sessions
        host.sweep_translator_sessions = real_sweep
        assert host.sweep_translator_sessions() == 3
        assert json.loads((sessions / 'sessions.json').read_text(encoding='utf-8')) == {}
        assert sorted(path.name for path in sessions.iterdir()) == ['sessions.json']

        # A symlink in any parent component is rejected before a child can be
        # created, proving cleanup/root traversal cannot escape its configured tree.
        outside = base / 'outside'
        outside.mkdir()
        bad_parent = base / 'bad-parent'
        try:
            bad_parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
        else:
            try:
                host.ensure_dir(bad_parent / 'must-not-exist', 0o700)
                raise AssertionError('parent symlink was accepted')
            except host.TranslationError:
                pass
            assert not (outside / 'must-not-exist').exists()

    print('HOST_TRANSLATE_V53_FIXTURE_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
