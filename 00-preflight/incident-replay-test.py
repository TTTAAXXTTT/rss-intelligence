#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

pkg = Path(sys.argv[1]).resolve()
incident = Path(sys.argv[2]).resolve()
pipeline = pkg / '04-daily-pipeline'
sys.path.insert(0, str(pipeline))
spec = importlib.util.spec_from_file_location('prepare_daily_v52_replay', pipeline / 'prepare-daily.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

progress = json.loads((incident / 'run' / 'morning-progress.json').read_text(encoding='utf-8'))
runid = progress['runId']
rundir = incident / 'run' / runid
articles = []
ids = []
for page in range(1, 101):
    raw = json.loads((rundir / f'raw-page-{page:03d}.json').read_text(encoding='utf-8'))
    for article in raw['articles']:
        articles.append(article)
        ids.append(str(article['id']).strip())
assert len(articles) == 4000
assert len(set(ids)) == 4000

digest = m.snapshot_hash(progress['sourceFetchRunId'], progress['cutoffTimestampSec'], ids)
manifest = {
    'schemaVersion': 2,
    'edition': 'morning',
    'hours': 18,
    'runId': runid,
    'snapshotId': digest[:32],
    'snapshotHash': digest,
    'sourceFetchRunId': progress['sourceFetchRunId'],
    'sourceFetchFinishedAt': progress['sourceFetchFinishedAt'],
    'normalCutoffTimestampSec': progress.get('normalCutoffTimestampSec', progress['cutoffTimestampSec']),
    'effectiveCutoffTimestampSec': progress.get('effectiveCutoffTimestampSec', progress['cutoffTimestampSec']),
    'cutoffTimestampSec': progress['cutoffTimestampSec'],
    'recoveryFromWatermark': bool(progress.get('recoveryFromWatermark')),
    'watermarkAnchorTimestampSec': progress.get('watermarkAnchorTimestampSec'),
    'pageCount': 100,
    'allPagesComplete': True,
    'totalRawArticles': 4000,
    'runDirectory': str(rundir),
}
verified = m.snapshot_articles(manifest)
assert len(verified) == 4000

with tempfile.TemporaryDirectory(prefix='rss-v52-incident-replay-') as td:
    td = Path(td)
    m.BOOTSTRAP_STATE = td / 'bootstrap-state-v52.json'
    replay_run = td / 'run'
    replay_run.mkdir()
    plan = m.create_or_load_plan('morning', manifest, verified, replay_run)
    assert plan['totalRawArticles'] == 4000
    assert plan['hostBootstrapBackfillArticles'] == 3623, plan
    assert plan['modelInputArticles'] == 377, plan
    assert plan['batchCount'] <= 25, plan['batchCount']
    assert plan['batchCount'] >= 19, plan['batchCount']
    assert max(x['inputChars'] for x in plan['batches']) <= m.MAX_CLASSIFIER_INPUT_CHARS
    assert max(x['rawCount'] for x in plan['batches']) <= m.MAX_CLASSIFIER_ARTICLES

    quarantine_rows = json.loads((replay_run / 'bootstrap-quarantine.json').read_text(encoding='utf-8'))
    quarantine = {row['id']: row for row in quarantine_rows}
    assert len(quarantine) == 3623

    model_ids = set()
    for meta in plan['batches']:
        batch = json.loads((replay_run / meta['file']).read_text(encoding='utf-8'))
        model_ids.update(str(a['id']) for a in batch['articles'])
    assert len(model_ids) == 377
    assert set(quarantine).isdisjoint(model_ids)
    assert set(quarantine) | model_ids == set(ids)

    old_candidate_ids = set()
    fresh_old_candidate_ids = set()
    flagged_old_candidate_ids = set()
    tail_ids = set()
    article_by_id = {str(a['id']): a for a in articles}
    for page in range(1, 101):
        rec = json.loads((rundir / f'page-{page:03d}.json').read_text(encoding='utf-8'))
        cls = rec['classification']
        for row in cls['accounting']:
            if row['disposition'] == 'candidate':
                aid = str(row['id'])
                old_candidate_ids.add(aid)
                if article_by_id[aid].get('isLikelyBackfill'):
                    flagged_old_candidate_ids.add(aid)
                else:
                    fresh_old_candidate_ids.add(aid)
        if page >= 70:
            raw = json.loads((rundir / f'raw-page-{page:03d}.json').read_text(encoding='utf-8'))
            tail_ids.update(str(a['id']) for a in raw['articles'])

    assert len(old_candidate_ids) == 204
    assert len(fresh_old_candidate_ids) == 172
    assert len(flagged_old_candidate_ids) == 32
    assert fresh_old_candidate_ids <= model_ids
    assert flagged_old_candidate_ids <= set(quarantine)
    assert len(tail_ids) == 1240
    assert tail_ids <= set(quarantine), len(tail_ids - set(quarantine))

    # Cross-schedule regression: after Morning commits bootstrap feeds + quarantined article IDs,
    # an Afternoon view containing the same inventory must not send those 3623 IDs back to the model.
    feed_keys=sorted({m.article_feed_key(a) for a in articles})
    m.BOOTSTRAP_STATE.write_text(json.dumps({
        'schemaVersion':2,
        'feeds':{k:{} for k in feed_keys},
        'quarantinedArticleIds':{aid:{'feedKey':m.article_feed_key(article_by_id[aid]),'contentFingerprint':m.article_content_fingerprint(article_by_id[aid])} for aid in quarantine},
    }),encoding='utf-8')
    replay_run2=td/'run-afternoon'; replay_run2.mkdir()
    plan2=m.create_or_load_plan('afternoon',dict(manifest,edition='afternoon',hours=6,runId='afternoon-replay',snapshotId='afternoon-snapshot',snapshotHash='afternoon-hash'),verified,replay_run2)
    assert plan2['hostBootstrapBackfillArticles']==3623, plan2
    assert plan2['persistentBootstrapQuarantineHits']==3623, plan2
    assert plan2['newBootstrapQuarantineArticles']==0, plan2
    assert plan2['modelInputArticles']==377, plan2

    print('INCIDENT_RAW=4000')
    print('INCIDENT_BOOTSTRAP_QUARANTINE=3623')
    print('INCIDENT_MODEL_INPUT=377')
    print(f'INCIDENT_DYNAMIC_BATCHES={plan["batchCount"]}')
    print('INCIDENT_OLD_V51_CANDIDATES=204')
    print('INCIDENT_FRESH_CANDIDATES_ROUTED_TO_MODEL=172')
    print('INCIDENT_HISTORICAL_V51_CANDIDATES_QUARANTINED=32')
    print('INCIDENT_TAIL70_100_QUARANTINED=1240')
    print('INCIDENT_AFTERNOON_PERSISTENT_QUARANTINE=3623')
    print('INCIDENT_REPLAY_V52_OK')
