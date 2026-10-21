#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
HOST_FINALIZE = HERE / 'host-finalize.py'
if not HOST_FINALIZE.exists():
    HOST_FINALIZE = HERE.parent / '04-daily-pipeline' / 'host-finalize.py'
spec = importlib.util.spec_from_file_location('host_finalize', HOST_FINALIZE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    SHANGHAI = ZoneInfo('Asia/Shanghai')
except Exception:
    # Minimal Windows Python installations may not bundle the IANA tzdata
    # package. Shanghai has no DST in the covered runtime, so UTC+08:00 is an
    # exact fixture substitute; also inject it into the imported module.
    SHANGHAI = timezone(timedelta(hours=8))
    module.ZoneInfo = lambda _name: SHANGHAI


PRIORITY_CATEGORY = {
    'S': 'S-商业机会',
    'A': 'A-AI-Agent',
    'B': 'B-海外互联网',
    'C': 'C-消费科技',
}


def candidate(article_id, event_key, index, priority='A', source_url=None, **overrides):
    value = {
        'primaryArticleId': article_id,
        'eventKeyCandidate': event_key,
        'title': f'候选 {index}',
        'originalTitle': f'Candidate {index}',
        'priority': priority,
        'category': PRIORITY_CATEGORY[priority],
        'summary': f'摘要 {index}',
        'whyImportant': f'重要性 {index}',
        'impact': f'影响 {index}',
        'isHighValueOpportunity': priority == 'S',
        'needsVerification': False,
        'publishedAt': f'2026-08-11T{index % 24:02d}:00:00Z',
        'sources': [{
            'name': 'Fixture',
            'url': source_url or f'https://example.com/{article_id}',
        }],
    }
    value.update(overrides)
    return value


def candidate_file(edition, run_id, candidates, baseline_id=None, baseline_found=None):
    snapshot_hash = hashlib.sha256(f'snapshot:{run_id}'.encode()).hexdigest()
    value = {
        'schemaVersion': 2,
        'edition': edition,
        'runId': run_id,
        'snapshotId': snapshot_hash[:32],
        'snapshotHash': snapshot_hash,
        'allPagesComplete': True,
        'preparedAt': '2026-08-11T09:00:00Z',
        'sourceFetchRunId': f'fetch-{run_id}',
        'sourceFetchFinishedAt': '2026-08-11T08:59:00Z',
        'normalCutoffTimestampSec': 1,
        'effectiveCutoffTimestampSec': 1,
        'cutoffTimestampSec': 1,
        'pageCount': 1,
        'totalRawArticles': len(candidates),
        'accountedRawArticles': len(candidates),
        'hostBootstrapBackfillArticles': 0,
        'persistentBootstrapQuarantineHits': 0,
        'newBootstrapQuarantineArticles': 0,
        'modelInputArticles': len(candidates),
        'modelBackfillArticles': 0,
        'modelFilteredLowValue': 0,
        'modelMergedDuplicates': 0,
        'candidateArticleCount': len(candidates),
        'preliminaryCandidates': len(candidates),
        'classifierBatchCount': 1 if candidates else 0,
        'bootstrapFeedIds': [],
        'candidates': candidates,
        'recoveryFromWatermark': False,
        'morningBaselineId': baseline_id,
        'morningBaselineFound': baseline_found,
    }
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def install_candidate(current, runs, value):
    """Install the complete immutable provenance graph consumed by Host Finalizer."""
    run_dir = (runs / value['runId']).resolve()
    raw_path = run_dir / 'raw-accounting.json'
    quarantine_path = run_dir / 'bootstrap-quarantine.json'
    value['rawAccountingFile'] = str(raw_path)
    value['bootstrapQuarantineFile'] = str(quarantine_path)
    raw = [
        {
            'id': row['primaryArticleId'],
            'disposition': 'candidate',
            'accountingSource': 'model',
        }
        for row in value['candidates']
    ]
    manifest = {
        'schemaVersion': 2,
        'edition': value['edition'],
        'runId': value['runId'],
        'snapshotId': value['snapshotId'],
        'snapshotHash': value['snapshotHash'],
        'sourceFetchRunId': value['sourceFetchRunId'],
        'allPagesComplete': True,
        'pageCount': value['pageCount'],
        'totalRawArticles': value['totalRawArticles'],
        'runDirectory': str(run_dir),
    }
    write(raw_path, raw)
    write(quarantine_path, [])
    write(run_dir / 'snapshot-manifest.json', manifest)
    # combined.json is the immutable copy and must exactly equal current.
    write(run_dir / 'combined.json', value)
    write(current / f'{value["edition"]}-candidates.json', value)
    return run_dir


def found_baseline(items):
    """Match prepare-daily.py/baseline_events byte-for-byte baselineId derivation."""
    payload = {
        'schemaVersion': 2,
        'found': True,
        'dateKey': datetime.now(SHANGHAI).strftime('%Y-%m-%d'),
        'title': '08:00 情报晨报',
        'reportMarkdown': '# 08:00 情报晨报\n\nFixture baseline.\n',
        'items': items,
        'publishedAt': '2026-08-11T01:00:00Z',
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    payload['baselineId'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
    return payload


def empty_baseline():
    return {
        'schemaVersion': 2,
        'found': False,
        'dateKey': datetime.now(SHANGHAI).strftime('%Y-%m-%d'),
        'baselineId': 'none',
        'items': [],
    }


def read_stage(path):
    return json.loads(path.read_text(encoding='utf-8'))


def assert_exact_accounting(stage, expected_ids):
    rows = stage['accounting']
    got = [row['primaryArticleId'] for row in rows]
    assert len(got) == len(expected_ids)
    assert len(got) == len(set(got))
    assert set(got) == set(expected_ids)
    item_keys = [item['eventKey'] for item in stage['items']]
    assert len(item_keys) == len(set(item_keys))
    retained_keys = set()
    for row in rows:
        if row['disposition'] in ('retained', 'merged'):
            assert row['finalEventKey'] in item_keys
            if row['disposition'] == 'retained':
                retained_keys.add(row['finalEventKey'])
        else:
            assert row['disposition'] == 'filtered'
            assert not row.get('finalEventKey')
    assert retained_keys == set(item_keys)


with tempfile.TemporaryDirectory(prefix='rss-v52-host-finalize-') as temp:
    root = Path(temp)
    current = root / 'current'
    stages = root / 'stages'
    runs = root / 'runs'
    module.CANDIDATE_ROOT = current
    module.STAGE_ROOT = stages
    module.RUNS_ROOT = runs

    # Exact eventKey duplicates and exact raw-URL duplicates both form one
    # deterministic event. URL comparison remains byte/case sensitive.
    duplicate_url = 'https://example.com/shared-source?x=1'
    morning_candidates = [
        candidate('m1', 'same-event', 1, priority='S'),
        candidate('m2', 'same-event', 2),
        candidate('m3', 'url-primary', 3, priority='S', source_url=duplicate_url),
        candidate('m4', 'url-alias', 4, source_url=duplicate_url),
        candidate('m5', 'case-sensitive-url', 5, source_url='https://EXAMPLE.com/shared-source?x=1'),
    ]
    morning = candidate_file('morning', 'morning-fixture-1', morning_candidates)
    install_candidate(current, runs, morning)
    first = module.finalize('morning')
    first_bytes = first.read_bytes()
    first_mtime = first.stat().st_mtime_ns
    stage = read_stage(first)
    assert len(stage['items']) == 3
    assert_exact_accounting(stage, [row['primaryArticleId'] for row in morning_candidates])
    dispositions = {row['primaryArticleId']: row['disposition'] for row in stage['accounting']}
    assert dispositions == {
        'm1': 'retained', 'm2': 'merged',
        'm3': 'retained', 'm4': 'merged',
        'm5': 'retained',
    }
    assert len({source['url'] for item in stage['items'] for source in item['sources']}) == 4

    # A direct retry must reuse, not rewrite or multiply, the immutable stage.
    second = module.finalize('morning')
    assert second == first
    assert second.read_bytes() == first_bytes
    assert second.stat().st_mtime_ns == first_mtime
    assert len(list((stages / 'morning').glob('*.json'))) == 1
    assert not list(stages.rglob('*.tmp'))
    if os.name == 'posix':
        assert stat.S_IMODE(stages.stat().st_mode) == 0o700
        assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(first.stat().st_mode) == 0o600
    print('PASS host_finalize_event_and_url_dedup_accounting_idempotency_permissions')

    # Morning cap is enforced by code: all 26 candidates remain accounted,
    # while only the deterministic top 24 events can be retained.
    capped = [
        candidate(f'cap-{index:02d}', f'cap-event-{index:02d}', index,
                  priority=('S' if index < 3 else 'A'))
        for index in range(26)
    ]
    capped_value = candidate_file('morning', 'morning-fixture-cap', capped)
    install_candidate(current, runs, capped_value)
    capped_stage = read_stage(module.finalize('morning'))
    assert len(capped_stage['items']) == 24
    assert len(capped_stage['reportMarkdown']) <= 12000
    assert_exact_accounting(capped_stage, [row['primaryArticleId'] for row in capped])
    assert sum(row['disposition'] == 'filtered' for row in capped_stage['accounting']) == 2
    assert {'cap-event-00', 'cap-event-01', 'cap-event-02'} <= {
        item['eventKey'] for item in capped_stage['items']
    }
    print('PASS host_finalize_morning_cap_with_full_accounting')

    # Found=true baseline ID uses the exact immutable payload hash. A matching
    # eventKey or an exact Morning source URL is no-progress; only a truly new
    # key+URL is retained. New wording/source for the same key is still filtered.
    baseline = found_baseline([
        {
            'eventKey': 'old-event',
            'title': '早间事件',
            'priority': 'S',
            'category': 'S-商业机会',
            'summary': '早间摘要',
            'whyImportant': '早间价值',
            'impact': '早间影响',
            'isHighValueOpportunity': True,
            'sources': [{'name': 'Morning', 'url': 'https://example.com/morning-old'}],
        },
    ])
    write(current / 'afternoon-morning-baseline.json', baseline)
    afternoon_candidates = [
        candidate('a1', 'old-event', 1, source_url='https://example.com/new-outlet'),
        candidate('a2', 'different-key-same-url', 2, source_url='https://example.com/morning-old'),
        candidate('a3', 'new-event', 3, priority='S', source_url='https://example.com/new-event'),
    ]
    afternoon = candidate_file(
        'afternoon', 'afternoon-fixture-1', afternoon_candidates,
        baseline_id=baseline['baselineId'], baseline_found=True,
    )
    install_candidate(current, runs, afternoon)
    afternoon_stage = read_stage(module.finalize('afternoon'))
    assert [item['eventKey'] for item in afternoon_stage['items']] == ['new-event']
    assert len(afternoon_stage['items']) <= 16
    assert len(afternoon_stage['reportMarkdown']) <= 8000
    assert afternoon_stage['morningBaselineId'] == baseline['baselineId']
    assert_exact_accounting(afternoon_stage, [row['primaryArticleId'] for row in afternoon_candidates])
    afternoon_dispositions = {
        row['primaryArticleId']: row['disposition'] for row in afternoon_stage['accounting']
    }
    assert afternoon_dispositions == {'a1': 'filtered', 'a2': 'filtered', 'a3': 'retained'}
    print('PASS host_finalize_afternoon_exact_baseline_and_no_progress_gate')

    # No same-day Morning report is a valid immutable baseline. An empty
    # Afternoon produces a publishable empty stage and explicit no-news text.
    empty = empty_baseline()
    write(current / 'afternoon-morning-baseline.json', empty)
    empty_value = candidate_file(
        'afternoon', 'afternoon-fixture-empty', [],
        baseline_id='none', baseline_found=False,
    )
    install_candidate(current, runs, empty_value)
    empty_path = module.finalize('afternoon')
    empty_stage = read_stage(empty_path)
    assert empty_stage['items'] == []
    assert empty_stage['accounting'] == []
    assert empty_stage['morningBaselineId'] == 'none'
    assert '本时段没有达到重点标准的新情报。' in empty_stage['reportMarkdown']
    assert '本次没有可用的同日 Morning baseline' in empty_stage['reportMarkdown']
    assert len(empty_stage['reportMarkdown']) <= 8000
    assert module.finalize('afternoon') == empty_path
    assert len([
        path for path in (stages / 'afternoon').glob('afternoon-fixture-empty-*.json')
    ]) == 1
    print('PASS host_finalize_empty_afternoon_idempotent_stage')

    # Strict provenance and baseline hash mismatches fail closed before a stage
    # can be created.
    invalid = candidate_file('morning', 'morning-fixture-invalid', [
        candidate('dup', 'event-1', 1),
        candidate('dup', 'event-2', 2),
    ])
    install_candidate(current, runs, invalid)
    try:
        module.finalize('morning')
    except RuntimeError as exc:
        assert str(exc) == 'HOST_FINAL_CANDIDATE_IDS_NOT_UNIQUE'
    else:
        raise AssertionError('duplicate candidate IDs must fail')
    assert not any(
        'morning-fixture-invalid' in path.name
        for path in (stages / 'morning').glob('*.json')
    )

    bad_baseline = found_baseline([])
    bad_baseline['baselineId'] = '0' * 32
    write(current / 'afternoon-morning-baseline.json', bad_baseline)
    bad_value = candidate_file(
        'afternoon', 'afternoon-fixture-bad-baseline', [],
        baseline_id=bad_baseline['baselineId'], baseline_found=True,
    )
    install_candidate(current, runs, bad_value)
    try:
        module.finalize('afternoon')
    except RuntimeError as exc:
        assert str(exc) == 'AFTERNOON_BASELINE_HASH_MISMATCH'
    else:
        raise AssertionError('bad baseline hash must fail')
    assert not any(
        'afternoon-fixture-bad-baseline' in path.name
        for path in (stages / 'afternoon').glob('*.json')
    )
    print('PASS host_finalize_provenance_and_baseline_fail_closed')

print('HOST_FINALIZE_FIXTURES_OK')
