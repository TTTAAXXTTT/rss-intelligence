#!/usr/bin/env python3
import json,re,xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path('/home/ubuntu/.openclaw/rss-intelligence')
WORK=Path('/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current')
ENV=Path('/home/ubuntu/.openclaw/.env')
today=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def env():
    d={}
    for raw in ENV.read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if line and not line.startswith('#') and '=' in line:
            k,v=line.split('=',1); d[k.strip()]=v.strip().strip('"\'')
    return d

errors=[]
try:
    r=load(ROOT/'fetch-state-v52/last-run.json'); s=load(ROOT/'fetch-state-v52/last-success.json')
    if r.get('schemaVersion')!=2 or s.get('schemaVersion')!=2: errors.append('V5.2 FreshRSS fetch schema invalid')
    if r.get('status')!='ok' or r.get('runId')!=s.get('runId'): errors.append('V5.2 latest fetch is not the latest success')
except Exception as e: errors.append('V5.2 FreshRSS fetch state invalid: '+str(e))
try:
    ms=load(WORK/'morning-snapshot.json'); mc=load(WORK/'morning-candidates.json')
    if ms.get('schemaVersion')!=2 or ms.get('allPagesComplete') is not True: errors.append('V5.2 Morning snapshot incomplete')
    if mc.get('schemaVersion')!=2 or mc.get('allPagesComplete') is not True: errors.append('V5.2 Morning candidate file incomplete')
    if mc.get('snapshotHash')!=ms.get('snapshotHash'): errors.append('V5.2 Morning candidate/snapshot hash mismatch')
    if int(mc.get('accountedRawArticles',-1))!=int(mc.get('totalRawArticles',-2)): errors.append('V5.2 Morning raw accounting incomplete')
except Exception as e: errors.append('V5.2 Morning snapshot/candidate state invalid: '+str(e))
try:
    m=load(ROOT/'daily-state-v52/latest-morning-final.json')
    if m.get('schemaVersion')!=2 or m.get('edition')!='morning' or m.get('dateKey')!=today: errors.append('V5.2 Morning E2E not found for today')
except Exception as e: errors.append('Morning final state invalid: '+str(e))
try:
    a=load(ROOT/'daily-state-v52/last-success.json')
    if a.get('schemaVersion')!=2 or a.get('edition')!='afternoon' or a.get('reportDateKey')!=today: errors.append('V5.2 Afternoon E2E not found for today')
except Exception as e: errors.append('Afternoon success state invalid: '+str(e))
try:
    bs=load(ROOT/'bootstrap-state-v52.json')
    if bs.get('schemaVersion')!=2 or not isinstance(bs.get('feeds'),dict) or not isinstance(bs.get('quarantinedArticleIds',{}),dict): errors.append('V5.2 bootstrap state invalid')
except Exception as e: errors.append('Bootstrap state invalid: '+str(e))
try:
    rr=load(WORK/'review-run.json')
    wanted=str(rr.get('runId') or ''); kind=str(rr.get('reviewKind') or '')
    if rr.get('schemaVersion')!=2: errors.append('V5.2 review run schema invalid')
    if rr.get('reviewDate') != today: errors.append('V5.2 current review run is not for today')
    if not wanted: errors.append('V5.2 current review runId missing')
    if kind != 'e2e': errors.append('V5.2 E2E gate requires reviewKind=e2e')
    if wanted and kind:
        run_re=re.compile(r'(?m)^REVIEW_RUN_ID='+re.escape(wanted)+r'\s*$')
        kind_re=re.compile(r'(?m)^REVIEW_KIND='+re.escape(kind)+r'\s*$')
        matched=False
        for rp in (ROOT/'reviews').glob('*.json'):
            try: rd=load(rp); sm=rd.get('summaryMarkdown')
            except Exception: continue
            if rd.get('status')=='pending' and isinstance(sm,str) and run_re.search(sm) and kind_re.search(sm): matched=True; break
        if not matched: errors.append('V5.2 Review E2E Pending Review for current runId missing')
except Exception as e: errors.append('Review E2E state invalid: '+str(e))
try:
    ee=env(); token=ee.get('RSS_PUBLISH_TOKEN',''); pubroot=Path(ee.get('RSS_PUBLISH_PUBLIC_ROOT','/var/www/rss-intelligence')); pub=pubroot/token
    if not re.fullmatch(r'[A-Fa-f0-9]{64}',token): errors.append('RSS_PUBLISH_TOKEN invalid')
    else:
        for name in ('all.xml','priority.xml','reports.xml'):
            p=pub/name
            if not p.is_file(): errors.append(f'{name} missing')
            else:
                try: ET.parse(p)
                except Exception as e: errors.append(f'{name} XML invalid: {e}')
except Exception as e: errors.append('public RSS verification failed: '+str(e))
if errors:
    for e in errors: print('FAIL',e)
    print('V52_E2E_NOT_READY'); raise SystemExit(1)
out={'schemaVersion':2,'status':'ok','verifiedAt':datetime.now().astimezone().isoformat(),'dateKey':today,'checks':['v52-fresh-fetch','immutable-snapshot','exact-raw-accounting','bootstrap-state','morning','afternoon','review','public-rss-xml']}
p=ROOT/'v52-e2e-ready.json'; t=p.with_suffix('.tmp'); t.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); t.chmod(0o600); t.replace(p); p.chmod(0o600)
print('V52_E2E_READY file='+str(p))
