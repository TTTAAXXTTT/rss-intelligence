#!/usr/bin/env python3
import hashlib
import html
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(os.environ.get('RSS_DAILY_STAGE_ROOT', '/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52'))
FETCH_STATE_ROOT = Path(os.environ.get('RSS_FETCH_STATE_ROOT', '/home/ubuntu/.openclaw/rss-intelligence/fetch-state-v52'))
FETCH_SUCCESS_STATE = Path(os.environ.get('RSS_FETCH_SUCCESS_STATE', str(FETCH_STATE_ROOT / 'last-success.json')))
FETCH_LAST_RUN_STATE = Path(os.environ.get('RSS_FETCH_LAST_RUN_STATE', str(FETCH_STATE_ROOT / 'last-run.json')))
ENV_FILE = Path(os.environ.get('OPENCLAW_ENV_FILE', '/home/ubuntu/.openclaw/.env'))
CURATOR_ENV_FILE = Path(os.environ.get('RSS_CURATOR_ENV_FILE', '/home/ubuntu/openclaw-plugins/rss-curator/.env'))
DAILY_STATE_ROOT = Path(os.environ.get('RSS_DAILY_STATE_ROOT', '/home/ubuntu/.openclaw/rss-intelligence/daily-state-v52'))
INTEL_SUCCESS_STATE = DAILY_STATE_ROOT / 'last-success.json'
LATEST_MORNING_FINAL = DAILY_STATE_ROOT / 'latest-morning-final.json'
RECOVERY_MAX_SECONDS = int(os.environ.get('RSS_RECOVERY_MAX_SECONDS', str(72 * 3600)))
RECOVERY_OVERLAP_SECONDS = 120
SOURCE_SNAPSHOT_MAX_SECONDS = int(os.environ.get('RSS_SOURCE_SNAPSHOT_MAX_SECONDS', str(6 * 3600)))
PAGE_LIMIT = int(os.environ.get('RSS_SOURCE_PAGE_LIMIT', '40'))
MAX_SNAPSHOT_PAGES = int(os.environ.get('RSS_MAX_SNAPSHOT_PAGES', '500'))
MAX_SUMMARY_CHARS = int(os.environ.get('RSS_MAX_SUMMARY_CHARS', '2000'))
BACKFILL_THRESHOLD_DAYS = float(os.environ.get('RSS_BACKFILL_THRESHOLD_DAYS', '7'))


def nowiso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, obj, mode=0o600, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    os.close(fd)
    try:
        text = json.dumps(
            obj,
            ensure_ascii=False,
            separators=(',', ':') if compact else None,
            indent=None if compact else 2,
        ) + '\n'
        Path(tmp).write_text(text, encoding='utf-8')
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        os.chmod(path, mode)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _load_env_file(path, values, overwrite=True):
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key or (not overwrite and key in values):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[key] = value


def read_env():
    values = {}
    _load_env_file(ENV_FILE, values, overwrite=True)
    _load_env_file(CURATOR_ENV_FILE, values, overwrite=True)
    values.update(os.environ)
    return values


def fresh_config():
    env = read_env()
    api = env.get('FRESHRSS_API_URL', 'https://rss.example.com/api/greader.php').strip().rstrip('/')
    user = env.get('FRESHRSS_API_USER', '').strip()
    password = env.get('FRESHRSS_API_PASSWORD', '')
    if not re.match(r'^https?://', api, re.I):
        raise RuntimeError('FRESHRSS_API_URL_INVALID')
    if not user:
        raise RuntimeError('FRESHRSS_API_USER_MISSING')
    if not password:
        raise RuntimeError('FRESHRSS_API_PASSWORD_MISSING')
    return api, user, password


def http_text(req, timeout=30):
    with urlopen(req, timeout=timeout) as response:
        if not (200 <= response.status < 300):
            raise RuntimeError(f'FRESHRSS_HTTP_{response.status}')
        return response.read().decode('utf-8')


def login_freshrss(api, user, password):
    body = urlencode({'Email': user, 'Passwd': password}).encode('utf-8')
    req = Request(
        api + '/accounts/ClientLogin',
        data=body,
        method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'text/plain'},
    )
    text = http_text(req)
    for line in text.splitlines():
        if line.strip().startswith('Auth='):
            token = line.strip()[5:]
            if token:
                return token
    raise RuntimeError('FRESHRSS_LOGIN_AUTH_TOKEN_MISSING')


def get_json(url, auth):
    req = Request(
        url,
        method='GET',
        headers={'Authorization': f'GoogleLogin auth={auth}', 'Accept': 'application/json'},
    )
    return json.loads(http_text(req))


def num(value):
    try:
        value = float(value)
        if value != value or value in (float('inf'), float('-inf')):
            return None
        return value
    except Exception:
        return None


