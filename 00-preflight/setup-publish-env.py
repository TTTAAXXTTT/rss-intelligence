#!/usr/bin/env python3
import os,re,secrets,shutil,tempfile
from datetime import datetime
from pathlib import Path
p=Path('/home/ubuntu/.openclaw/.env')
if not p.exists(): raise SystemExit('STOP_GLOBAL_ENV_MISSING')
mode=p.stat().st_mode & 0o777
if mode!=0o600: raise SystemExit(f'STOP_GLOBAL_ENV_MODE_{mode:o}_EXPECTED_600')
lines=p.read_text(encoding='utf-8').splitlines(); values={}
for line in lines:
 s=line.strip()
 if s and not s.startswith('#') and '=' in s:
  k,v=s.split('=',1); values[k.strip()]=v.strip().strip('"\'')
token=values.get('RSS_PUBLISH_TOKEN','')
status='reused'
if token:
 if not re.fullmatch(r'[A-Fa-f0-9]{64}',token): raise SystemExit('STOP_EXISTING_RSS_PUBLISH_TOKEN_INVALID')
else:
 token=secrets.token_hex(32); status='generated'
updates={
 'RSS_PUBLISH_TOKEN':token,
 'RSS_PUBLISH_DATA_ROOT':'/home/ubuntu/.openclaw/rss-intelligence/publisher-data-v52',
 'RSS_PUBLISH_PUBLIC_ROOT':'/var/www/rss-intelligence',
 'RSS_PUBLISH_BASE_URL':'https://rss.example.com/intelligence',
}
out=[]; seen=set()
for line in lines:
 s=line.strip()
 if s and not s.startswith('#') and '=' in s:
  k=s.split('=',1)[0].strip()
  if k in updates:
   if k not in seen: out.append(f'{k}={updates[k]}'); seen.add(k)
   continue
 out.append(line)
for k,v in updates.items():
 if k not in seen: out.append(f'{k}={v}')
stamp=datetime.now().strftime('%Y%m%d-%H%M%S'); backup=p.with_name(p.name+f'.before-rss-v52-{stamp}'); shutil.copy2(p,backup); os.chmod(backup,0o600)
fd,tmp=tempfile.mkstemp(prefix='.env.v52.',dir=p.parent); os.close(fd); Path(tmp).write_text('\n'.join(out).rstrip()+'\n',encoding='utf-8'); os.chmod(tmp,0o600); os.replace(tmp,p); os.chmod(p,0o600)
print(f'RSS_PUBLISH_ENV_OK token={status} backup={backup}')
