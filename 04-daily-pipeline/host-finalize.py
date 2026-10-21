#!/usr/bin/env python3
"""Deterministic Host Finalizer for validated V5.2 classifier candidates."""

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


CANDIDATE_ROOT = Path(os.environ.get(
    'RSS_DAILY_STAGE_CURRENT',
    '/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current',
))
STAGE_ROOT = Path(os.environ.get(
    'RSS_FINALIZER_STAGE_ROOT',
    '/home/ubuntu/.openclaw/rss-intelligence/finalizer-stage-v52',
))
RUNS_ROOT = Path(os.environ.get(
    'RSS_DAILY_STAGE_RUNS_ROOT',
    '/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/runs',
))
GENERATOR = 'host-deterministic-v1'
RAW_URL = re.compile(r'^https?://[^\s\[\]<>]+$', re.I)
SAFE_RUN_ID = re.compile(r'^[A-Za-z0-9._:-]+$')
ILLEGAL_XML_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
ALLOWED_CATEGORIES = {
    'S-链上金融', 'S-跨境金融', 'S-商业机会',
    'A-AI-Agent', 'A-AI工具自动化', 'A-微信企微私域', 'A-AIxCryptoPayment',
    'B-海外互联网', 'B-开源自托管', 'B-政策监管',
    'C-宏观流动性', 'C-消费科技',
}
PRIORITY_RANK = {'S': 0, 'A': 1, 'B': 2, 'C': 3}
FINAL_CAP = {'morning': 24, 'afternoon': 16}
REPORT_CAP = {'morning': 12000, 'afternoon': 8000}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def bounded(value, name, maximum, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a string')
    result = value.strip()
    if not allow_empty and not result:
        raise ValueError(f'{name} must not be empty')
    if ILLEGAL_XML_TEXT.search(result):
        raise ValueError(f'{name} contains illegal control characters')
    if len(result) > maximum:
        raise ValueError(f'{name} exceeds {maximum} characters')
    return result


def valid_url(value, name):
    url = bounded(value, name, 4096)
    if not RAW_URL.match(url):
        raise ValueError(f'{name} must be a raw HTTP(S) URL')
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.hostname == '...':
        raise ValueError(f'{name} must be a usable HTTP(S) URL')
    return url


def optional_iso(value, name):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = bounded(value, name, 64)
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'{name} invalid') from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        os.chmod(path, mode)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def markdown_plain(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text.translate(str.maketrans({
        '[': '［', ']': '］', '`': '｀', '*': '＊', '<': '＜', '>': '＞',
    }))


def candidate_rank(candidate, index):
    priority = candidate['priority']
    published = candidate.get('publishedAt')
    timestamp = 0.0
    if published:
        try:
            timestamp = datetime.fromisoformat(str(published).replace('Z', '+00:00')).timestamp()
        except (TypeError, ValueError):
            timestamp = 0.0
    return (
        PRIORITY_RANK[priority],
        0 if candidate['isHighValueOpportunity'] is True else 1,
        0 if candidate['needsVerification'] is False else 1,
        -timestamp,
        candidate['eventKey'],
        candidate['primaryArticleId'],
    )


def normalize_candidate(raw, index):
    if not isinstance(raw, dict):
        raise ValueError('candidate must be an object')
    candidate = {
        'primaryArticleId': bounded(raw.get('primaryArticleId'), 'primaryArticleId', 512),
        'eventKey': bounded(raw.get('eventKeyCandidate'), 'eventKeyCandidate', 500),
        'title': bounded(raw.get('title'), 'title', 300),
        'priority': raw.get('priority'),
        'category': bounded(raw.get('category'), 'category', 160),
        'summary': bounded(raw.get('summary'), 'summary', 3000),
        'whyImportant': bounded(raw.get('whyImportant'), 'whyImportant', 3000),
        'impact': bounded(raw.get('impact'), 'impact', 3000),
        'isHighValueOpportunity': raw.get('isHighValueOpportunity'),
        'needsVerification': raw.get('needsVerification'),
        '_index': index,
    }
    if len(candidate['eventKey']) < 3:
        raise ValueError('eventKeyCandidate must be at least 3 characters')
    if candidate['priority'] not in PRIORITY_RANK:
        raise ValueError('invalid candidate priority')
    if candidate['category'] not in ALLOWED_CATEGORIES:
        raise ValueError('invalid candidate category')
    if not isinstance(candidate['isHighValueOpportunity'], bool):
        raise ValueError('isHighValueOpportunity must be boolean')
    if not isinstance(candidate['needsVerification'], bool):
        raise ValueError('needsVerification must be boolean')
    sources = raw.get('sources')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 3:
        raise ValueError('candidate sources must contain 1..3 entries')
    normalized_sources = []
    seen_urls = set()
    for number, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError('candidate source must be an object')
        url = valid_url(source.get('url'), f'sources[{number}].url')
        if url in seen_urls:
            continue
        seen_urls.add(url)
        name_value = source.get('name') or url
        normalized_sources.append({'name': bounded(name_value, f'sources[{number}].name', 200), 'url': url})
    if not normalized_sources:
        raise ValueError('candidate has no unique source')
    candidate['sources'] = normalized_sources
    for key, maximum in (
        ('opportunity', 3000), ('risk', 3000), ('originalTitle', 1000),
    ):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            candidate[key] = bounded(value, key, maximum)
    published_at = optional_iso(raw.get('publishedAt'), 'publishedAt')
    if published_at:
        candidate['publishedAt'] = published_at
    return candidate


def canonical_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def baseline_events(edition):
    if edition != 'afternoon':
        return set(), set(), None, 'none'
    path = CANDIDATE_ROOT / 'afternoon-morning-baseline.json'
    if not path.exists():
        raise RuntimeError('AFTERNOON_MORNING_BASELINE_MISSING')
    baseline = load(path)
    if baseline.get('schemaVersion') != 2 or not isinstance(baseline.get('items', []), list):
        raise RuntimeError('AFTERNOON_MORNING_BASELINE_INVALID')
    keys = set()
    urls = set()
    for item in baseline.get('items') or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get('eventKey') or '').strip()
        if not key:
            continue
        keys.add(key)
        for source in item.get('sources') or []:
            if isinstance(source, dict) and str(source.get('url') or '').strip():
                urls.add(str(source['url']).strip())
    found = baseline.get('found') is True
    baseline_id = str(baseline.get('baselineId') or '')
    if not found:
        if baseline_id != 'none' or baseline.get('items') not in (None, []):
            raise RuntimeError('AFTERNOON_EMPTY_BASELINE_INVALID')
    else:
        payload = {
            'schemaVersion': 2,
            'found': True,
            'dateKey': baseline.get('dateKey'),
            'title': baseline.get('title', ''),
            'reportMarkdown': baseline.get('reportMarkdown', ''),
            'items': baseline.get('items', []),
            'publishedAt': baseline.get('publishedAt', ''),
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        expected_id = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
        if baseline_id != expected_id:
            raise RuntimeError('AFTERNOON_BASELINE_HASH_MISMATCH')
        today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
        if baseline.get('dateKey') != today:
            raise RuntimeError('AFTERNOON_BASELINE_NOT_SAME_DAY')
    return keys, urls, baseline, canonical_sha(baseline)


def validate_candidate_provenance(candidate_file, run_id, candidate_ids):
    if len(candidate_ids) > 500:
        raise RuntimeError('HOST_FINAL_TOO_MANY_CANDIDATES')
    total = int(candidate_file.get('totalRawArticles', -1))
    model_input = int(candidate_file.get('modelInputArticles', -1))
    candidate_count = len(candidate_ids)
    if (
        total < 0
        or int(candidate_file.get('accountedRawArticles', -1)) != total
        or int(candidate_file.get('hostBootstrapBackfillArticles', -1)) + model_input != total
        or int(candidate_file.get('candidateArticleCount', -1)) != candidate_count
        or int(candidate_file.get('preliminaryCandidates', -1)) != candidate_count
        or int(candidate_file.get('modelBackfillArticles', -1))
        + int(candidate_file.get('modelFilteredLowValue', -1))
        + int(candidate_file.get('modelMergedDuplicates', -1))
        + candidate_count != model_input
    ):
        raise RuntimeError('HOST_FINAL_CANDIDATE_COUNT_EQUATION_MISMATCH')
    run_dir = (RUNS_ROOT / run_id).resolve()
    try:
        run_dir.relative_to(RUNS_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError('HOST_FINAL_RUN_DIRECTORY_OUTSIDE_ROOT') from exc
    raw_path = run_dir / 'raw-accounting.json'
    combined_path = run_dir / 'combined.json'
    manifest_path = run_dir / 'snapshot-manifest.json'
    quarantine_path = run_dir / 'bootstrap-quarantine.json'
    expected_raw_ref = str(raw_path)
    if str(candidate_file.get('rawAccountingFile') or '') != expected_raw_ref:
        raise RuntimeError('HOST_FINAL_RAW_ACCOUNTING_PATH_INVALID')
    if str(candidate_file.get('bootstrapQuarantineFile') or '') != str(quarantine_path):
        raise RuntimeError('HOST_FINAL_QUARANTINE_PATH_INVALID')
    if not all(path.is_file() for path in (raw_path, combined_path, manifest_path, quarantine_path)):
        raise RuntimeError('HOST_FINAL_PROVENANCE_FILE_MISSING')
    if load(combined_path) != candidate_file:
        raise RuntimeError('HOST_FINAL_CURRENT_COMBINED_MISMATCH')
    raw_accounting = load(raw_path)
    if not isinstance(raw_accounting, list):
        raise RuntimeError('HOST_FINAL_RAW_ACCOUNTING_NOT_LIST')
    if len(raw_accounting) != total:
        raise RuntimeError('HOST_FINAL_RAW_ACCOUNTING_LENGTH_MISMATCH')
    allowed = {'backfill', 'filtered', 'merged', 'candidate'}
    seen = set()
    counts = {key: 0 for key in allowed}
    model_candidate_ids = set()
    for row in raw_accounting:
        if not isinstance(row, dict):
            raise RuntimeError('HOST_FINAL_RAW_ACCOUNTING_ROW_INVALID')
        article_id = str(row.get('id') or '').strip()
        disposition = str(row.get('disposition') or '').strip()
        if not article_id or article_id in seen or disposition not in allowed:
            raise RuntimeError('HOST_FINAL_RAW_ACCOUNTING_ROW_INVALID')
        seen.add(article_id)
        counts[disposition] += 1
        if disposition == 'candidate':
            model_candidate_ids.add(article_id)
    if model_candidate_ids != set(candidate_ids):
        raise RuntimeError('HOST_FINAL_PRELIMINARY_CANDIDATE_ID_MISMATCH')
    expected_backfill = int(candidate_file.get('hostBootstrapBackfillArticles', 0)) + int(candidate_file.get('modelBackfillArticles', 0))
    if (
        counts['backfill'] != expected_backfill
        or counts['filtered'] != int(candidate_file.get('modelFilteredLowValue', 0))
        or counts['merged'] != int(candidate_file.get('modelMergedDuplicates', 0))
        or counts['candidate'] != len(candidate_ids)
    ):
        raise RuntimeError('HOST_FINAL_RAW_ACCOUNTING_COUNT_MISMATCH')
    manifest = load(manifest_path)
    if (
        manifest.get('schemaVersion') != 2
        or manifest.get('runId') != run_id
        or manifest.get('edition') != candidate_file.get('edition')
        or manifest.get('snapshotHash') != candidate_file.get('snapshotHash')
        or manifest.get('snapshotId') != candidate_file.get('snapshotId')
        or manifest.get('sourceFetchRunId') != candidate_file.get('sourceFetchRunId')
        or str(manifest.get('runDirectory') or '') != str(run_dir)
        or int(manifest.get('totalRawArticles', -1)) != total
        or manifest.get('allPagesComplete') is not True
    ):
        raise RuntimeError('HOST_FINAL_SNAPSHOT_MANIFEST_MISMATCH')
    quarantine = load(quarantine_path)
    if not isinstance(quarantine, list) or len(quarantine) != int(candidate_file.get('hostBootstrapBackfillArticles', -1)):
        raise RuntimeError('HOST_FINAL_QUARANTINE_COUNT_MISMATCH')
    quarantine_ids = [str(row.get('id') or '').strip() for row in quarantine if isinstance(row, dict)]
    if len(quarantine_ids) != len(quarantine) or '' in quarantine_ids or len(quarantine_ids) != len(set(quarantine_ids)):
        raise RuntimeError('HOST_FINAL_QUARANTINE_IDS_INVALID')
    host_backfill_ids = {
        str(row.get('id') or '').strip()
        for row in raw_accounting
        if row.get('accountingSource') == 'host-bootstrap-quarantine'
    }
    if set(quarantine_ids) != host_backfill_ids:
        raise RuntimeError('HOST_FINAL_QUARANTINE_ACCOUNTING_MISMATCH')


def make_item(primary, group):
    item = {
        'eventKey': primary['eventKey'],
        'title': primary['title'],
        'priority': primary['priority'],
        'category': primary['category'],
        'summary': primary['summary'],
        'whyImportant': primary['whyImportant'],
        'impact': primary['impact'],
        'isHighValueOpportunity': primary['isHighValueOpportunity'],
        '_needsVerification': primary['needsVerification'],
    }
    for key in ('publishedAt', 'opportunity', 'risk', 'originalTitle'):
        if primary.get(key):
            item[key] = primary[key]
    sources = []
    seen_urls = set()
    for candidate in group:
        for source in candidate['sources']:
            if source['url'] not in seen_urls and len(sources) < 5:
                sources.append(source)
                seen_urls.add(source['url'])
    item['sources'] = sources
    return item


def group_candidates(candidates):
    parent = list(range(len(candidates)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_event = {}
    by_url = {}
    for index, candidate in enumerate(candidates):
        event_key = candidate['eventKey']
        if event_key in by_event:
            union(index, by_event[event_key])
        else:
            by_event[event_key] = index
        for source in candidate['sources']:
            url = source['url']
            if url in by_url:
                union(index, by_url[url])
            else:
                by_url[url] = index
    groups = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(find(index), []).append(candidate)
    return list(groups.values())


def clipped(value, maximum):
    value = markdown_plain(value)
    return value if len(value) <= maximum else value[:maximum - 1].rstrip() + '…'


def report_item_block(item):
    lines = [
        f'### [{item["priority"]}] {clipped(item["title"], 180)}',
        f'- 分类：{item["category"]}',
        f'- 摘要：{clipped(item["summary"], 220)}',
        f'- 为什么重要：{clipped(item["whyImportant"], 160)}',
        f'- 影响：{clipped(item["impact"], 160)}',
    ]
    if item.get('opportunity'):
        lines.append(f'- 机会：{clipped(item["opportunity"], 160)}')
    if item.get('risk'):
        lines.append(f'- 风险：{clipped(item["risk"], 140)}')
    if item.get('_needsVerification') is True:
        lines.append('- 核验：待原始来源复核')
    lines.append(f'- 来源：{item["sources"][0]["url"]}')
    return '\n'.join(lines)


def grouped_bullets(items, categories):
    selected = [item for item in items if item['category'] in categories]
    if not selected:
        return '- 本时段没有符合条件的新情报。'
    return '\n'.join(
        f'- [{item["priority"]}] {clipped(item["title"], 120)}'
        for item in selected
    )


def build_report(edition, candidate_file, items, accounting, baseline):
    retained = sum(row['disposition'] == 'retained' for row in accounting)
    merged = sum(row['disposition'] == 'merged' for row in accounting)
    filtered = sum(row['disposition'] == 'filtered' for row in accounting)
    stats = (
        f'原始 {int(candidate_file.get("totalRawArticles", 0))} 篇；'
        f'Host 隔离 {int(candidate_file.get("hostBootstrapBackfillArticles", 0))} 篇；'
        f'模型输入 {int(candidate_file.get("modelInputArticles", 0))} 篇；'
        f'分类批次 {int(candidate_file.get("classifierBatchCount", 0))}；'
        f'初选 {len(candidate_file.get("candidates") or [])} 条；'
        f'最终保留 {retained} 条、合并 {merged} 条、过滤 {filtered} 条。'
    )
    opportunity = [item for item in items if item['isHighValueOpportunity']]
    opportunity_text = grouped_bullets(opportunity, ALLOWED_CATEGORIES) if opportunity else '- 本时段没有新增高价值商业机会。'
    ai_categories = {'A-AI-Agent', 'A-AI工具自动化', 'A-微信企微私域', 'A-AIxCryptoPayment'}
    policy_categories = {'B-政策监管', 'C-宏观流动性'}
    def assemble(top_count):
        top_text = '\n\n'.join(report_item_block(item) for item in items[:top_count])
        if not top_text:
            top_text = '本时段没有达到重点标准的新情报。'
        if edition == 'morning':
            sections = [
            '# 08:00 情报晨报',
            f'> {stats}',
            '## 一、今日最高价值情报', top_text,
            '## 二、四条核心主线',
            '### 1. 全球资金与金融基础设施', grouped_bullets(items, {'S-链上金融', 'S-跨境金融', 'C-宏观流动性'}),
            '### 2. 新商业模式与赚钱机会', grouped_bullets(items, {'S-商业机会'}),
            '### 3. Crypto真实产业进展', grouped_bullets(items, {'S-链上金融', 'A-AIxCryptoPayment'}),
            '### 4. 全球互联网新东西', grouped_bullets(items, {'B-海外互联网', 'B-开源自托管', 'C-消费科技'}),
            '## 三、值得进一步研究的机会', opportunity_text,
            '## 四、AI / Agent重要变化', grouped_bullets(items, ai_categories),
            '## 五、政策与宏观环境', grouped_bullets(items, policy_categories),
            '## 六、继续观察', grouped_bullets(items, {'B-海外互联网', 'B-开源自托管', 'C-消费科技'}),
            '## 七、过滤统计', f'- {stats}',
            ]
        else:
            baseline_note = (
            '已使用同日 Morning immutable baseline，并过滤仅重复上午事实且没有新来源的事件。'
            if baseline and baseline.get('found') is True else
            '本次没有可用的同日 Morning baseline；未用旧报告替代。'
        )
            sections = [
            '# 14:00 情报更新',
            f'> {stats} {baseline_note}',
            '## 一、新增必须关注', top_text,
            '## 二、新发现机会', opportunity_text,
            '## 三、上午重点事件的新进展', grouped_bullets(items, {'S-链上金融', 'S-跨境金融', 'S-商业机会'}),
            '## 四、AI / Agent重大变化', grouped_bullets(items, ai_categories),
            '## 五、继续观察', grouped_bullets(items, {'B-海外互联网', 'B-开源自托管', 'B-政策监管', 'C-宏观流动性', 'C-消费科技'}),
            '## 六、过滤统计', f'- {stats}',
            ]
        if candidate_file.get('recoveryFromWatermark') is True:
            sections.insert(2, '> 本次包含从上次成功水位线恢复的扩展覆盖。')
        return '\n\n'.join(sections).strip() + '\n'
    for top_count in ((8, 5, 3, 0) if edition == 'morning' else (6, 4, 2, 0)):
        report = assemble(top_count)
        if 1 <= len(report) <= REPORT_CAP[edition]:
            return report
    raise RuntimeError('HOST_FINAL_REPORT_LENGTH_INVALID')


def reusable_stage(edition, run_id, stage_id, expected):
    path = STAGE_ROOT / edition / f'{run_id}-{stage_id}.json'
    if not path.is_file():
        return None
    try:
        if os.name == 'posix' and stat.S_IMODE(path.stat().st_mode) != 0o600:
            return None
        data = load(path)
        if data == expected:
            return path
        return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def finalize(edition):
    candidate_path = CANDIDATE_ROOT / f'{edition}-candidates.json'
    candidate_file = load(candidate_path)
    if (
        candidate_file.get('schemaVersion') != 2
        or candidate_file.get('edition') != edition
        or candidate_file.get('allPagesComplete') is not True
        or not isinstance(candidate_file.get('candidates'), list)
    ):
        raise RuntimeError('HOST_FINAL_CANDIDATE_FILE_INVALID')
    run_id = bounded(candidate_file.get('runId'), 'runId', 160)
    if not SAFE_RUN_ID.fullmatch(run_id):
        raise RuntimeError('HOST_FINAL_RUN_ID_UNSAFE')
    candidates = [normalize_candidate(raw, index) for index, raw in enumerate(candidate_file['candidates'])]
    ids = [candidate['primaryArticleId'] for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError('HOST_FINAL_CANDIDATE_IDS_NOT_UNIQUE')
    validate_candidate_provenance(candidate_file, run_id, ids)
    candidate_sha = canonical_sha(candidate_file)
    baseline_keys, baseline_urls, baseline, baseline_sha = baseline_events(edition)
    if edition == 'afternoon':
        baseline_id = bounded(candidate_file.get('morningBaselineId'), 'morningBaselineId', 160)
        baseline_found = candidate_file.get('morningBaselineFound')
        if not isinstance(baseline_found, bool):
            raise RuntimeError('HOST_FINAL_MORNING_BASELINE_FOUND_INVALID')
        if baseline_found is not bool((baseline or {}).get('found')):
            raise RuntimeError('HOST_FINAL_MORNING_BASELINE_FOUND_MISMATCH')
        if str((baseline or {}).get('baselineId') or '') != baseline_id:
            raise RuntimeError('HOST_FINAL_MORNING_BASELINE_ID_MISMATCH')
    else:
        baseline_id = ''

    grouped = []
    for group in group_candidates(candidates):
        ordered = sorted(group, key=lambda item: candidate_rank(item, item['_index']))
        primary = ordered[0]
        grouped.append((candidate_rank(primary, primary['_index']), primary['eventKey'], ordered))
    grouped.sort(key=lambda row: (row[0], row[1]))

    eligible = []
    baseline_filtered_keys = set()
    for rank, event_key, group in grouped:
        baseline_match = edition == 'afternoon' and any(
            candidate['eventKey'] in baseline_keys
            or any(source['url'] in baseline_urls for source in candidate['sources'])
            for candidate in group
        )
        if baseline_match:
            baseline_filtered_keys.add(event_key)
            continue
        eligible.append((rank, event_key, group))
    selected = eligible[:FINAL_CAP[edition]]
    selected_keys = {event_key for _, event_key, _ in selected}

    items = []
    accounting_by_id = {}
    for _, event_key, group in grouped:
        if event_key not in selected_keys:
            reason = (
                '与 Morning baseline 相同且没有新来源，Host 确定性过滤'
                if event_key in baseline_filtered_keys else
                f'超过 {edition} 最终精选上限，由 Host 按优先级/机会/时间确定性过滤'
            )
            for candidate in group:
                accounting_by_id[candidate['primaryArticleId']] = {
                    'primaryArticleId': candidate['primaryArticleId'],
                    'disposition': 'filtered',
                    'reason': reason,
                }
            continue
        primary = group[0]
        items.append(make_item(primary, group))
        accounting_by_id[primary['primaryArticleId']] = {
            'primaryArticleId': primary['primaryArticleId'],
            'disposition': 'retained',
            'finalEventKey': primary['eventKey'],
            'reason': '分类批次已通过严格校验；Host 按优先级与机会价值保留',
        }
        for duplicate in group[1:]:
            accounting_by_id[duplicate['primaryArticleId']] = {
                'primaryArticleId': duplicate['primaryArticleId'],
                'disposition': 'merged',
                'finalEventKey': primary['eventKey'],
                'reason': '相同 eventKey 或原始来源 URL 的跨候选事件由 Host 确定性合并',
            }

    accounting = [accounting_by_id[candidate['primaryArticleId']] for candidate in candidates]
    if len(accounting) != len(candidates) or set(accounting_by_id) != set(ids):
        raise RuntimeError('HOST_FINAL_ACCOUNTING_MISMATCH')
    item_keys = [item['eventKey'] for item in items]
    if len(item_keys) != len(set(item_keys)):
        raise RuntimeError('HOST_FINAL_ITEM_KEYS_NOT_UNIQUE')
    retained_keys = {
        row['finalEventKey'] for row in accounting if row['disposition'] == 'retained'
    }
    if retained_keys != set(item_keys):
        raise RuntimeError('HOST_FINAL_RETAINED_ITEM_COVERAGE_MISMATCH')

    report = build_report(edition, candidate_file, items, accounting, baseline)
    for item in items:
        item.pop('_needsVerification', None)
    prepared_at = optional_iso(candidate_file.get('preparedAt'), 'preparedAt')
    if not prepared_at:
        raise RuntimeError('HOST_FINAL_PREPARED_AT_MISSING')
    stage_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f'{GENERATOR}|{edition}|{candidate_sha}|{baseline_sha}',
    ))
    stage = {
        'schemaVersion': 2,
        'status': 'staged',
        'stageId': stage_id,
        'generator': GENERATOR,
        'edition': edition,
        'candidateRunId': run_id,
        'generatedAt': prepared_at,
        'savedAt': prepared_at,
        'title': '08:00 情报晨报' if edition == 'morning' else '14:00 情报更新',
        'reportMarkdown': report,
        'accounting': accounting,
        'items': items,
        'candidateSha256': candidate_sha,
        'baselineSha256': baseline_sha,
        'selectionStats': {
            'preliminaryCandidates': len(candidates),
            'retained': len(items),
            'merged': sum(row['disposition'] == 'merged' for row in accounting),
            'filtered': sum(row['disposition'] == 'filtered' for row in accounting),
            'cap': FINAL_CAP[edition],
        },
    }
    if edition == 'afternoon':
        stage['morningBaselineId'] = baseline_id

    previous = reusable_stage(edition, run_id, stage_id, stage)
    if previous:
        print(f'HOST_FINAL_STAGE_REUSED edition={edition} candidateRunId={run_id} file={previous.name}')
        return previous

    directory = STAGE_ROOT / edition
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STAGE_ROOT, 0o700)
    os.chmod(directory, 0o700)
    path = directory / f'{run_id}-{stage_id}.json'
    atomic_json(path, stage, 0o600)
    print(
        f'HOST_FINAL_STAGE_READY edition={edition} candidateRunId={run_id} '
        f'candidates={len(candidates)} retained={len(items)} '
        f'merged={sum(row["disposition"] == "merged" for row in accounting)} '
        f'filtered={sum(row["disposition"] == "filtered" for row in accounting)} '
        f'file={path.name}'
    )
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('edition', choices=['morning', 'afternoon'])
    args = parser.parse_args()
    path = finalize(args.edition)
    print(f'HOST_FINAL_STAGE_PATH={path.resolve()}')


if __name__ == '__main__':
    main()
