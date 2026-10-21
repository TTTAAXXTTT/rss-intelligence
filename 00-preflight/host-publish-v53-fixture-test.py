#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import hashlib
import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PUBLISH = ROOT / 'host-publish.py'
FIXTURE = ROOT / 'fixture-current'
if not PUBLISH.is_file():
    PUBLISH = ROOT.parent / '04-daily-pipeline' / 'host-publish.py'
if not FIXTURE.is_dir():
    FIXTURE = ROOT / 'host-publish-v53-fixture-current'


spec = importlib.util.spec_from_file_location('host_publish_v53', PUBLISH)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


with tempfile.TemporaryDirectory(prefix='host-publish-v53-') as temp:
    root = Path(temp)
    current = root / 'current'
    data = root / 'data'
    public = root / 'public'
    state = root / 'state'
    current.mkdir()
    candidate = json.loads((FIXTURE / 'morning-candidates.json').read_text(encoding='utf-8'))
    candidate['bootstrapFeedIds'] = []
    candidate['bootstrapQuarantineReleasedArticleIds'] = []
    candidate['bootstrapQuarantineFile'] = ''
    (current / 'morning-candidates.json').write_text(
        json.dumps(candidate, ensure_ascii=False), encoding='utf-8'
    )
    stage = json.loads((FIXTURE / 'stage.json').read_text(encoding='utf-8'))
    token = 'a' * 64
    feed_base = f'https://example.test/intelligence/{token}'
    translations = public / token / 'translations'
    translations.mkdir(parents=True)
    translation_root = root / 'translation-v53'
    manifest_dir = translation_root / 'manifests' / 'morning'
    manifest_dir.mkdir(parents=True)
    for directory in (translation_root, translation_root / 'manifests', manifest_dir):
        os.chmod(directory, 0o700)
    saved = datetime.now(timezone.utc).replace(microsecond=0)
    expires = saved + timedelta(hours=48)
    saved_text = saved.isoformat().replace('+00:00', 'Z')
    expires_text = expires.isoformat().replace('+00:00', 'Z')
    retained = {row['finalEventKey']: row['primaryArticleId'] for row in stage['accounting'] if row['disposition'] == 'retained'}
    rows = []
    for number, item in enumerate(stage['items']):
        name = f'{number:064x}.html'
        page = translations / name
        page.write_text('<!doctype html><p>译文</p>', encoding='utf-8')
        os.chmod(page, 0o644)
        rows.append({
            'eventKey': item['eventKey'],
            'primaryArticleId': retained[item['eventKey']],
            'originalUrl': item['sources'][0]['url'],
            'translatedUrl': f'{feed_base}/translations/{name}',
            'publicFile': f'translations/{name}',
            'savedAt': saved_text,
            'expiresAt': expires_text,
            'status': 'complete',
            'sourceMethod': 'fixture',
            'sourceCompleteness': 'full',
            'truncated': False,
            'sourceChars': 100,
            'translatedChars': 80,
            'outputHash': hashlib.sha256(page.read_bytes()).hexdigest(),
        })
    manifest = manifest_dir / 'manifest.json'
    base_manifest = {
        'schemaVersion': 1,
        'producer': 'host-translation-v1',
        'status': 'complete',
        'coveragePolicy': 'all-stage-items-v1',
        'candidateRunId': stage['candidateRunId'],
        'stageId': stage['stageId'],
        'edition': 'morning',
        'generatedAt': saved_text,
        'savedAt': saved_text,
        'expiresAt': expires_text,
        'itemCount': len(rows),
        'stageItemCount': len(stage['items']),
        'expectedKeysSha256': publisher.expected_keys_hash(stage),
        'items': rows,
    }
    manifest.write_text(json.dumps(base_manifest, ensure_ascii=False), encoding='utf-8')
    os.chmod(manifest, 0o600)
    env = dict(os.environ)
    env.update({
        'OPENCLAW_ENV_FILE': str(root / 'missing.env'),
        'RSS_DAILY_STAGE_CURRENT': str(current),
        'RSS_DAILY_STATE_ROOT': str(state),
        'RSS_PUBLISH_DATA_ROOT': str(data),
        'RSS_PUBLISH_PUBLIC_ROOT': str(public),
        'RSS_PUBLISH_BASE_URL': 'https://example.test/intelligence',
        'RSS_PUBLISH_TOKEN': token,
        'RSS_BOOTSTRAP_STATE': str(root / 'bootstrap.json'),
        'RSS_TRANSLATION_ROOT': str(translation_root),
    })
    result = subprocess.run(
        [sys.executable, str(PUBLISH), str(FIXTURE / 'stage.json'),
         '--translation-manifest', str(manifest)],
        env=env, text=True, capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    output = json.loads(result.stdout)
    pub = public / token
    for name in ('all.xml', 'priority.xml', 'reports.xml', 'brief.xml'):
        ET.parse(pub / name)
    assert output['itemCount'] == 24
    assert output['translationCompleteCount'] == 24
    assert output['translationLimitedCount'] == 0
    assert 'AI精选情报' not in (pub / 'all.xml').read_text(encoding='utf-8')
    assert 'AI完整日报' not in (pub / 'reports.xml').read_text(encoding='utf-8')
    full = (pub / '2026-08-15-morning.html').read_text(encoding='utf-8')
    brief = (pub / '2026-08-15-morning-brief.html').read_text(encoding='utf-8')
    assert full.count('class="cover"') == 1
    assert brief.count('class="cover"') == 1
    assert full.count('<h2>') == 24
    assert brief.count('<li>') == 24
    report = json.loads((data / 'reports' / '2026-08-15-morning.json').read_text(encoding='utf-8'))
    assert report['presentationVersion'] == 'content-presentation-v1'
    assert report['itemCount'] == 24
    assert report['markdown'].count('阅读中文全文（保留 48 小时）') == 24
    assert report['briefMarkdown'].count('阅读中文全文（保留 48 小时）') == 24
    content_tag = '{http://purl.org/rss/1.0/modules/content/}encoded'
    translation_pattern = re.compile(r'https://example\.test/intelligence/[a-f0-9]{64}/translations/[a-f0-9]{64}\.html')
    all_nodes = ET.parse(pub / 'all.xml').findall('./channel/item')
    priority_nodes = ET.parse(pub / 'priority.xml').findall('./channel/item')
    report_nodes = ET.parse(pub / 'reports.xml').findall('./channel/item')
    brief_nodes = ET.parse(pub / 'brief.xml').findall('./channel/item')
    expected_originals = {item['sources'][0]['url'] for item in stage['items']}
    expected_translations = {row['translatedUrl'] for row in rows}
    expected_priority_originals = {
        item['sources'][0]['url'] for item in stage['items']
        if item['priority'] == 'S' or item.get('isHighValueOpportunity') is True
    }
    assert len(all_nodes) == len(stage['items'])
    assert {node.findtext('link') for node in all_nodes} == expected_originals
    assert {node.findtext('link') for node in priority_nodes} == expected_priority_originals
    # Article-feed canonicals must remain the original sources.  The temporary
    # Chinese URL belongs only in content:encoded.
    for node in all_nodes + priority_nodes:
        canonical = node.findtext('link')
        body = node.findtext(content_tag) or ''
        links = translation_pattern.findall(body)
        assert canonical in expected_originals
        assert canonical not in links
        assert len(links) == 1
        assert links[0] in expected_translations
        assert '阅读中文全文' in body
        assert '可获取内容的中文译文' not in body
    assert len(report_nodes) == len(brief_nodes) == 1
    report_links = set(translation_pattern.findall(report_nodes[0].findtext(content_tag) or ''))
    brief_links = set(translation_pattern.findall(brief_nodes[0].findtext(content_tag) or ''))
    assert report_links == brief_links == expected_translations
    assert set(translation_pattern.findall(full)) == expected_translations
    assert set(translation_pattern.findall(brief)) == expected_translations
    descriptions = [node.findtext('description') or '' for node in all_nodes]
    assert descriptions and all(text.count('。') == 1 for text in descriptions)

    # Presentation-only repair may rebuild an active edition but must never
    # alter the processing watermark or bootstrap lifecycle.
    watermark_before = (state / 'last-success.json').read_bytes()
    bootstrap_before = (root / 'bootstrap.json').read_bytes()
    repaired = subprocess.run(
        [sys.executable, str(PUBLISH), str(FIXTURE / 'stage.json'),
         '--translation-manifest', str(manifest), '--presentation-only'],
        env=env, text=True, capture_output=True,
    )
    assert repaired.returncode == 0, repaired.stderr
    repaired_output = json.loads(repaired.stdout)
    assert repaired_output['presentationOnly'] is True
    assert repaired_output['watermarkUpdated'] is False
    assert (state / 'last-success.json').read_bytes() == watermark_before
    assert (root / 'bootstrap.json').read_bytes() == bootstrap_before

    # XML 1.0 allowlisting removes forbidden noncharacters and lone surrogates,
    # not only the familiar C0 controls.
    forbidden = '\x01\ufffe\uffff\ud800'
    cleaned = publisher.xml_safe('A' + forbidden + 'B')
    assert cleaned == 'AB'
    ET.fromstring(publisher.rss('A' + forbidden + 'B', 'https://example.test/', []))

    # Expired report links are removed together with their separator, leaving a
    # normal original-source bullet rather than "- · 查看原文".
    expired_report = {
        'markdown': f'- [阅读中文全文（保留 48 小时）]({feed_base}/translations/{rows[0]["publicFile"].split("/", 1)[1]}) · [查看原文](https://example.test/original)',
        'translationExpiresAt': '2000-01-01T00:00:00Z',
    }
    expired_markdown = publisher.renderable_report_markdown(expired_report)
    assert '- ·' not in expired_markdown
    assert expired_markdown == '- [查看原文](https://example.test/original)'

    def public_snapshot():
        paths = [pub / name for name in (
            'all.xml', 'priority.xml', 'reports.xml', 'brief.xml',
            '2026-08-15-morning.html', '2026-08-15-morning-brief.html',
        )]
        paths.append(state / 'last-success.json')
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def run_manifest(value, name):
        path = manifest_dir / f'{name}.json'
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.chmod(path, 0o600)
        return subprocess.run(
            [sys.executable, str(PUBLISH), str(FIXTURE / 'stage.json'),
             '--translation-manifest', str(path)],
            env=env, text=True, capture_output=True,
        )

    # Missing, limited, failed, extra, or duplicate accounting must never be
    # accepted as a publishable current feed.  Validation happens before any
    # public file or success watermark changes.
    before_rejections = public_snapshot()
    partial = json.loads(json.dumps(base_manifest))
    partial['items'] = partial['items'][:-1]
    partial['itemCount'] = len(partial['items'])
    rejected_partial = run_manifest(partial, 'partial')
    assert rejected_partial.returncode != 0, rejected_partial.stdout

    limited = json.loads(json.dumps(base_manifest))
    limited['items'][0]['status'] = 'limited'
    rejected_limited = run_manifest(limited, 'limited')
    assert rejected_limited.returncode != 0, rejected_limited.stdout

    failed = json.loads(json.dumps(base_manifest))
    failed['items'][0]['status'] = 'failure'
    rejected_failed = run_manifest(failed, 'failed')
    assert rejected_failed.returncode != 0, rejected_failed.stdout

    empty = json.loads(json.dumps(base_manifest))
    empty['items'] = []
    empty['itemCount'] = 0
    rejected_empty = run_manifest(empty, 'empty')
    assert rejected_empty.returncode != 0, rejected_empty.stdout

    duplicate = json.loads(json.dumps(base_manifest))
    duplicate['items'].append(dict(duplicate['items'][0]))
    duplicate['itemCount'] = len(duplicate['items'])
    rejected_duplicate = run_manifest(duplicate, 'duplicate')
    assert rejected_duplicate.returncode != 0, rejected_duplicate.stdout

    bad_count = json.loads(json.dumps(base_manifest))
    bad_count['itemCount'] -= 1
    rejected_count = run_manifest(bad_count, 'bad-count')
    assert rejected_count.returncode != 0, rejected_count.stdout

    bad_completeness = json.loads(json.dumps(base_manifest))
    bad_completeness['items'][0]['sourceCompleteness'] = 'summary'
    rejected_completeness = run_manifest(bad_completeness, 'bad-completeness')
    assert rejected_completeness.returncode != 0, rejected_completeness.stdout

    truncated = json.loads(json.dumps(base_manifest))
    truncated['items'][0]['truncated'] = True
    rejected_truncated = run_manifest(truncated, 'truncated')
    assert rejected_truncated.returncode != 0, rejected_truncated.stdout

    assert public_snapshot() == before_rejections

    # The publisher independently enforces an exact 48-hour retention window.
    bad = json.loads(json.dumps(base_manifest))
    bad['expiresAt'] = (saved + timedelta(hours=49)).isoformat().replace('+00:00', 'Z')
    bad_manifest = manifest_dir / 'bad-expiry.json'
    bad_manifest.write_text(json.dumps(bad, ensure_ascii=False), encoding='utf-8')
    os.chmod(bad_manifest, 0o600)
    rejected = subprocess.run(
        [sys.executable, str(PUBLISH), str(FIXTURE / 'stage.json'),
         '--translation-manifest', str(bad_manifest)],
        env=env, text=True, capture_output=True,
    )
    assert rejected.returncode != 0 and 'EXPIRY_NOT_48H' in rejected.stderr

    def expect_manifest_rejected(name, mutate, marker):
        value = json.loads(json.dumps(base_manifest))
        mutate(value)
        path = manifest_dir / f'{name}.json'
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.chmod(path, 0o600)
        result = subprocess.run(
            [sys.executable, str(PUBLISH), str(FIXTURE / 'stage.json'),
             '--translation-manifest', str(path)],
            env=env, text=True, capture_output=True,
        )
        assert result.returncode != 0 and marker in result.stderr, (name, result.stderr)

    def mismatch_generated(value):
        value['generatedAt'] = (saved + timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')

    expect_manifest_rejected(
        'bad-generated-at', mismatch_generated,
        'TRANSLATION_MANIFEST_GENERATED_AT_MISMATCH',
    )

    def future_manifest(value):
        future_saved = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=10)
        future_expires = future_saved + timedelta(hours=48)
        value['generatedAt'] = value['savedAt'] = future_saved.isoformat().replace('+00:00', 'Z')
        value['expiresAt'] = future_expires.isoformat().replace('+00:00', 'Z')
        for row in value['items']:
            row['savedAt'] = value['savedAt']
            row['expiresAt'] = value['expiresAt']

    expect_manifest_rejected(
        'bad-future-saved-at', future_manifest,
        'TRANSLATION_MANIFEST_SAVED_AT_IN_FUTURE',
    )

    def mismatched_row_saved(value):
        value['items'][0]['savedAt'] = (saved + timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')

    expect_manifest_rejected(
        'bad-row-saved-at', mismatched_row_saved,
        'TRANSLATION_ROW_TIME_MISMATCH',
    )

    def mismatched_row_expiry(value):
        value['items'][0]['expiresAt'] = (expires - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')

    expect_manifest_rejected(
        'bad-row-expiry', mismatched_row_expiry,
        'TRANSLATION_ROW_TIME_MISMATCH',
    )

    # Reprojection is the cleanup-side projection primitive.  One instant
    # before expiry every current item remains fully readable; at exactly 48h
    # all four feeds drop the expired edition rather than exposing dead links.
    # It must not advance processing state or commit bootstrap changes.
    state_before_reproject = (state / 'last-success.json').read_bytes()
    bootstrap_before_reproject = (root / 'bootstrap.json').read_bytes()

    def reproject(at):
        repro_env = dict(env)
        repro_env['RSS_NOW_ISO'] = at.isoformat().replace('+00:00', 'Z')
        return subprocess.run(
            [sys.executable, str(PUBLISH), '--reconcile-expired'],
            env=repro_env, text=True, capture_output=True,
        )

    just_before = reproject(expires - timedelta(microseconds=1))
    assert just_before.returncode == 0, just_before.stderr
    for name in ('all.xml', 'priority.xml'):
        nodes = ET.parse(pub / name).findall('./channel/item')
        assert nodes
        assert all(len(translation_pattern.findall(node.findtext(content_tag) or '')) == 1 for node in nodes)
    for name in ('reports.xml', 'brief.xml'):
        nodes = ET.parse(pub / name).findall('./channel/item')
        assert len(nodes) == 1
        assert set(translation_pattern.findall(nodes[0].findtext(content_tag) or '')) == expected_translations

    at_expiry = reproject(expires)
    assert at_expiry.returncode == 0, at_expiry.stderr
    for name in ('all.xml', 'priority.xml', 'reports.xml', 'brief.xml'):
        assert ET.parse(pub / name).findall('./channel/item') == []
        assert '/translations/' not in (pub / name).read_text(encoding='utf-8')
    assert (state / 'last-success.json').read_bytes() == state_before_reproject
    assert (root / 'bootstrap.json').read_bytes() == bootstrap_before_reproject

    after_expiry = reproject(expires + timedelta(microseconds=1))
    assert after_expiry.returncode == 0, after_expiry.stderr
    for name in ('all.xml', 'priority.xml', 'reports.xml', 'brief.xml'):
        assert ET.parse(pub / name).findall('./channel/item') == []
    print('HOST_PUBLISH_V53_FIXTURES_OK')
