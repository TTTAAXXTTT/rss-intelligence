#!/usr/bin/env python3
import csv,json,os,subprocess,sys,tempfile,xml.etree.ElementTree as ET
from pathlib import Path
pkg=Path(sys.argv[1]).resolve(); merge=pkg/'01-init-kit/merge-init-reviews.py'
with tempfile.TemporaryDirectory(prefix='rss-v52-init-fixture-') as td:
    r=Path(td); rev=r/'reviews'; out=r/'result'; rev.mkdir(); out.mkdir()
    (out/'baseline.json').write_text(json.dumps({'subscriptions':[{'title':'Existing','feedUrl':'https://existing.example/feed.xml','categories':['S-链上金融']}]},ensure_ascii=False),encoding='utf-8')
    def wr(name,bid,cat,changes):
        obj={'schemaVersion':1,'reviewId':name,'status':'pending','reviewDate':'2026-08-08','generatedAt':'2026-08-08T00:00:00Z','savedAt':'2026-08-08T00:00:01Z','summaryMarkdown':f'INIT_V52_BATCH_ID={bid}\nINIT_V52_CATEGORY={cat}\n','changes':changes}
        (rev/f'{name}.json').write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    wr('r1','B1','S-链上金融',[{'action':'add','feedUrl':'https://a.example/feed.xml','feedTitle':'A','toCategory':'S-链上金融','score':90,'reason':'good'},{'action':'add','feedUrl':'https://existing.example/feed.xml','feedTitle':'Old','toCategory':'S-链上金融','score':92,'reason':'already'}])
    wr('r2','B2','S-链上金融',[{'action':'add','feedUrl':'https://a.example/feed.xml','feedTitle':'A duplicate','toCategory':'S-链上金融','score':85,'reason':'dup'}])
    wr('r3','B3','S-跨境金融',[{'action':'add','feedUrl':'https://wrong.example/feed.xml','feedTitle':'Wrong','toCategory':'S-链上金融','score':95,'reason':'wrong cat'}])
    env=os.environ.copy(); env['RSS_INIT_REVIEWS_DIR']=str(rev); env['RSS_INIT_RESULT_ROOT']=str(out)
    cp=subprocess.run([sys.executable,str(merge)],env=env,capture_output=True,text=True,check=True)
    lines=dict(line.split('=',1) for line in cp.stdout.splitlines() if '=' in line)
    assert lines['MASTER_COUNT']=='1'
    assert lines['ALREADY_SUBSCRIBED_SKIPPED']=='1'
    assert lines['DUPLICATE_RECORDS']=='1'
    assert lines['INVALID_STRUCTURED_URLS']=='1'
    rows=list(csv.DictReader((out/'final/rss-init-master.csv').open(encoding='utf-8-sig')))
    assert len(rows)==1 and rows[0]['feedUrl']=='https://a.example/feed.xml' and rows[0]['score']=='90.0'
    dups=list(csv.DictReader((out/'final/rss-init-duplicates.csv').open(encoding='utf-8-sig'))); assert len(dups)==1
    existing=list(csv.DictReader((out/'final/rss-init-already-subscribed.csv').open(encoding='utf-8-sig'))); assert len(existing)==1
    invalid=(out/'final/invalid-structured-urls.txt').read_text(encoding='utf-8'); assert 'invalid toCategory' in invalid
    ET.parse(out/'final/rss-initial-library.opml')
print('INIT_MERGE_V52_FIXTURE_OK')
