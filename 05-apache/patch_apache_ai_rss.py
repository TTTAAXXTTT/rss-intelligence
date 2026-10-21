#!/usr/bin/env python3
import argparse, glob, os, re, shutil
from pathlib import Path
from datetime import datetime

p=argparse.ArgumentParser()
p.add_argument('--check',action='store_true')
p.add_argument('--apply',action='store_true')
a=p.parse_args()
if a.check==a.apply:
    raise SystemExit('Usage: patch_apache_ai_rss.py --check | --apply')

vhost_re=re.compile(r'(?is)<VirtualHost\b[^>]*>.*?</VirtualHost>')
server_re=re.compile(r'(?im)^\s*ServerName\s+rss\.example\.com\s*$')
https_open_re=re.compile(r'(?is)^<VirtualHost\b[^>]*:443\b[^>]*>')
marker='# BEGIN RSS INTELLIGENCE STATIC'

matches=[]
for enabled in glob.glob(os.environ.get('APACHE_SITES_ENABLED_GLOB','/etc/apache2/sites-enabled/*.conf')):
    try:
        path=Path(enabled).resolve()
        text=path.read_text(encoding='utf-8')
    except Exception:
        continue
    for m in vhost_re.finditer(text):
        block=m.group(0)
        if https_open_re.search(block) and server_re.search(block):
            matches.append((path,text,m.start(),m.end(),block))

# Deduplicate identical resolved file+block offsets when enabled symlinks alias the same vhost.
uniq={(str(x[0]),x[2],x[3]):x for x in matches}
if len(uniq)!=1:
    raise SystemExit(f'STOP: expected exactly one HTTPS :443 VirtualHost for rss.example.com, found {len(uniq)}')

path,text,start,end,block=next(iter(uniq.values()))
print(f'TARGET={path}')
if marker in block:
    print('ALREADY_PATCHED=true')
    print('CHECK_OK=true')
    raise SystemExit(0)

catch=re.compile(r'(?im)^(?P<indent>\s*)ProxyPass\s+(?:"/?"|/?)\s+(?:"?http://127\.0\.0\.1:8080/?"?)(?:\s+(?:nocanon|retry=\S+|connectiontimeout=\S+|timeout=\S+))*\s*$')
cm=list(catch.finditer(block))
if len(cm)!=1:
    raise SystemExit(f'STOP: expected one HTTPS catch-all ProxyPass / -> 127.0.0.1:8080, found {len(cm)}')
print('PROXYPASS='+cm[0].group(0).strip())
print('CHECK_OK=true')
if a.check:
    raise SystemExit(0)

insert='''# BEGIN RSS INTELLIGENCE STATIC
ProxyPass /intelligence/ !
Alias /intelligence/ /var/www/rss-intelligence/
<Directory "/var/www/rss-intelligence/">
    Options -Indexes
    AllowOverride None
    Require all granted
</Directory>
<Location "/intelligence/">
    Header always set X-Robots-Tag "noindex, nofollow, noarchive"
</Location>
# END RSS INTELLIGENCE STATIC
'''
block_pos=cm[0].start()
new_block=block[:block_pos]+insert+'\n'+block[block_pos:]
new_text=text[:start]+new_block+text[end:]
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup=path.with_name(path.name+f'.before-rss-intelligence-v52-{stamp}')
shutil.copy2(path,backup)
tmp=path.with_name(path.name+'.v5.tmp')
tmp.write_text(new_text,encoding='utf-8')
os.replace(tmp,path)
print(f'PATCHED={path}')
print(f'BACKUP={backup}')
print('APPLY_OK=true')
