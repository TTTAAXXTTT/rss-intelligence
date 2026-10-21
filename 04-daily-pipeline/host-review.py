#!/usr/bin/env python3
"""Deterministic, read-only FreshRSS subscription health review.

The script intentionally does not ask a model to create the final review.  It
reads the immutable review-run marker, performs one FreshRSS subscription-list
read, and writes a pending/no-change review that is compatible with the
rss-curator review store.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path


RUN_FILE = Path(os.environ.get(
    'RSS_REVIEW_RUN_FILE',
    '/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current/review-run.json',
))
REVIEWS_ROOT = Path(os.environ.get(
    'RSS_REVIEWS_DIR',
    '/home/ubuntu/.openclaw/rss-intelligence/reviews',
))

PRODUCER = 'host-deterministic-review-v1'
POLICY_VERSION = 'host-review-policy-v1'
SAFE_RUN_ID = re.compile(r'^review-[A-Za-z0-9._:-]{1,150}$')
RUN_TIMESTAMP = re.compile(r'^review-(\d{8}T\d{6}Z)(?:-|$)')
CONTROL_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
ALLOWED_KINDS = {'e2e', 'semi-monthly'}
CANONICAL_CATEGORIES = (
    'S-链上金融',
    'S-跨境金融',
    'S-商业机会',
    'A-AI-Agent',
    'A-AI工具自动化',
    'A-微信企微私域',
    'A-AIxCryptoPayment',
    'B-海外互联网',
    'B-开源自托管',
    'B-政策监管',
    'C-宏观流动性',
    'C-消费科技',
)
MAX_RUN_BYTES = 16_384
MAX_REVIEW_BYTES = 1_000_000
MAX_SUBSCRIPTIONS = 10_000


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def sha256_json(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def iso_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def bounded_text(value, name, maximum, allow_empty=False):
    if not isinstance(value, str):
        raise RuntimeError(f'{name} must be a string')
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise RuntimeError(f'{name} must not be empty')
    if len(normalized) > maximum:
        raise RuntimeError(f'{name} exceeds {maximum} characters')
    if CONTROL_TEXT.search(normalized):
        raise RuntimeError(f'{name} contains control characters')
    return normalized


def read_json(path, maximum):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f'{path.name} must be a regular file')
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise RuntimeError(f'{path.name} size is invalid')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'{path.name} is not valid UTF-8 JSON') from exc


def ensure_reviews_root():
    REVIEWS_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if REVIEWS_ROOT.is_symlink() or not REVIEWS_ROOT.is_dir():
        raise RuntimeError('reviews root must be a real directory')
    os.chmod(REVIEWS_ROOT, 0o700)


def atomic_create_json(path, value):
    """Publish a complete 0600 file atomically without overwriting a peer."""
    path = Path(path)
    if path.parent.resolve() != REVIEWS_ROOT.resolve():
        raise RuntimeError('review output escaped reviews root')
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
        # A hard-link of a fully written inode is an atomic create and refuses
        # to replace an existing deterministic artifact.
        os.link(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_run():
    raw = RUN_FILE.read_bytes() if RUN_FILE.is_file() and not RUN_FILE.is_symlink() else b''
    if not raw or len(raw) > MAX_RUN_BYTES:
        raise RuntimeError('review-run.json is missing or has invalid size')
    try:
        run = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('review-run.json is not valid UTF-8 JSON') from exc
    if not isinstance(run, dict) or run.get('schemaVersion') != 2:
        raise RuntimeError('review-run.json schemaVersion must be 2')
    run_id = bounded_text(run.get('runId'), 'runId', 160)
    if not SAFE_RUN_ID.fullmatch(run_id):
        raise RuntimeError('runId contains unsafe characters')
    review_kind = bounded_text(run.get('reviewKind'), 'reviewKind', 32)
    if review_kind not in ALLOWED_KINDS:
        raise RuntimeError('reviewKind is invalid')
    review_date = bounded_text(run.get('reviewDate'), 'reviewDate', 10)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', review_date):
        raise RuntimeError('reviewDate must use YYYY-MM-DD')
    try:
        if date.fromisoformat(review_date).isoformat() != review_date:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError('reviewDate is not a real calendar date') from exc
    normalized = {
        'schemaVersion': 2,
        'runId': run_id,
        'reviewDate': review_date,
        'reviewKind': review_kind,
    }
    return normalized, hashlib.sha256(raw).hexdigest()


def read_subscriptions():
    """Perform exactly one read of the FreshRSS subscription-list endpoint.

    Tests may monkeypatch this zero-argument function.  Credentials and the
    short-lived authentication token remain local variables and are never
    returned, logged, or persisted.
    """
    try:
        from daily_common import fresh_config, get_json, login_freshrss

        api, user, password = fresh_config()
        authentication = login_freshrss(api, user, password)
        payload = get_json(
            api + '/reader/api/0/subscription/list?output=json',
            authentication,
        )
    except Exception as exc:
        raise RuntimeError('FRESHRSS_SUBSCRIPTION_READ_FAILED') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('subscriptions'), list):
        raise RuntimeError('FRESHRSS_SUBSCRIPTION_SCHEMA_INVALID')
    subscriptions = []
    for raw in payload['subscriptions']:
        if not isinstance(raw, dict):
            raise RuntimeError('FRESHRSS_SUBSCRIPTION_ROW_INVALID')
        categories = []
        for category in raw.get('categories') or []:
            if isinstance(category, dict):
                label = category.get('label')
            else:
                label = category
            if isinstance(label, str):
                categories.append(label)
        subscriptions.append({
            'id': raw.get('id'),
            'title': raw.get('title') or '',
            'categories': categories,
        })
    return subscriptions


def normalize_subscriptions(raw_subscriptions):
    if not isinstance(raw_subscriptions, list):
        raise RuntimeError('subscriptions must be a list')
    if not 1 <= len(raw_subscriptions) <= MAX_SUBSCRIPTIONS:
        raise RuntimeError('subscription count is outside the safe range')
    normalized = []
    seen_ids = set()
    for index, raw in enumerate(raw_subscriptions):
        if not isinstance(raw, dict):
            raise RuntimeError(f'subscriptions[{index}] must be an object')
        subscription_id = bounded_text(raw.get('id'), f'subscriptions[{index}].id', 4096)
        if subscription_id in seen_ids:
            raise RuntimeError('duplicate FreshRSS subscription id')
        seen_ids.add(subscription_id)
        title = bounded_text(
            raw.get('title', ''),
            f'subscriptions[{index}].title',
            500,
            allow_empty=True,
        )
        raw_categories = raw.get('categories')
        if raw_categories is None:
            raw_categories = []
        if not isinstance(raw_categories, list) or len(raw_categories) > 100:
            raise RuntimeError(f'subscriptions[{index}].categories must be a bounded list')
        categories = []
        seen_categories = set()
        for number, raw_category in enumerate(raw_categories):
            if isinstance(raw_category, dict):
                raw_category = raw_category.get('label')
            category = bounded_text(
                raw_category,
                f'subscriptions[{index}].categories[{number}]',
                160,
            )
            if category not in seen_categories:
                seen_categories.add(category)
                categories.append(category)
        normalized.append({
            'id': subscription_id,
            'title': title,
            'categories': sorted(categories),
        })
    normalized.sort(key=lambda row: (row['id'], row['title']))
    return normalized


def subscription_stats(subscriptions):
    counts = {category: 0 for category in CANONICAL_CATEGORIES}
    uncategorized = 0
    for subscription in subscriptions:
        categories = subscription['categories']
        if not categories:
            uncategorized += 1
        for category in categories:
            if category in counts:
                counts[category] += 1
    return {
        'total': len(subscriptions),
        'uncategorized': uncategorized,
        'categories': counts,
    }


def build_summary(run, stats, inventory_hash):
    kind = run['reviewKind']
    lines = [
        f"REVIEW_RUN_ID={run['runId']}",
        f'REVIEW_KIND={kind}',
        'HOST_DETERMINISTIC_REVIEW=true',
        f"E2E_TEST_ONLY={'true' if kind == 'e2e' else 'false'}",
    ]
    if kind == 'semi-monthly':
        lines.append('DISCOVERY_DEFERRED=true')
    lines.extend([
        '',
        f"# FreshRSS 订阅健康审核 · {run['reviewDate']}",
        '',
        '本报告由主机端确定性只读流程生成；未修改 FreshRSS，未执行任何订阅变更。',
        '',
        '### Current subscriptions overview',
        '',
        f"- 订阅总数：{stats['total']}",
        f"- 未分类订阅：{stats['uncategorized']}",
        f'- 订阅库存摘要：`sha256:{inventory_hash}`',
        '',
        '#### 分类统计',
        '',
    ])
    for category in CANONICAL_CATEGORIES:
        lines.append(f"- {category}：{stats['categories'][category]}")
    if kind == 'e2e':
        lines.extend([
            '',
            '### E2E result',
            '',
            '- FreshRSS 只读订阅列表读取成功。',
            '- Pending Review 原子保存成功。',
            '- 本次为管线烟雾测试，不生成 ADD、REMOVE 或 MOVE 提案。',
        ])
    else:
        lines.extend([
            '',
            '### ADD',
            '',
            '本次确定性健康审核不执行外部来源发现，因此无 ADD 提案。',
            '',
            '### KEEP',
            '',
            f"FreshRSS 当前保存 {stats['total']} 个订阅；本报告仅确认库存与分类结构可读。",
            '',
            '### WATCH',
            '',
            f"未分类订阅数量：{stats['uncategorized']}。如非零，应在人工审核中检查分类。",
            '',
            '### REMOVE',
            '',
            '无。主机端健康审核不会仅凭结构统计提出删除。',
            '',
            '### MOVE',
            '',
            '无。分类移动需结合内容质量并由人工确认。',
            '',
            '### Risks and uncertainty',
            '',
            '- 本流程不做开放网络的新来源发现，也不对文章内容作主观质量判断。',
            '- 本报告是 Pending Review，不代表批准，不会触发 FreshRSS 变更。',
        ])
    lines.extend([
        '',
        '### Review statistics',
        '',
        f"- reviewKind：{kind}",
        f"- subscriptions：{stats['total']}",
        '- structured changes：0',
        '- modifiesFreshRss：false',
    ])
    summary = '\n'.join(lines).strip() + '\n'
    if len(summary) > 100_000:
        raise RuntimeError('generated summary exceeds review-store limit')
    return summary


def generated_at_for_run(run):
    match = RUN_TIMESTAMP.match(run['runId'])
    if match:
        try:
            parsed = datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace('+00:00', 'Z')
        except ValueError:
            pass
    return f"{run['reviewDate']}T00:00:00Z"


def input_hash_for(run, run_file_hash, inventory_hash, stats):
    return sha256_json({
        'producer': PRODUCER,
        'policyVersion': POLICY_VERSION,
        'runId': run['runId'],
        'reviewKind': run['reviewKind'],
        'reviewDate': run['reviewDate'],
        'runFileSha256': run_file_hash,
        'subscriptionInventorySha256': inventory_hash,
        'subscriptionStats': stats,
    })


def artifact_hash(review):
    payload = dict(review)
    payload.pop('artifactHash', None)
    return sha256_json(payload)


def review_filename(generated_at, review_id):
    safe_timestamp = generated_at.replace(':', '-').replace('.', '-')
    return f'{safe_timestamp}-pending-{review_id}.json'


def matching_artifacts(run):
    marker = f"REVIEW_RUN_ID={run['runId']}"
    found = []
    for path in REVIEWS_ROOT.glob('*.json'):
        try:
            value = read_json(path, MAX_REVIEW_BYTES)
        except RuntimeError:
            continue
        if not isinstance(value, dict):
            continue
        summary = value.get('summaryMarkdown')
        has_marker = isinstance(summary, str) and marker in summary.splitlines()
        if value.get('runId') == run['runId'] or has_marker:
            found.append((path, value))
    return found


def validate_stats(value):
    if not isinstance(value, dict) or set(value) != {'total', 'uncategorized', 'categories'}:
        raise RuntimeError('stored subscriptionStats schema is invalid')
    total = value.get('total')
    uncategorized = value.get('uncategorized')
    categories = value.get('categories')
    if not isinstance(total, int) or not 1 <= total <= MAX_SUBSCRIPTIONS:
        raise RuntimeError('stored subscription total is invalid')
    if not isinstance(uncategorized, int) or not 0 <= uncategorized <= total:
        raise RuntimeError('stored uncategorized count is invalid')
    if not isinstance(categories, dict) or set(categories) != set(CANONICAL_CATEGORIES):
        raise RuntimeError('stored category stats are invalid')
    if any(not isinstance(count, int) or not 0 <= count <= total for count in categories.values()):
        raise RuntimeError('stored category count is invalid')
    return value


def validate_artifact(path, review, run, run_file_hash):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('stored review is not a regular file')
    if path.parent.resolve() != REVIEWS_ROOT.resolve():
        raise RuntimeError('stored review escaped reviews root')
    if os.name == 'posix' and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError('stored review permissions are not 0600')
    if not isinstance(review, dict):
        raise RuntimeError('stored review must be an object')
    required = {
        'schemaVersion': 1,
        'status': 'pending',
        'producer': PRODUCER,
        'policyVersion': POLICY_VERSION,
        'runId': run['runId'],
        'reviewKind': run['reviewKind'],
        'reviewDate': run['reviewDate'],
        'runFileSha256': run_file_hash,
        'changes': [],
        'modifiesFreshRss': False,
        'appliesChanges': False,
    }
    for key, expected in required.items():
        if review.get(key) != expected:
            raise RuntimeError(f'stored review {key} gate failed')
    inventory_hash = review.get('subscriptionInventorySha256')
    if not isinstance(inventory_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', inventory_hash):
        raise RuntimeError('stored inventory hash is invalid')
    stats = validate_stats(review.get('subscriptionStats'))
    expected_input_hash = input_hash_for(run, run_file_hash, inventory_hash, stats)
    if review.get('inputHash') != expected_input_hash:
        raise RuntimeError('stored review input hash mismatch')
    expected_review_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'rss-host-review:{expected_input_hash}'))
    if review.get('reviewId') != expected_review_id:
        raise RuntimeError('stored reviewId mismatch')
    generated_at = generated_at_for_run(run)
    if review.get('generatedAt') != generated_at:
        raise RuntimeError('stored generatedAt mismatch')
    saved_at = review.get('savedAt')
    if not isinstance(saved_at, str) or len(saved_at) > 64:
        raise RuntimeError('stored savedAt is invalid')
    try:
        datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
    except ValueError as exc:
        raise RuntimeError('stored savedAt is invalid') from exc
    expected_summary = build_summary(run, stats, inventory_hash)
    if review.get('summaryMarkdown') != expected_summary:
        raise RuntimeError('stored summary does not match deterministic template')
    if review.get('artifactHash') != artifact_hash(review):
        raise RuntimeError('stored artifact hash mismatch')
    if path.name != review_filename(generated_at, expected_review_id):
        raise RuntimeError('stored review filename mismatch')
    return path


def build_artifact(run, run_file_hash, subscriptions):
    stats = subscription_stats(subscriptions)
    inventory_hash = sha256_json(subscriptions)
    input_hash = input_hash_for(run, run_file_hash, inventory_hash, stats)
    review_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'rss-host-review:{input_hash}'))
    generated_at = generated_at_for_run(run)
    review = {
        'schemaVersion': 1,
        'reviewId': review_id,
        'status': 'pending',
        'reviewDate': run['reviewDate'],
        'generatedAt': generated_at,
        'savedAt': iso_now(),
        'summaryMarkdown': build_summary(run, stats, inventory_hash),
        'changes': [],
        'producer': PRODUCER,
        'policyVersion': POLICY_VERSION,
        'runId': run['runId'],
        'reviewKind': run['reviewKind'],
        'runFileSha256': run_file_hash,
        'subscriptionInventorySha256': inventory_hash,
        'inputHash': input_hash,
        'subscriptionStats': stats,
        'modifiesFreshRss': False,
        'appliesChanges': False,
    }
    review['artifactHash'] = artifact_hash(review)
    return review, REVIEWS_ROOT / review_filename(generated_at, review_id)


def finalize(reuse=False):
    run, run_file_hash = load_run()
    ensure_reviews_root()
    existing = matching_artifacts(run)
    if len(existing) > 1:
        raise RuntimeError('multiple review artifacts match this run')
    if existing:
        path, review = existing[0]
        if not reuse:
            raise RuntimeError('review artifact already exists; pass --reuse')
        return validate_artifact(path, review, run, run_file_hash)

    subscriptions = normalize_subscriptions(read_subscriptions())
    review, path = build_artifact(run, run_file_hash, subscriptions)
    try:
        atomic_create_json(path, review)
    except FileExistsError:
        if not reuse:
            raise RuntimeError('deterministic review artifact already exists') from None
        stored = read_json(path, MAX_REVIEW_BYTES)
        return validate_artifact(path, stored, run, run_file_hash)
    return validate_artifact(path, review, run, run_file_hash)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('review_kind', choices=sorted(ALLOWED_KINDS))
    parser.add_argument('--reuse', action='store_true')
    args = parser.parse_args()
    run, _ = load_run()
    if run['reviewKind'] != args.review_kind:
        raise RuntimeError('CLI review kind does not match review-run.json')
    path = finalize(reuse=args.reuse)
    print(f'HOST_REVIEW_PATH={path.resolve()}')


if __name__ == '__main__':
    main()