def received_ms(article):
    value = num(article.get('crawlTimeMsec'))
    if value is not None and value > 0:
        return int(value)
    value = num(article.get('timestampUsec'))
    if value is not None and value > 0:
        return int(value / 1000)
    return None


def iso_seconds(value):
    value = num(value)
    if value is None or value <= 0:
        return ''
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def iso_msec(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace('+00:00', 'Z')


def first_href(values):
    if not isinstance(values, list):
        return ''
    for item in values:
        if isinstance(item, dict) and isinstance(item.get('href'), str) and item['href'].strip():
            return item['href'].strip()
    return ''


def html_to_text(raw):
    value = raw or ''
    value = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', ' ', value, flags=re.I)
    value = re.sub(r'<style\b[^>]*>[\s\S]*?</style>', ' ', value, flags=re.I)
    value = re.sub(r'<br\s*/?>', '\n', value, flags=re.I)
    value = re.sub(r'</(?:p|div|li)\s*>', '\n', value, flags=re.I)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = html.unescape(value).replace('\r', '')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def classify_backfill(published_sec, received_msec):
    published = num(published_sec)
    if published is None or published <= 0:
        return None, False
    lag = (received_msec - published * 1000) / 86_400_000
    return round(lag, 2), lag > BACKFILL_THRESHOLD_DAYS


def subscription_map(api, auth):
    payload = get_json(api + '/reader/api/0/subscription/list?output=json', auth)
    result = {}
    for sub in payload.get('subscriptions') or []:
        feed_id = str(sub.get('id') or '')
        if not feed_id:
            continue
        result[feed_id] = {
            'title': str(sub.get('title') or ''),
            'siteUrl': str(sub.get('htmlUrl') or ''),
            'categories': [
                str(cat.get('label') or '')
                for cat in (sub.get('categories') or [])
                if str(cat.get('label') or '')
            ],
        }
    return result


def article_feed_key(article):
    return str(
        article.get('feedId')
        or article.get('feedSiteUrl')
        or article.get('feedTitle')
        or ''
    ).strip()


def normalize_article(article, subs, cutoff_msec, page, ordinal):
    recv = received_ms(article)
    if recv is None:
        return None, 'missing_receive'
    if recv < cutoff_msec:
        return None, 'before_cutoff'
    origin = article.get('origin') or {}
    feed_id = str(origin.get('streamId') or '')
    sub = subs.get(feed_id, {})
    summary_full = html_to_text(str((article.get('summary') or {}).get('content') or ''))
    url = first_href(article.get('alternate')) or first_href(article.get('canonical')) or str(origin.get('htmlUrl') or '')
    raw_id = str(article.get('id') or '').strip()
    published = num(article.get('published'))
    if not raw_id:
        # Stable across continuation page/ordinal movement.
        seed = '\n'.join([
            feed_id,
            url,
            str(article.get('title') or ''),
            str(int(published) if published is not None else ''),
            str(article.get('author') or ''),
        ])
        raw_id = 'synthetic:' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]
    lag, backfill = classify_backfill(published, recv)
    feed_title = sub.get('title') or str(origin.get('title') or '')
    feed_site = sub.get('siteUrl') or str(origin.get('htmlUrl') or '')
    normalized = {
        'id': raw_id,
        'title': str(article.get('title') or ''),
        'url': url,
        'feedId': feed_id,
        'feedTitle': feed_title,
        'feedSiteUrl': feed_site,
        'feedKey': feed_id or feed_site or feed_title,
        'categories': sub.get('categories') or [],
        'author': str(article.get('author') or ''),
        'publishedAt': iso_seconds(article.get('published')),
        'receivedAt': iso_msec(recv),
        'publishedTimestamp': int(published) if published is not None else None,
        'receivedTimestamp': recv,
        'receiveLagDays': lag,
        'isLikelyBackfill': backfill,
        'summaryText': summary_full[:MAX_SUMMARY_CHARS] + ('…' if len(summary_full) > MAX_SUMMARY_CHARS else ''),
        'summaryTruncated': len(summary_full) > MAX_SUMMARY_CHARS,
    }
    if not normalized['feedKey']:
        raise RuntimeError(f'SOURCE_ARTICLE_FEED_KEY_MISSING page={page} ordinal={ordinal}')
    return normalized, None


