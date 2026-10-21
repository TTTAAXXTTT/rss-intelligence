#!/usr/bin/env python3
"""Offline fixtures for deterministic Host Review V5.2."""

import contextlib
import copy
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
HOST_REVIEW = HERE / 'host-review.py'
if not HOST_REVIEW.exists():
    HOST_REVIEW = HERE.parent / '04-daily-pipeline' / 'host-review.py'
if not HOST_REVIEW.exists():
    raise SystemExit(f'HOST_REVIEW_IMPLEMENTATION_MISSING path={HOST_REVIEW}')

spec = importlib.util.spec_from_file_location('host_review', HOST_REVIEW)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    SHANGHAI = ZoneInfo('Asia/Shanghai')
except Exception:
    # Some minimal Windows Python installs omit the IANA tzdata package.
    SHANGHAI = timezone(timedelta(hours=8))
    if hasattr(module, 'ZoneInfo'):
        module.ZoneInfo = lambda _name: SHANGHAI


CANONICAL_CATEGORIES = (
    'S-链上金融', 'S-跨境金融', 'S-商业机会',
    'A-AI-Agent', 'A-AI工具自动化', 'A-微信企微私域', 'A-AIxCryptoPayment',
    'B-海外互联网', 'B-开源自托管', 'B-政策监管',
    'C-宏观流动性', 'C-消费科技',
)


def subscription(number, title, category=None, feed_url=None):
    feed_url = feed_url or f'https://feeds.example.com/{number}.xml'
    return {
        'id': f'feed/{feed_url}',
        'title': title,
        'htmlUrl': f'https://sites.example.com/{number}',
        'categories': [] if category is None else [{
            'id': f'user/-/label/{category}',
            'label': category,
        }],
    }


SUBSCRIPTIONS = [
    subscription('chain', 'Chain Official', 'S-链上金融'),
    subscription('agent', 'Agent Engineering', 'A-AI-Agent'),
    subscription('global', 'Global Product', 'B-海外互联网'),
    subscription('consumer', 'Consumer Technology', 'C-消费科技'),
    subscription('uncategorized', 'Uncategorized Source'),
]


class OfflineFreshRssStub:
    def __init__(self, subscriptions):
        self.subscriptions = subscriptions
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return copy.deepcopy(self.subscriptions)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def current_review_date():
    return datetime.now(SHANGHAI).strftime('%Y-%m-%d')


def run_id(suffix):
    return f'review-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{suffix}-9001'


def install_run(path, wanted_run_id, kind, date=None):
    value = {
        'schemaVersion': 2,
        'runId': wanted_run_id,
        'reviewDate': date or current_review_date(),
        'reviewKind': kind,
    }
    write_json(path, value)
    return value


