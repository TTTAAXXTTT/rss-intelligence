#!/usr/bin/env python3
import csv, hashlib, json, math, os, re
from pathlib import Path
ROOT=Path(os.environ.get('RSS_INIT_RESULT_ROOT','/home/ubuntu/.openclaw/workspace/rss-init-v52'))
PKG=Path(os.environ.get('RSS_V5_PKG','/home/ubuntu/rss-intelligence-v5.2'))
targets=json.loads((PKG/'01-init-kit/coverage-targets.json').read_text(encoding='utf-8'))
master=ROOT/'final/rss-init-master.csv'; counts={}
if master.exists():
    with master.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f): counts[r['category']]=counts.get(r['category'],0)+1
out=ROOT/'coverage-gaps.tsv'; lines=['id\tcategory\ttopic']
remaining=[]
for cat,target in targets.items():
    cur=counts.get(cat,0); gap=max(0,target-cur)
    print(f'{cat}: current={cur} target={target} gap={gap}')
    if gap:
        remaining.append((cat,gap))
        batches=min(4,math.ceil(gap/2))
        base=re.sub(r'[^A-Za-z0-9]+','_',cat).strip('_') or 'CAT'
        slug=f'{base}_{hashlib.sha1(cat.encode("utf-8")).hexdigest()[:6]}'
        for i in range(1,batches+1):
            topic=f'Supplement coverage gap for {cat}; prioritize missing regions, local-language first-party sources, regulators, official research, unique non-duplicate sources.'
            lines.append(f'GAP_{slug}_{i:02d}\t{cat}\t{topic}')
out.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(f'GAP_CATEGORY_COUNT={len(remaining)}')
print(f'GAP_BATCH_FILE={out}')
if remaining: print('COVERAGE_GAP_PASS_NEEDED')
else: print('COVERAGE_TARGETS_MET')
