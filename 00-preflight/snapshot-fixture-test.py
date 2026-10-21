#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

pkg = Path(sys.argv[1]).resolve()
pipeline = pkg / '04-daily-pipeline'
sys.path.insert(0, str(pipeline))
from daily_common import normalize_article
spec = importlib.util.spec_from_file_location('snapshot_daily_v52_fixture', pipeline / 'snapshot-daily.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory(prefix='rss-v52-snapshot-fixture-') as td:
    td = Path(td)
    m.ROOT = td / 'workspace'
    m.MAX_SNAPSHOT_PAGES = 10
    source = {'schemaVersion':2,'status':'ok','runId':'fetch-fixture','finishedAt':'2026-08-11T00:00:00Z'}
    m.fresh_state = lambda require_fresh=True: source
    m.compute_cutoff = lambda hours: (1000, 900, True, 800)
    m.fresh_config = lambda: ('https://example.invalid/api/greader.php','u','p')
    m.login_freshrss = lambda *a: 'auth'
    m.subscription_map = lambda *a: {}
    m.assert_source_run_unchanged = lambda runid: None
    calls=[]
    def article(aid, backfill=False):
        return {'id':aid,'url':f'https://example.com/{aid}','feedId':'feed/1','feedTitle':'F','feedSiteUrl':'https://example.com','feedKey':'feed/1','isLikelyBackfill':backfill,'summaryTruncated':False}
    def fake_fetch(api,auth,subs,hours,cursor,cutoff,page):
        calls.append(page)
        if page==1:
            return {'schemaVersion':2,'page':1,'cutoffTimestampSec':int(cutoff),'nextCursor':'c2','hasMore':True,'fetchedCount':1,'filteredOutBeforeCutoff':0,'duplicateSourceItems':0,'rawCount':1,'articles':[article('a1')]}
        if page==2:
            return {'schemaVersion':2,'page':2,'cutoffTimestampSec':int(cutoff),'nextCursor':None,'hasMore':False,'fetchedCount':1,'filteredOutBeforeCutoff':0,'duplicateSourceItems':0,'rawCount':1,'articles':[article('a2',True)]}
        raise AssertionError(page)
    m.fetch_page=fake_fetch
    old_argv=sys.argv[:]
    try:
        sys.argv=['snapshot-daily.py','morning']
        m.main()
        manifest=json.loads((m.ROOT/'current/morning-snapshot.json').read_text(encoding='utf-8'))
        assert manifest['schemaVersion']==2 and manifest['allPagesComplete'] is True
        assert manifest['pageCount']==2 and manifest['totalRawArticles']==2 and manifest['likelyBackfillArticles']==1
        first_calls=list(calls)
        sys.argv=['snapshot-daily.py','morning','--reuse']
        m.main()
        assert calls==first_calls, 'completed immutable snapshot was unexpectedly fetched again'
    finally:
        sys.argv=old_argv

# Capacity is detected by Host-only snapshot before any model phase exists.
with tempfile.TemporaryDirectory(prefix='rss-v52-snapshot-capacity-') as td:
    td=Path(td); m.ROOT=td/'workspace'; m.MAX_SNAPSHOT_PAGES=2
    m.fresh_state=lambda require_fresh=True:{'schemaVersion':2,'status':'ok','runId':'fetch-cap','finishedAt':'2026-08-11T00:00:00Z'}
    m.compute_cutoff=lambda hours:(1000,900,False,None)
    m.fresh_config=lambda:('https://example.invalid/api/greader.php','u','p')
    m.login_freshrss=lambda *a:'auth'; m.subscription_map=lambda *a:{}; m.assert_source_run_unchanged=lambda r:None
    def forever(api,auth,subs,hours,cursor,cutoff,page):
        a={'id':f'x{page}','url':f'https://example.com/x{page}','feedId':'feed/x','feedTitle':'X','feedSiteUrl':'https://example.com','feedKey':'feed/x','isLikelyBackfill':False,'summaryTruncated':False}
        return {'schemaVersion':2,'page':page,'cutoffTimestampSec':int(cutoff),'nextCursor':f'c{page+1}','hasMore':True,'fetchedCount':1,'filteredOutBeforeCutoff':0,'duplicateSourceItems':0,'rawCount':1,'articles':[a]}
    m.fetch_page=forever
    old_argv=sys.argv[:]
    try:
        sys.argv=['snapshot-daily.py','morning']
        try:
            m.main(); raise AssertionError('expected capacity failure')
        except RuntimeError as exc:
            assert 'SNAPSHOT_CAPACITY_EXCEEDED' in str(exc)
    finally:
        sys.argv=old_argv

# Missing FreshRSS IDs receive a stable synthetic ID independent of page/ordinal.
raw_item={'title':'No ID','published':1786000000,'crawlTimeMsec':1786000100000,'origin':{'streamId':'feed/x','htmlUrl':'https://example.com'},'alternate':[{'href':'https://example.com/item'}],'summary':{'content':'same'}}
subs={'feed/x':{'title':'X','siteUrl':'https://example.com','categories':[]}}
a1,_=normalize_article(raw_item,subs,0,1,1)
a2,_=normalize_article(raw_item,subs,0,99,40)
assert a1['id'].startswith('synthetic:') and a1['id']==a2['id']

print('SNAPSHOT_V52_FIXTURE_OK')