def read_review(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def assert_review_contract(path, run, expected_kind):
    value = read_review(path)
    assert value['schemaVersion'] == 1
    assert value['status'] == 'pending'
    assert value['runId'] == run['runId']
    assert value['reviewKind'] == expected_kind
    assert value['reviewDate'] == run['reviewDate']
    assert value['producer'] == 'host-deterministic-review-v1'
    parsed_uuid = uuid.UUID(value['reviewId'])
    assert parsed_uuid.version == 5
    assert value['changes'] == []
    summary = value['summaryMarkdown']
    assert summary.count(f'REVIEW_RUN_ID={run["runId"]}') == 1
    assert summary.count(f'REVIEW_KIND={expected_kind}') == 1
    assert 1 <= len(summary) <= 100_000
    return value


def assert_classification_statistics(value):
    expected = {
        'S-链上金融': 1,
        'A-AI-Agent': 1,
        'B-海外互联网': 1,
        'C-消费科技': 1,
    }
    stats = value.get('subscriptionStats') or value.get('statistics')
    if isinstance(stats, dict):
        assert int(stats.get('total', -1)) == 5
        assert int(stats.get('uncategorized', -1)) == 1
        categories = stats.get('categories')
        assert isinstance(categories, dict)
        for category in CANONICAL_CATEGORIES:
            assert int(categories.get(category, 0)) == expected.get(category, 0)
        return

    # A producer may keep statistics only in the human-readable artifact.
    # Require unambiguous category/count and total/uncategorized lines.
    summary = value['summaryMarkdown']
    assert re.search(r'(?m)^(?:SUBSCRIPTIONS_TOTAL|TOTAL_SUBSCRIPTIONS)=5\s*$', summary)
    assert re.search(r'(?m)^(?:UNCATEGORIZED|UNCATEGORIZED_SUBSCRIPTIONS)=1\s*$', summary)
    for category, count in expected.items():
        assert re.search(rf'(?m)^{re.escape(category)}\s*[=:：]\s*{count}\s*$', summary)


def cli_reuse(kind):
    old_argv = sys.argv[:]
    output = io.StringIO()
    try:
        sys.argv = ['host-review.py', kind, '--reuse']
        with contextlib.redirect_stdout(output):
            result = module.main()
    finally:
        sys.argv = old_argv
    text = output.getvalue()
    match = re.search(r'(?m)^HOST_REVIEW_PATH=(.+)\s*$', text)
    assert match, text
    return Path(match.group(1).strip()), result


def expect_fail(run_file, reviews_root, value, expected_error, stub, subscriptions=None):
    before = {path.name for path in reviews_root.glob('*.json')}
    calls_before = stub.calls
    if subscriptions is not None:
        stub.subscriptions = subscriptions
    write_json(run_file, value)
    try:
        module.finalize(reuse=False)
    except (RuntimeError, ValueError) as exc:
        assert str(exc) == expected_error, (str(exc), expected_error)
    else:
        raise AssertionError(f'expected fail-closed error: {expected_error}')
    after = {path.name for path in reviews_root.glob('*.json')}
    assert after == before
    return calls_before


with tempfile.TemporaryDirectory(prefix='rss-v52-host-review-') as temp:
    root = Path(temp)
    run_file = root / 'current' / 'review-run.json'
    reviews = root / 'reviews'
    module.RUN_FILE = run_file
    module.REVIEWS_ROOT = reviews
    stub = OfflineFreshRssStub(SUBSCRIPTIONS)
    module.read_subscriptions = stub

    # E2E is an offline pipeline smoke artifact: one subscription read, no
    # proposals, and each exact marker appears once.
    e2e_run = install_run(run_file, run_id('1001'), 'e2e')
    e2e_path = Path(module.finalize(reuse=False))
    e2e = assert_review_contract(e2e_path, e2e_run, 'e2e')
    assert e2e['summaryMarkdown'].count('E2E_TEST_ONLY=true') == 1
    assert stub.calls == 1
    assert_classification_statistics(e2e)
    print('PASS host_review_e2e_markers_empty_changes_single_read_statistics')

    # --reuse is a true immutable reuse: UUID5 path, bytes, mtime and the
    # FreshRSS read count all remain unchanged.
    e2e_bytes = e2e_path.read_bytes()
    e2e_mtime = e2e_path.stat().st_mtime_ns
    reused_path, _ = cli_reuse('e2e')
    assert reused_path.resolve() == e2e_path.resolve()
    assert e2e_path.read_bytes() == e2e_bytes
    assert e2e_path.stat().st_mtime_ns == e2e_mtime
    assert stub.calls == 1
    assert len(list(reviews.glob('*.json'))) == 1
    assert not list(reviews.glob('.*.tmp'))
    if os.name == 'posix':
        assert stat.S_IMODE(reviews.stat().st_mode) == 0o700
        assert stat.S_IMODE(e2e_path.stat().st_mode) == 0o600
    print('PASS host_review_uuid5_cli_reuse_bytes_mtime_permissions')

    # Semi-monthly remains a Pending Review snapshot. The deterministic Host
    # producer only inventories/classifies current sources and never invents
    # add/remove/move proposals without an evidence-gathering workflow.
    semi_run = install_run(run_file, run_id('1002'), 'semi-monthly')
    semi_path = Path(module.finalize(reuse=False))
    semi = assert_review_contract(semi_path, semi_run, 'semi-monthly')
    assert 'E2E_TEST_ONLY=true' not in semi['summaryMarkdown']
    assert semi['summaryMarkdown'].count('DISCOVERY_DEFERRED=true') == 1
    assert stub.calls == 2
    assert_classification_statistics(semi)
    for heading in (
        '### Current subscriptions overview', '### ADD', '### KEEP',
        '### WATCH', '### REMOVE', '### MOVE',
        '### Risks and uncertainty', '### Review statistics',
    ):
        assert semi['summaryMarkdown'].count(heading) == 1
    print('PASS host_review_semi_monthly_inventory_statistics_empty_changes')

    # Validate run metadata before touching FreshRSS, and never leave a partial
    # artifact for unsafe run IDs, unknown kinds or impossible dates.
    valid_date = current_review_date()
    calls_before = stub.calls
    returned_calls = expect_fail(run_file, reviews, {
        'schemaVersion': 2,
        'runId': '../review-escape',
        'reviewDate': valid_date,
        'reviewKind': 'e2e',
    }, 'runId contains unsafe characters', stub)
    assert returned_calls == calls_before and stub.calls == calls_before

    returned_calls = expect_fail(run_file, reviews, {
        'schemaVersion': 2,
        'runId': run_id('1003'),
        'reviewDate': valid_date,
        'reviewKind': 'live-mutation',
    }, 'reviewKind is invalid', stub)
    assert returned_calls == calls_before and stub.calls == calls_before

    returned_calls = expect_fail(run_file, reviews, {
        'schemaVersion': 2,
        'runId': run_id('1004'),
        'reviewDate': '2026-02-30',
        'reviewKind': 'semi-monthly',
    }, 'reviewDate is not a real calendar date', stub)
    assert returned_calls == calls_before and stub.calls == calls_before
    print('PASS host_review_bad_run_kind_date_fail_before_read')

    # Duplicate FreshRSS subscription identity or raw feed URL is corruption,
    # not something the review may silently collapse.
    duplicate_id = copy.deepcopy(SUBSCRIPTIONS[:2])
    duplicate_id[1]['id'] = duplicate_id[0]['id']
    calls_before = stub.calls
    expect_fail(run_file, reviews, {
        'schemaVersion': 2,
        'runId': run_id('1005'),
        'reviewDate': valid_date,
        'reviewKind': 'semi-monthly',
    }, 'duplicate FreshRSS subscription id', stub, duplicate_id)
    assert stub.calls == calls_before + 1

    print('PASS host_review_duplicate_subscription_fail_closed')

print('HOST_REVIEW_FIXTURES_OK')
