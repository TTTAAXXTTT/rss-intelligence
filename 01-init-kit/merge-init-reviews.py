#!/usr/bin/env python3
import csv, json, os, re, sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape, quoteattr

REV=Path(os.environ.get('RSS_INIT_REVIEWS_DIR','/home/ubuntu/.openclaw/rss-intelligence/reviews'))
ROOT=Path(os.environ.get('RSS_INIT_RESULT_ROOT','/home/ubuntu/.openclaw/workspace/rss-init-v52'))
BASE=ROOT/'baseline.json'; FINAL=ROOT/'final'; FINAL.mkdir(parents=True,exist_ok=True)
RAW_URL=re.compile(r'^https?://[^\s\[\]<>]+$',re.I)
MARK=re.compile(r'(?m)^INIT_V52_BATCH_ID=([^\s]+)\s*$')
CAT_MARK=re.compile(r'(?m)^INIT_V52_CATEGORY=(.+?)\s*$')
ALLOWED_CATEGORIES={'S-链上金融','S-跨境金融','S-商业机会','A-AI-Agent','A-AI工具自动化','A-微信企微私域','A-AIxCryptoPayment','B-海外互联网','B-开源自托管','B-政策监管','C-宏观流动性','C-消费科技'}

def norm(u):
    u=u.strip()
    if not RAW_URL.match(u): return None
    try:
        s=urlsplit(u)
        host=(s.hostname or '').lower()
        port=s.port
    except Exception:
        return None
    if s.scheme.lower() not in ('http','https') or not host or host == '...': return None
    netloc=host
    if ':' in host and not host.startswith('['): netloc=f'[{host}]'
    if port: netloc+=f':{port}'
    path=s.path or '/'
    return urlunsplit((s.scheme.lower(),netloc,path,s.query,''))

def existing():
    out=set()
    try:
        d=json.loads(BASE.read_text(encoding='utf-8'))
        for x in d.get('subscriptions',[]):
            n=norm(str(x.get('feedUrl','')))
            if n: out.add(n)
    except Exception: pass
    return out

rows=[]; seen_batches=set(); bad_files=[]
for f in sorted(REV.glob('*.json')):
    try: d=json.loads(f.read_text(encoding='utf-8'))
    except Exception: continue
    if d.get('status') != 'pending':
        continue
    sm=str(d.get('summaryMarkdown',''))
    m=MARK.search(sm); cm=CAT_MARK.search(sm)
    if not m or not cm: continue
    bid=m.group(1); expected_cat=cm.group(1).strip()
    if expected_cat not in ALLOWED_CATEGORIES:
        bad_files.append(f'{bid}: invalid INIT_V52_CATEGORY {expected_cat!r}')
        continue
    seen_batches.add(bid)
    for c in d.get('changes',[]) or []:
        if c.get('action')!='add': continue
        score=c.get('score')
        try: score=float(score)
        except Exception: continue
        if score<82: continue
        cat=str(c.get('toCategory','')).strip()
        if cat != expected_cat or cat not in ALLOWED_CATEGORIES:
            bad_files.append(f'{bid}: invalid toCategory {cat!r}, expected {expected_cat!r}')
            continue
        u=str(c.get('feedUrl','')).strip(); n=norm(u)
        if not n: bad_files.append(f'{bid}: invalid feedUrl {u!r}'); continue
        rows.append({'batchId':bid,'title':str(c.get('feedTitle','')).strip() or n,
                     'feedUrl':u,'norm':n,'category':cat,
                     'score':score,'reason':str(c.get('reason','')).strip()})

exist=existing(); skipped=0; by={}; duplicates=[]; existing_rows=[]
for r in rows:
    if r['norm'] in exist:
        skipped+=1; existing_rows.append(r); continue
    cur=by.get(r['norm'])
    if cur is None:
        by[r['norm']]=r
    else:
        if r['score']>cur['score']:
            duplicates.append({'feedUrl':r['feedUrl'],'keptBatch':r['batchId'],'keptTitle':r['title'],'keptScore':r['score'],'droppedBatch':cur['batchId'],'droppedTitle':cur['title'],'droppedScore':cur['score']})
            by[r['norm']]=r
        else:
            duplicates.append({'feedUrl':cur['feedUrl'],'keptBatch':cur['batchId'],'keptTitle':cur['title'],'keptScore':cur['score'],'droppedBatch':r['batchId'],'droppedTitle':r['title'],'droppedScore':r['score']})