def fetch_page(api, auth, subs, hours, cursor, cutoff, page):
    cutoff_sec = int(cutoff if cutoff is not None else time.time() - hours * 3600)
    query = {'output': 'json', 'n': str(PAGE_LIMIT), 'ot': str(cutoff_sec)}
    if cursor:
        query['c'] = cursor
    payload = get_json(api + '/reader/api/0/stream/contents/reading-list?' + urlencode(query), auth)
    raw_items = payload.get('items') or []
    articles = []
    before = 0
    missing = 0
    seen_ids = set()
    duplicate_source_items = 0
    for idx, item in enumerate(raw_items, 1):
        normalized, reason = normalize_article(item, subs, cutoff_sec * 1000, page, idx)
        if reason == 'before_cutoff':
            before += 1
            continue
        if reason == 'missing_receive':
            missing += 1
            continue
        if normalized['id'] in seen_ids:
            duplicate_source_items += 1
            continue
        seen_ids.add(normalized['id'])
        articles.append(normalized)
    if missing:
        raise RuntimeError(
            f'SOURCE_ARTICLE_RECEIVE_TIME_MISSING page={page} count={missing}; refusing silent omission'
        )
    next_cursor = str(payload.get('continuation') or '').strip() or None
    return {
        'schemaVersion': 2,
        'source': 'FreshRSS Google Reader API immutable-snapshot source page',
        'hours': hours,
        'limit': PAGE_LIMIT,
        'page': page,
        'cutoffTimestampSec': cutoff_sec,
        'fetchedCount': len(raw_items),
        'filteredOutBeforeCutoff': before,
        'duplicateSourceItems': duplicate_source_items,
        'rawCount': len(articles),
        'hasMore': bool(next_cursor),
        'nextCursor': next_cursor,
        'articles': articles,
    }


def parse_iso_ts(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def fresh_state(require_fresh=True):
    if not FETCH_SUCCESS_STATE.exists():
        raise RuntimeError('SOURCE_FETCH_SUCCESS_STATE_MISSING')
    if not FETCH_LAST_RUN_STATE.exists():
        raise RuntimeError('SOURCE_FETCH_LAST_RUN_STATE_MISSING')
    success = load(FETCH_SUCCESS_STATE)
    last = load(FETCH_LAST_RUN_STATE)
    if success.get('status') != 'ok' or not success.get('runId') or not success.get('finishedAt'):
        raise RuntimeError('SOURCE_FETCH_SUCCESS_STATE_INVALID')
    if last.get('status') != 'ok' or last.get('runId') != success.get('runId'):
        raise RuntimeError(f'SOURCE_FETCH_LATEST_RUN_NOT_SUCCESSFUL lastStatus={last.get("status")}')
    timestamp = datetime.fromisoformat(str(success['finishedAt']).replace('Z', '+00:00'))
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if age < -300:
        raise RuntimeError('SOURCE_FETCH_SUCCESS_TIME_IN_FUTURE')
    if require_fresh and age > SOURCE_SNAPSHOT_MAX_SECONDS:
        raise RuntimeError(
            f'SOURCE_FETCH_STALE_FOR_SNAPSHOT ageSeconds={int(age)} limit={SOURCE_SNAPSHOT_MAX_SECONDS}'
        )
    return success


def assert_source_run_unchanged(expected_run_id):
    success = load(FETCH_SUCCESS_STATE)
    last = load(FETCH_LAST_RUN_STATE)
    if success.get('status') != 'ok' or last.get('status') != 'ok':
        raise RuntimeError('SOURCE_FETCH_STATE_CHANGED_DURING_SNAPSHOT')
    if success.get('runId') != expected_run_id or last.get('runId') != expected_run_id:
        raise RuntimeError(
            f'SOURCE_FETCH_RUN_CHANGED_DURING_SNAPSHOT expected={expected_run_id} '
            f'success={success.get("runId")} last={last.get("runId")}'
        )


def compute_cutoff(hours):
    now_sec = int(time.time())
    normal = now_sec - hours * 3600
    effective = normal
    recovery = False
    anchor = None
    if INTEL_SUCCESS_STATE.exists():
        try:
            state = load(INTEL_SUCCESS_STATE)
            if state.get('status') == 'ok' and state.get('sourceFetchFinishedAt'):
                anchor = parse_iso_ts(state['sourceFetchFinishedAt']) - RECOVERY_OVERLAP_SECONDS
                if anchor < now_sec - RECOVERY_MAX_SECONDS:
                    raise RuntimeError(
                        f'INTELLIGENCE_WATERMARK_TOO_OLD ageSeconds={now_sec-anchor}; manual catch-up required'
                    )
                effective = min(normal, anchor)
                recovery = effective < normal
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f'INTELLIGENCE_WATERMARK_INVALID: {exc}') from exc
    return normal, effective, recovery, anchor


def snapshot_hash(source_run_id, cutoff, article_ids):
    digest = hashlib.sha256()
    digest.update(str(source_run_id).encode('utf-8'))
    digest.update(b'\n')
    digest.update(str(int(cutoff)).encode('ascii'))
    digest.update(b'\n')
    for article_id in article_ids:
        digest.update(str(article_id).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()
