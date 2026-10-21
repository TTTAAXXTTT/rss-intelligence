#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

pkg = Path(sys.argv[1]).resolve()
pipeline = pkg / '04-daily-pipeline'
sys.path.insert(0, str(pipeline))
spec = importlib.util.spec_from_file_location('prepare_daily_v52_fixture', pipeline / 'prepare-daily.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Strict validator rejects incomplete accounting and validates raw URLs against the exact batch.
batch={'schemaVersion':2,'edition':'morning','batch':1,'rawCount':2,'inputChars':100,'snapshotId':'s','articles':[{'id':'a1','url':'https://example.com/a1'},{'id':'a2','url':'https://example.com/a2'}]}
valid={'schemaVersion':2,'edition':'morning','batch':1,'rawCount':2,'backfillCount':0,'filteredLowValueCount':1,'mergedDuplicateArticleCount':0,'candidateArticleCount':1,'accounting':[{'id':'a1','disposition':'candidate'},{'id':'a2','disposition':'filtered'}],'candidates':[{'primaryArticleId':'a1','eventKeyCandidate':'https://example.com/a1','title':'测试','originalTitle':'Test','priority':'S','category':'S-链上金融','summary':'s','whyImportant':'w','impact':'i','isHighValueOpportunity':False,'needsVerification':False,'sources':[{'name':'Example','url':'https://example.com/a1'}]}]}
assert m.validate_classifier_result('morning',batch,json.loads(json.dumps(valid)))['candidateArticleCount']==1
bad=json.loads(json.dumps(valid)); bad['accounting']=bad['accounting'][:1]
try:
    m.validate_classifier_result('morning',batch,bad); raise AssertionError('missing accounting accepted')
except ValueError:
    pass

with tempfile.TemporaryDirectory(prefix='rss-v52-daily-fixture-') as td:
    td=Path(td)
    m.ROOT=td/'workspace'; m.BOOTSTRAP_STATE=td/'bootstrap-state-v52.json'; m.DAILY_STATE_ROOT=td/'state'; m.LATEST_MORNING_FINAL=m.DAILY_STATE_ROOT/'latest-morning-final.json'
    run=td/'run'; run.mkdir()
    articles=[
        {'id':'old1','url':'https://example.com/old1','feedId':'feed/1','feedTitle':'F1','feedSiteUrl':'https://example.com/f1','feedKey':'feed/1','title':'old','isLikelyBackfill':True,'receiveLagDays':30,'summaryText':'old','summaryTruncated':False},
        {'id':'new1','url':'https://example.com/new1','feedId':'feed/1','feedTitle':'F1','feedSiteUrl':'https://example.com/f1','feedKey':'feed/1','title':'new','isLikelyBackfill':False,'receiveLagDays':0.2,'summaryText':'new','summaryTruncated':False},
        {'id':'old2','url':'https://example.com/old2','feedId':'feed/2','feedTitle':'F2','feedSiteUrl':'https://example.com/f2','feedKey':'feed/2','title':'old2','isLikelyBackfill':True,'receiveLagDays':90,'summaryText':'old2','summaryTruncated':True},
        {'id':'new2','url':'https://example.com/new2','feedId':'feed/2','feedTitle':'F2','feedSiteUrl':'https://example.com/f2','feedKey':'feed/2','title':'new2','isLikelyBackfill':False,'receiveLagDays':1,'summaryText':'new2','summaryTruncated':False},
    ]
    raw={'schemaVersion':2,'page':1,'cutoffTimestampSec':100,'nextCursor':None,'hasMore':False,'fetchedCount':4,'filteredOutBeforeCutoff':0,'duplicateSourceItems':0,'rawCount':4,'articles':articles}
    (run/'raw-page-001.json').write_text(json.dumps(raw),encoding='utf-8')
    ids=[a['id'] for a in articles]; dig=m.snapshot_hash('fetch-fixture',100,ids)
    manifest={'schemaVersion':2,'edition':'morning','hours':18,'runId':'morning-fixture','snapshotId':dig[:32],'snapshotHash':dig,'sourceFetchRunId':'fetch-fixture','sourceFetchFinishedAt':'2026-08-11T00:00:00Z','normalCutoffTimestampSec':100,'effectiveCutoffTimestampSec':100,'cutoffTimestampSec':100,'recoveryFromWatermark':False,'watermarkAnchorTimestampSec':None,'pageCount':1,'allPagesComplete':True,'totalRawArticles':4,'runDirectory':str(run)}
    manifest_path=td/'snapshot.json'; manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    def fake_classifier(edition,batch_data,runid):
        acct=[]; candidates=[]
        for idx,a in enumerate(batch_data['articles']):
            if idx==0:
                acct.append({'id':a['id'],'disposition':'candidate'})
                candidates.append({'primaryArticleId':a['id'],'eventKeyCandidate':a['url'],'title':a['title'],'originalTitle':a['title'],'priority':'S','category':'S-链上金融','summary':'s','whyImportant':'w','impact':'i','isHighValueOpportunity':False,'needsVerification':False,'sources':[{'name':'x','url':a['url']}]})
            else:
                acct.append({'id':a['id'],'disposition':'filtered'})
        return {'schemaVersion':2,'edition':edition,'batch':batch_data['batch'],'rawCount':len(acct),'backfillCount':0,'filteredLowValueCount':len(acct)-len(candidates),'mergedDuplicateArticleCount':0,'candidateArticleCount':len(candidates),'accounting':acct,'candidates':candidates}
    m.call_classifier_validated=fake_classifier
    old_argv=sys.argv[:]
    try:
        sys.argv=['prepare-daily.py','morning','--snapshot',str(manifest_path),'--reuse']
        m.main()
    finally:
        sys.argv=old_argv
    result=json.loads((m.ROOT/'current/morning-candidates.json').read_text(encoding='utf-8'))
    assert result['schemaVersion']==2 and result['totalRawArticles']==4 and result['accountedRawArticles']==4
    assert result['hostBootstrapBackfillArticles']==2 and result['modelInputArticles']==2 and result['classifierBatchCount']==1
    accounting=json.loads((run/'raw-accounting.json').read_text(encoding='utf-8'))
    assert {x['id'] for x in accounting}==set(ids)
    assert sum(1 for x in accounting if x['accountingSource']=='host-bootstrap-quarantine')==2

    # After a successful bootstrap, the same historical inventory IDs stay quarantined in a later window.
    # A genuinely new delayed article ID from an already bootstrapped feed must still return to the model path.
    m.BOOTSTRAP_STATE.write_text(json.dumps({
        'schemaVersion':2,
        'feeds':{'feed/1':{},'feed/2':{}},
        'quarantinedArticleIds':{
            'old1':{'feedKey':'feed/1','contentFingerprint':m.article_content_fingerprint(articles[0])},
            'old2':{'feedKey':'feed/2','contentFingerprint':m.article_content_fingerprint(articles[2])},
        },
    }),encoding='utf-8')
    delayed_new={'id':'old3-new-arrival','url':'https://example.com/old3','feedId':'feed/1','feedTitle':'F1','feedSiteUrl':'https://example.com/f1','feedKey':'feed/1','title':'delayed','isLikelyBackfill':True,'receiveLagDays':20,'summaryText':'delayed','summaryTruncated':False}
    run2=td/'run2'; run2.mkdir()
    plan2=m.create_or_load_plan('afternoon',dict(manifest,edition='afternoon',hours=6,runId='afternoon-fixture-2',snapshotId='s2',snapshotHash='h2'),articles+[delayed_new],run2)
    assert plan2['hostBootstrapBackfillArticles']==2, plan2
    assert plan2['persistentBootstrapQuarantineHits']==2, plan2
    assert plan2['newBootstrapQuarantineArticles']==0, plan2
    assert plan2['modelInputArticles']==3, plan2
    model2=[]
    for meta in plan2['batches']:
        model2.extend(json.loads((run2/meta['file']).read_text(encoding='utf-8'))['articles'])
    assert 'old3-new-arrival' in {a['id'] for a in model2}
    assert not {'old1','old2'} & {a['id'] for a in model2}

    # Same quarantined ID with changed content must be rescued back to the model path.
    changed_old1=dict(articles[0], summaryText='old content materially changed')
    run2b=td/'run2b'; run2b.mkdir()
    plan2b=m.create_or_load_plan('afternoon',dict(manifest,edition='afternoon',hours=6,runId='afternoon-fixture-2b',snapshotId='s2b',snapshotHash='h2b'),[changed_old1,articles[2]],run2b)
    assert plan2b['persistentBootstrapQuarantineHits']==1, plan2b
    assert plan2b['quarantineFingerprintMismatchArticles']==1, plan2b
    assert plan2b['quarantineFingerprintMismatchArticleIds']==['old1'], plan2b
    assert plan2b['modelInputArticles']==1, plan2b
    changed_model=[]
    for meta in plan2b['batches']:
        changed_model.extend(json.loads((run2b/meta['file']).read_text(encoding='utf-8'))['articles'])
    assert {a['id'] for a in changed_model}=={'old1'}

    # Dynamic dual limit: both item count and input characters constrain a batch.
    long_articles=[]
    for i in range(30):
        long_articles.append({'id':f'l{i}','url':f'https://example.com/l{i}','feedId':'f','feedTitle':'f','feedKey':'f','summaryText':'x'*2000})
    batches=m.make_batches(long_articles)
    assert len(batches)>1
    assert all(len(items)<=m.MAX_CLASSIFIER_ARTICLES for items,_ in batches)
    assert all(chars<=m.MAX_CLASSIFIER_INPUT_CHARS for _,chars in batches)

    # Yield is control flow, not a failure. It preserves the plan before any model call.
    run3=td/'run3'; run3.mkdir()
    raw3=dict(raw,articles=[articles[1]],rawCount=1,fetchedCount=1)
    (run3/'raw-page-001.json').write_text(json.dumps(raw3),encoding='utf-8')
    dig3=m.snapshot_hash('fetch-yield',100,['new1'])
    man3=dict(manifest,runId='morning-yield',snapshotId=dig3[:32],snapshotHash=dig3,sourceFetchRunId='fetch-yield',totalRawArticles=1,runDirectory=str(run3))
    man3p=td/'snapshot3.json'; man3p.write_text(json.dumps(man3),encoding='utf-8')
    old_slice=m.PREPARE_SLICE_SECONDS; m.PREPARE_SLICE_SECONDS=-1
    called={'n':0}
    def should_not_call(*a,**k): called['n']+=1; raise AssertionError('classifier should not run before yield')
    old_classifier=m.call_classifier_validated; m.call_classifier_validated=should_not_call
    old_argv=sys.argv[:]
    try:
        sys.argv=['prepare-daily.py','morning','--snapshot',str(man3p),'--reuse']
        try:
            m.main(); raise AssertionError('expected yield')
        except SystemExit as exc:
            assert exc.code==75
    finally:
        sys.argv=old_argv; m.PREPARE_SLICE_SECONDS=old_slice; m.call_classifier_validated=old_classifier
    assert called['n']==0

print('DAILY_V52_FIXTURE_OK')