master=sorted(by.values(),key=lambda r:(r['category'],-r['score'],r['title'].lower()))

csvp=FINAL/'rss-init-master.csv'
with csvp.open('w',newline='',encoding='utf-8-sig') as fh:
    w=csv.DictWriter(fh,fieldnames=['category','score','title','feedUrl','batchId','reason']); w.writeheader()
    for r in master: w.writerow({k:r[k] for k in w.fieldnames})

dupp=FINAL/'rss-init-duplicates.csv'
with dupp.open('w',newline='',encoding='utf-8-sig') as fh:
    fields=['feedUrl','keptBatch','keptTitle','keptScore','droppedBatch','droppedTitle','droppedScore']; w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(duplicates)
existp=FINAL/'rss-init-already-subscribed.csv'
with existp.open('w',newline='',encoding='utf-8-sig') as fh:
    fields=['category','score','title','feedUrl','batchId','reason']; w=csv.DictWriter(fh,fieldnames=fields); w.writeheader();
    for r in existing_rows: w.writerow({k:r[k] for k in fields})

# Same-host is an audit signal only. Multiple feeds from one official host may be legitimately distinct.
host_groups={}
for r in master:
    h=(urlsplit(r['feedUrl']).hostname or '').lower()
    if h: host_groups.setdefault(h,[]).append(r)
samehostp=FINAL/'rss-init-same-host-review.csv'
with samehostp.open('w',newline='',encoding='utf-8-sig') as fh:
    fields=['host','category','score','title','feedUrl','batchId']; w=csv.DictWriter(fh,fieldnames=fields); w.writeheader()
    for host,grp in sorted(host_groups.items()):
        if len(grp)<2: continue
        for r in grp: w.writerow({'host':host,'category':r['category'],'score':r['score'],'title':r['title'],'feedUrl':r['feedUrl'],'batchId':r['batchId']})

md=FINAL/'rss-init-master.md'
parts=['# RSS Initialization V5.2 Master','','仅包含经过验证且评分 >=82 的 ADD 候选。','']
for cat in sorted({r['category'] for r in master}):
    parts += [f'## {cat}','']
    for r in [x for x in master if x['category']==cat]:
        parts += [f'- **{r["title"]}** — {r["score"]:g} 分',f'  - Feed: {r["feedUrl"]}',f'  - Batch: {r["batchId"]}',f'  - 理由: {r["reason"]}','']
md.write_text('\n'.join(parts),encoding='utf-8')

opml=FINAL/'rss-initial-library.opml'
body=[]
for cat in sorted({r['category'] for r in master}):
    body.append(f'    <outline text={quoteattr(cat)} title={quoteattr(cat)}>')
    for r in [x for x in master if x['category']==cat]:
        body.append(f'      <outline type="rss" text={quoteattr(r["title"])} title={quoteattr(r["title"])} xmlUrl={quoteattr(r["feedUrl"])} />')
    body.append('    </outline>')
opml.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n  <head><title>RSS Intelligence V5.2 Seed Library</title></head>\n  <body>\n'+'\n'.join(body)+'\n  </body>\n</opml>\n',encoding='utf-8')

counts={}
for r in master: counts[r['category']]=counts.get(r['category'],0)+1
print(f'MASTER_COUNT={len(master)}')
print(f'UNIQUE_INIT_BATCHES={len(seen_batches)}')
print(f'ALREADY_SUBSCRIBED_SKIPPED={skipped}')
print(f'DUPLICATE_RECORDS={len(duplicates)}')
print(f'INVALID_STRUCTURED_URLS={len(bad_files)}')
for k in sorted(counts): print(f'{k}={counts[k]}')
print(f'MASTER_MD={md}')
print(f'MASTER_CSV={csvp}')
print(f'DUPLICATES_CSV={dupp}')
print(f'ALREADY_SUBSCRIBED_CSV={existp}')
print(f'SAME_HOST_REVIEW_CSV={samehostp}')
print(f'OPML_PREVIEW={opml}')
if bad_files:
    (FINAL/'invalid-structured-urls.txt').write_text('\n'.join(bad_files)+'\n',encoding='utf-8')
