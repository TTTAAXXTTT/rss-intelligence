#!/usr/bin/env python3
import argparse,json
from pathlib import Path

ap=argparse.ArgumentParser(); ap.add_argument('edition',choices=['morning','afternoon']); ap.add_argument('candidate_run_id'); args=ap.parse_args()
root=Path('/home/ubuntu/.openclaw/rss-intelligence/finalizer-stage-v52')/args.edition
found=[]
if root.is_dir():
    for p in root.glob('*.json'):
        try:d=json.loads(p.read_text(encoding='utf-8'))
        except Exception:continue
        if d.get('schemaVersion')==2 and d.get('status')=='staged' and d.get('edition')==args.edition and d.get('candidateRunId')==args.candidate_run_id:
            found.append((str(d.get('savedAt') or ''),p))
if not found: raise SystemExit(1)
found.sort(reverse=True,key=lambda x:x[0]); print(found[0][1])
