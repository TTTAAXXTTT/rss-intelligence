#!/usr/bin/env python3
import argparse
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from daily_common import (
    MAX_SNAPSHOT_PAGES,
    ROOT,
    article_feed_key,
    assert_source_run_unchanged,
    atomic_json,
    compute_cutoff,
    fetch_page,
    fresh_config,
    fresh_state,
    load,
    login_freshrss,
    snapshot_hash,
    subscription_map,
)


def rebuild_existing(rundir, pages_done):
    ids = []
    feed_keys = set()
    totals = {
        'raw': 0,
        'likelyBackfill': 0,
        'summaryTruncated': 0,
        'sourceBeforeCutoff': 0,
        'sourceDuplicates': 0,
    }
    seen = set()
    for page in range(1, pages_done + 1):
        path = rundir / f'raw-page-{page:03d}.json'
        if not path.exists():
            raise RuntimeError(f'SNAPSHOT_RESUME_RAW_PAGE_MISSING page={page}')
        raw = load(path)
        for article in raw.get('articles') or []:
            article_id = str(article.get('id') or '').strip()
            if not article_id:
                raise RuntimeError(f'SNAPSHOT_ARTICLE_ID_MISSING page={page}')
            if article_id in seen:
                raise RuntimeError(f'SNAPSHOT_CROSS_PAGE_DUPLICATE_ID id={article_id}')
            seen.add(article_id)
            ids.append(article_id)
            feed_keys.add(article_feed_key(article))
            totals['raw'] += 1
            totals['likelyBackfill'] += int(bool(article.get('isLikelyBackfill')))
            totals['summaryTruncated'] += int(bool(article.get('summaryTruncated')))
        totals['sourceBeforeCutoff'] += int(raw.get('filteredOutBeforeCutoff', 0))
        totals['sourceDuplicates'] += int(raw.get('duplicateSourceItems', 0))
    return ids, feed_keys, totals, seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('edition', choices=['morning', 'afternoon'])
    parser.add_argument('--reuse', action='store_true')
    args = parser.parse_args()

    edition = args.edition
    hours = 18 if edition == 'morning' else 6
    current_dir = ROOT / 'current'
    current_manifest = current_dir / f'{edition}-snapshot.json'
    progress_path = current_dir / f'{edition}-snapshot-progress.json'
    current_dir.mkdir(parents=True, exist_ok=True)

    source = fresh_state(require_fresh=True)
    source_run_id = str(source['runId'])
    normal_cutoff, effective_cutoff, recovery, watermark_anchor = compute_cutoff(hours)

    if args.reuse and current_manifest.exists():
        try:
            old = load(current_manifest)
            if (
                old.get('schemaVersion') == 2
                and old.get('edition') == edition
                and old.get('allPagesComplete') is True
                and old.get('sourceFetchRunId') == source_run_id
                and int(old.get('hours')) == hours
            ):
                print(
                    f'SNAPSHOT_REUSED edition={edition} runId={old.get("runId")} '
                    f'pages={old.get("pageCount")} raw={old.get("totalRawArticles")} '
                    f'manifest={current_manifest}',
                    flush=True,
                )
                return
        except Exception:
            pass

    progress = None
    if args.reuse and progress_path.exists():
        try:
            candidate = load(progress_path)
            if (
                candidate.get('schemaVersion') == 2
                and candidate.get('edition') == edition
                and candidate.get('sourceFetchRunId') == source_run_id
                and int(candidate.get('hours')) == hours
                and candidate.get('allPagesComplete') is False
            ):
                progress = candidate
        except Exception:
            progress = None

    api, user, password = fresh_config()
    auth = login_freshrss(api, user, password)
    subscriptions = subscription_map(api, auth)

    if progress:
        runid = str(progress['runId'])
        rundir = ROOT / 'runs' / runid
        pages_done = int(progress.get('pageCount', 0))
        cursor = progress.get('nextCursor')
        cutoff = int(progress['cutoffTimestampSec'])
        normal_cutoff = int(progress.get('normalCutoffTimestampSec', cutoff))
        effective_cutoff = int(progress.get('effectiveCutoffTimestampSec', cutoff))
        recovery = bool(progress.get('recoveryFromWatermark', False))
        watermark_anchor = progress.get('watermarkAnchorTimestampSec')
        ids, feed_keys, totals, seen = rebuild_existing(rundir, pages_done)
        print(f'SNAPSHOT_RESUME edition={edition} runId={runid} nextPage={pages_done + 1}', flush=True)
    else:
        runid = f'{edition}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{uuid.uuid4().hex[:8]}'
        rundir = ROOT / 'runs' / runid
        rundir.mkdir(parents=True, exist_ok=False)
        os.chmod(rundir, 0o700)
        pages_done = 0
        cursor = None
        cutoff = effective_cutoff
        ids = []
        feed_keys = set()
        seen = set()
        totals = {
            'raw': 0,
            'likelyBackfill': 0,
            'summaryTruncated': 0,
            'sourceBeforeCutoff': 0,
            'sourceDuplicates': 0,
        }

    complete = False
    for page in range(pages_done + 1, MAX_SNAPSHOT_PAGES + 1):
        raw_page = fetch_page(api, auth, subscriptions, hours, cursor, cutoff, page)
        if int(raw_page['cutoffTimestampSec']) != int(cutoff):
            raise RuntimeError('SNAPSHOT_SOURCE_CUTOFF_CHANGED')
        for article in raw_page['articles']:
            article_id = str(article.get('id') or '').strip()
            if article_id in seen:
                raise RuntimeError(f'SNAPSHOT_CROSS_PAGE_DUPLICATE_ID id={article_id} page={page}')
            seen.add(article_id)
            ids.append(article_id)
            feed_keys.add(article_feed_key(article))
            totals['raw'] += 1
            totals['likelyBackfill'] += int(bool(article.get('isLikelyBackfill')))
            totals['summaryTruncated'] += int(bool(article.get('summaryTruncated')))
        totals['sourceBeforeCutoff'] += int(raw_page.get('filteredOutBeforeCutoff', 0))
        totals['sourceDuplicates'] += int(raw_page.get('duplicateSourceItems', 0))
        atomic_json(rundir / f'raw-page-{page:03d}.json', raw_page, compact=True)
        pages_done = page
        cursor = raw_page['nextCursor']
        progress_obj = {
            'schemaVersion': 2,
            'edition': edition,
            'hours': hours,
            'runId': runid,
            'sourceFetchRunId': source_run_id,
            'sourceFetchFinishedAt': source.get('finishedAt'),
            'normalCutoffTimestampSec': normal_cutoff,
            'effectiveCutoffTimestampSec': cutoff,
            'cutoffTimestampSec': cutoff,
            'recoveryFromWatermark': recovery,
            'watermarkAnchorTimestampSec': watermark_anchor,
            'pageCount': pages_done,
            'allPagesComplete': False,
            'nextCursor': cursor,
            'totals': totals,
        }
        atomic_json(progress_path, progress_obj, compact=True)
        print(
            f'SNAPSHOT_PAGE_OK page={page} raw={raw_page["rawCount"]} '
            f'cumulativeRaw={totals["raw"]} hasMore={raw_page["hasMore"]}',
            flush=True,
        )
        if not raw_page['hasMore']:
            complete = True
            break

    if not complete:
        raise RuntimeError(
            f'SNAPSHOT_CAPACITY_EXCEEDED pages={MAX_SNAPSHOT_PAGES} raw={totals["raw"]}; '
            f'refusing model calls before full immutable snapshot'
        )

    assert_source_run_unchanged(source_run_id)
    digest = snapshot_hash(source_run_id, cutoff, ids)
    manifest = {
        'schemaVersion': 2,
        'edition': edition,
        'hours': hours,
        'runId': runid,
        'snapshotId': digest[:32],
        'snapshotHash': digest,
        'sourceFetchRunId': source_run_id,
        'sourceFetchFinishedAt': source.get('finishedAt'),
        'createdAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'normalCutoffTimestampSec': normal_cutoff,
        'effectiveCutoffTimestampSec': cutoff,
        'cutoffTimestampSec': cutoff,
        'recoveryFromWatermark': recovery,
        'watermarkAnchorTimestampSec': watermark_anchor,
        'pageCount': pages_done,
        'allPagesComplete': True,
        'nextCursor': None,
        'totalRawArticles': totals['raw'],
        'likelyBackfillArticles': totals['likelyBackfill'],
        'summaryTruncatedArticles': totals['summaryTruncated'],
        'sourceItemsBeforeCutoff': totals['sourceBeforeCutoff'],
        'sourceDuplicateItems': totals['sourceDuplicates'],
        'uniqueArticleIds': len(seen),
        'feedKeys': sorted(feed_keys),
        'runDirectory': str(rundir),
    }
    atomic_json(rundir / 'snapshot-manifest.json', manifest, compact=True)
    atomic_json(current_manifest, manifest, compact=True)
    try:
        progress_path.unlink()
    except FileNotFoundError:
        pass
    print(
        f'SNAPSHOT_COMPLETE edition={edition} runId={runid} pages={pages_done} '
        f'raw={totals["raw"]} backfillFlag={totals["likelyBackfill"]} '
        f'manifest={current_manifest}',
        flush=True,
    )


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        print(f'SNAPSHOT_FAIL {exc}', flush=True)
        if 'SNAPSHOT_CAPACITY_EXCEEDED' in str(exc):
            raise SystemExit(66)
        raise
