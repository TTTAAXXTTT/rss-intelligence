#!/usr/bin/env python3
import argparse, hashlib, html, json, os, re, stat, tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:
    fcntl = None

GLOBAL_ENV = Path(os.environ.get('OPENCLAW_ENV_FILE','/home/ubuntu/.openclaw/.env'))
CANDIDATE_ROOT = Path(os.environ.get('RSS_DAILY_STAGE_CURRENT','/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current'))
STATE_ROOT = Path(os.environ.get('RSS_DAILY_STATE_ROOT','/home/ubuntu/.openclaw/rss-intelligence/daily-state-v52'))
MAX_ITEMS = 500
MAX_REPORTS = 120
RAW_URL = re.compile(r'^https?://[^\s\[\]<>]+$', re.I)
SH = timezone(timedelta(hours=8))
TRANSLATION_CLOCK_SKEW = timedelta(minutes=5)
TRANSLATION_LIFETIME = timedelta(hours=48)
ALLOWED_CATEGORIES={'S-链上金融','S-跨境金融','S-商业机会','A-AI-Agent','A-AI工具自动化','A-微信企微私域','A-AIxCryptoPayment','B-海外互联网','B-开源自托管','B-政策监管','C-宏观流动性','C-消费科技'}
PRESENTATION_VERSION='content-presentation-v1'
COVERAGE_POLICY='all-stage-items-v1'
CATEGORY_LABELS={
    'A-AI-Agent':'A-智能代理',
    'A-AI工具自动化':'A-智能工具自动化',
    'A-AIxCryptoPayment':'A-智能技术×Crypto×Payment',
}


def load_json(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def iso(v=None):
    if not v: return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def date_key(v): return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(SH).strftime('%Y-%m-%d')

def load_env():
    e={}
    if GLOBAL_ENV.exists():
        for raw in GLOBAL_ENV.read_text(encoding='utf-8').splitlines():
            line=raw.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k,v=line.split('=',1); k=k.strip(); v=v.strip()
            if len(v)>=2 and v[0]==v[-1] and v[0] in ('"',"'"): v=v[1:-1]
            e[k]=v
    e.update(os.environ)
    return e

def ensure_dir(p,mode): p.mkdir(parents=True,exist_ok=True); os.chmod(p,mode)
def atomic_text(p,text,mode):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent); os.close(fd)
    try:
        Path(tmp).write_text(text,encoding='utf-8'); os.chmod(tmp,mode); os.replace(tmp,p); os.chmod(p,mode)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def atomic_json(p,obj,mode=0o600): atomic_text(p,json.dumps(obj,ensure_ascii=False,indent=2)+'\n',mode)
def xml_safe(s):
    # XML 1.0 Fifth Edition permits only TAB/LF/CR and these scalar ranges.
    # Using an allowlist also removes U+FFFE/U+FFFF and lone UTF-16 surrogates,
    # which html.escape() alone would preserve.
    return ''.join(
        char for char in str(s)
        if ord(char) in (0x09, 0x0A, 0x0D)
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )
def esc(s): return html.escape(xml_safe(s),quote=True)
def cdata(s): return xml_safe(s).replace(']]>',']]]]><![CDATA[>')

def valid_url(u):
    if not isinstance(u,str) or not RAW_URL.match(u): return False
    try: z=urlsplit(u)
    except Exception: return False
    return z.scheme.lower() in ('http','https') and bool(z.hostname) and z.hostname!='...'

def render_inline(s):
    x=esc(s)
    x=re.sub(r'`([^`]+)`',r'<code>\1</code>',x)
    x=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',x)
    x=re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)',lambda m:f'<a href="{esc(html.unescape(m.group(2)))}" rel="noopener noreferrer">{m.group(1)}</a>',x)
    return x

def markdown_html(md):
    lines=md.replace('\r','').split('\n'); out=[]; code=[]; in_code=False; list_open=False; ordered=False
    def close_list():
        nonlocal list_open,ordered
        if list_open: out.append('</ol>' if ordered else '</ul>'); list_open=False
    for line in lines:
        if line.startswith('```'):
            close_list()
            if in_code: out.append('<pre><code>'+esc('\n'.join(code))+'</code></pre>'); code=[]; in_code=False
            else: in_code=True
            continue
        if in_code: code.append(line); continue
        m=re.match(r'^(#{1,6})\s+(.*)$',line)
        if m: close_list(); n=len(m.group(1)); out.append(f'<h{n}>{render_inline(m.group(2))}</h{n}>'); continue
        m=re.match(r'^\s*[-*]\s+(.*)$',line)
        if m:
            if not list_open or ordered: close_list(); out.append('<ul>'); list_open=True; ordered=False
            out.append('<li>'+render_inline(m.group(1))+'</li>'); continue
        m=re.match(r'^\s*\d+[.)]\s+(.*)$',line)
        if m:
            if not list_open or not ordered: close_list(); out.append('<ol>'); list_open=True; ordered=True
            out.append('<li>'+render_inline(m.group(1))+'</li>'); continue
        if not line.strip(): close_list(); continue
        close_list(); out.append('<p>'+render_inline(line)+'</p>')
    close_list()
    if in_code: out.append('<pre><code>'+esc('\n'.join(code))+'</code></pre>')
    return '\n'.join(out)

def rss(title,link,items):
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>{esc(title)}</title><link>{esc(link)}</link><description>{esc(title)}</description><lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>{"".join(items)}</channel></rss>\n'

def utc_rfc(v): return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
def event_id(k): return hashlib.sha256(k.strip().encode()).hexdigest()[:32]

def expected_keys_hash(stage):
    keys=sorted(str(item['eventKey']) for item in stage['items'])
    return hashlib.sha256(json.dumps(keys,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def clean_text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def one_sentence(value, fallback='本条内容请查看原文。', maximum=180):
    """Create one readable Chinese sentence without model-authored rewriting."""
    text=clean_text(value) or clean_text(fallback)
    pieces=[part.strip() for part in re.split(r'(?<=[。！？!?])\s*',text) if part.strip()]
    chosen=''
    for part in pieces or [text]:
        chosen=(chosen+part).strip()
        if len(chosen)>=36 or len(pieces)<=1: break
    chosen=chosen[:maximum].rstrip('，,；;：:。！？!? ')
    # A semicolon keeps clauses inside a single sentence while removing accidental
    # multi-sentence report descriptions.
    chosen=re.sub(r'[。！？!?]+\s*', '；', chosen).rstrip('；')
    return (chosen or clean_text(fallback)[:maximum]).rstrip('。')+'。'


def display_category(value):
    return CATEGORY_LABELS.get(str(value),str(value))


def translation_label(row):
    return '阅读中文全文（保留 48 小时）'


def item_html(x):
    sources=''.join(f'<li><a href="{esc(s["url"])}" rel="noopener noreferrer">{esc(s.get("name") or s["url"])}</a></li>' for s in x['sources'])
    parts=[
        f'<p><strong>优先级：</strong>{esc(x["priority"])}　<strong>领域：</strong>{esc(display_category(x["category"]))}</p>',
        f'<p><strong>一句话：</strong>{esc(one_sentence(x.get("summary"),x.get("title")))}</p>',
        f'<h3>内容总结</h3><p>{esc(x["summary"])}</p>',
        f'<h3>为什么重要</h3><p>{esc(x["whyImportant"])}</p>',
        f'<h3>可能影响</h3><p>{esc(x["impact"])}</p>',
    ]
    if x.get('opportunity'): parts.append(f'<h3>机会</h3><p>{esc(x["opportunity"])}</p>')
    if x.get('risk'): parts.append(f'<h3>风险</h3><p>{esc(x["risk"])}</p>')
    if x.get('translatedUrl'):
        parts.append(f'<p><a href="{esc(x["translatedUrl"])}" rel="noopener noreferrer"><strong>{esc(translation_label({"status":x.get("translationStatus")}))}</strong></a></p>')
    parts.append('<h3>来源</h3><ul>'+sources+'</ul>')
    return ''.join(parts)


def rss_item(x):
    body=item_html(x); link=x['sources'][0]['url']; dt=x.get('eventAt') or x.get('publishedAt') or x['lastSeenAt']
    description=one_sentence(x.get('summary'),x.get('title'))
    return f'<item><guid isPermaLink="false">rss-intel:{esc(x["id"])}</guid><title>{esc(x["title"])}</title><link>{esc(link)}</link><pubDate>{utc_rfc(dt)}</pubDate><description>{esc(description)}</description><content:encoded><![CDATA[{cdata(body)}]]></content:encoded></item>'


def report_item(x,base,brief=False):
    suffix='-brief' if brief else ''
    name=f'{x["dateKey"]}-{x["reportType"]}{suffix}.html'; url=f'{base}/{name}'
    markdown=renderable_report_markdown(x,brief)
    body=markdown_html(markdown)
    safe=cdata(body)
    kind='精简' if brief else '完整'
    guid_kind='brief' if brief else 'full'
    title=f'{x["title"]} · {kind}版'
    return f'<item><guid isPermaLink="false">rss-report:{guid_kind}:{esc(x["dateKey"])}:{esc(x["reportType"])}</guid><title>{esc(title)}</title><link>{esc(url)}</link><pubDate>{utc_rfc(x["generatedAt"])}</pubDate><description><![CDATA[{safe}]]></description><content:encoded><![CDATA[{safe}]]></content:encoded></item>'


def renderable_report_markdown(report,brief=False):
    markdown=(report.get('briefMarkdown') if brief else report.get('markdown')) or report.get('markdown') or ''
    expiry=report.get('translationExpiresAt')
    active=False
    if expiry:
        try: active=parse_utc(expiry,'REPORT_TRANSLATION_EXPIRES_AT')>datetime.now(timezone.utc)
        except RuntimeError: active=False
    if not active:
        markdown=re.sub(
            r'[ \t]*\[(?:阅读中文全文（保留 48 小时）|阅读可获取内容的中文译文（保留 48 小时）)\]\(https?://[^\s)]+/translations/[a-f0-9]{32,64}\.html\)(?:[ \t]*·[ \t]*)?',
            ' ',markdown,
        )
        markdown='\n'.join(line.rstrip() for line in markdown.splitlines())
    return markdown


def report_title(edition):
    return '08:00 晨间内容' if edition=='morning' else '14:00 午间更新'


def build_report_markdown(stage,translations):
    edition=stage['edition']; title=report_title(edition); items=stage['items']
    lines=[f'# {title}',f'> 本期共 {len(items)} 条，以下每条均完整提炼并附 48 小时中文全文阅读页。']
    for number,item in enumerate(items,1):
        trans=translations.get(item['eventKey'])
        lines.extend([
            '',f'## {number}. [{item["priority"]}] {clean_text(item["title"])}',
            f'- 分类：{display_category(item["category"])}',
            f'- 一句话：{one_sentence(item.get("summary"),item.get("title"))}',
            f'- 内容总结：{clean_text(item["summary"])}',
            f'- 为什么重要：{clean_text(item["whyImportant"])}',
            f'- 可能影响：{clean_text(item["impact"])}',
        ])
        if item.get('opportunity'): lines.append(f'- 机会：{clean_text(item["opportunity"])}')
        if item.get('risk'): lines.append(f'- 风险：{clean_text(item["risk"])}')
        if item.get('_needsVerification') is True: lines.append('- 核验状态：待原始来源复核')
        if not trans:
            raise RuntimeError('REPORT_TRANSLATION_MISSING')
        lines.append(f'- [{translation_label(trans)}]({trans["translatedUrl"]}) · [查看原文]({item["sources"][0]["url"]})')
    result='\n'.join(lines).strip()+'\n'
    if not 1<=len(result)<=100000: raise RuntimeError('DETAILED_REPORT_LENGTH_INVALID')
    return result


def build_brief_markdown(stage,translations):
    edition=stage['edition']; title=report_title(edition); items=stage['items']
    lines=[f'# {title} · 精简版',f'> {len(items)} 条内容，每条一句话。','']
    for number,item in enumerate(items,1):
        trans=translations.get(item['eventKey'])
        if not trans: raise RuntimeError('BRIEF_TRANSLATION_MISSING')
        link=f' [{translation_label(trans)}]({trans["translatedUrl"]})'
        lines.append(f'{number}. [{item["priority"]}] {one_sentence(item.get("summary"),item.get("title"))}{link}')
    result='\n'.join(lines).strip()+'\n'
    if not 1<=len(result)<=30000: raise RuntimeError('BRIEF_REPORT_LENGTH_INVALID')
    return result


def page_html(report,markdown,brief=False):
    edition=report['reportType']; label='晨间内容' if edition=='morning' else '午间更新'
    kind='精简版' if brief else '完整版'; counts=report.get('priorityCounts') or {}
    chips=''.join(f'<span>{esc(key)} {int(counts.get(key,0))}</span>' for key in ('S','A','B','C') if counts.get(key,0))
    cover=(
        f'<header class="cover"><div class="eyebrow">{esc(report["dateKey"])} · {esc(kind)}</div>'
        f'<h1>{esc(label)}</h1><p>{int(report.get("itemCount",0))} 条重点内容，逐条提炼并提供可用的中文阅读页。</p>'
        f'<div class="chips">{chips}</div></header>'
    )
    body=markdown_html(markdown)
    style='''
:root{color-scheme:light;background:#f3f5f7;color:#17202a}*{box-sizing:border-box}body{margin:0;font:16px/1.75 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:940px;margin:0 auto;padding:28px 18px 64px}.cover{min-height:260px;padding:42px;border-radius:28px;background:linear-gradient(135deg,#0d3b66 0%,#136f63 58%,#f4a261 140%);color:#fff;box-shadow:0 18px 50px #16324f26;margin-bottom:30px;display:flex;flex-direction:column;justify-content:flex-end}.cover .eyebrow{font-size:13px;letter-spacing:.16em;opacity:.82}.cover h1{font-size:44px;line-height:1.12;margin:12px 0}.cover p{font-size:18px;margin:0 0 16px;opacity:.9}.chips{display:flex;gap:8px;flex-wrap:wrap}.chips span{background:#ffffff20;border:1px solid #ffffff45;border-radius:999px;padding:4px 12px}.content{background:#fff;border-radius:22px;padding:26px 34px;box-shadow:0 12px 36px #16324f12}.content>h1:first-child{display:none}.content h2{border-top:1px solid #e7ecef;padding-top:24px;margin-top:30px}.content a{color:#0a6c74;text-decoration-thickness:1px;text-underline-offset:3px}.content li{margin:.42em 0}code{background:#eef2f4;padding:2px 4px}@media(max-width:600px){main{padding:14px 10px 40px}.cover{min-height:220px;padding:26px;border-radius:20px}.cover h1{font-size:34px}.content{padding:20px 18px;border-radius:18px}}
'''
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>{esc(report["title"])} · {esc(kind)}</title><style>{style}</style></head><body><main>{cover}<article class="content">{body}</article></main></body></html>'


def parse_utc(value,name):
    try:
        parsed=datetime.fromisoformat(str(value or '').replace('Z','+00:00'))
    except ValueError as exc:
        raise RuntimeError(f'{name}_INVALID') from exc
    if parsed.tzinfo is None: raise RuntimeError(f'{name}_INVALID')
    return parsed.astimezone(timezone.utc)


def secure_directory(path,mode,name):
    path=Path(path)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink(): raise RuntimeError(f'{name}_INVALID')
    if os.name=='posix' and (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=mode):
        raise RuntimeError(f'{name}_PERMISSION_INVALID')
    return path.resolve(strict=True)


def translation_map(manifest_path,stage,feed_base,pub,translation_root):
    root=secure_directory(translation_root,0o700,'TRANSLATION_ROOT')
    manifests=secure_directory(root/'manifests',0o700,'TRANSLATION_MANIFESTS_ROOT')
    edition_root=secure_directory(manifests/stage['edition'],0o700,'TRANSLATION_EDITION_ROOT')
    if edition_root.parent!=manifests: raise RuntimeError('TRANSLATION_EDITION_ROOT_OUTSIDE_ROOT')
    raw_path=Path(manifest_path)
    raw_info=raw_path.lstat()
    if not stat.S_ISREG(raw_info.st_mode) or raw_path.is_symlink(): raise RuntimeError('TRANSLATION_MANIFEST_FILE_INVALID')
    path=raw_path.resolve(strict=True)
    if path.parent!=edition_root: raise RuntimeError('TRANSLATION_MANIFEST_OUTSIDE_ROOT')
    if os.name=='posix' and (raw_info.st_uid!=os.geteuid() or stat.S_IMODE(raw_info.st_mode)!=0o600):
        raise RuntimeError('TRANSLATION_MANIFEST_PERMISSION_INVALID')
    manifest=load_json(path)
    if (
        manifest.get('schemaVersion')!=1
        or manifest.get('producer')!='host-translation-v1'
        or manifest.get('status')!='complete'
        or manifest.get('coveragePolicy')!=COVERAGE_POLICY
        or manifest.get('candidateRunId')!=stage.get('candidateRunId')
        or manifest.get('stageId')!=stage.get('stageId')
        or manifest.get('edition')!=stage.get('edition')
        or not isinstance(manifest.get('items'),list)
    ):
        raise RuntimeError('TRANSLATION_MANIFEST_IDENTITY_INVALID')
    generated_at=parse_utc(manifest.get('generatedAt'),'TRANSLATION_MANIFEST_GENERATED_AT')
    saved_at=parse_utc(manifest.get('savedAt'),'TRANSLATION_MANIFEST_SAVED_AT')
    expires_at=parse_utc(manifest.get('expiresAt'),'TRANSLATION_MANIFEST_EXPIRES_AT')
    now=datetime.now(timezone.utc)
    if generated_at!=saved_at:
        raise RuntimeError('TRANSLATION_MANIFEST_GENERATED_AT_MISMATCH')
    if saved_at>now+TRANSLATION_CLOCK_SKEW:
        raise RuntimeError('TRANSLATION_MANIFEST_SAVED_AT_IN_FUTURE')
    if expires_at-saved_at!=TRANSLATION_LIFETIME:
        raise RuntimeError('TRANSLATION_MANIFEST_EXPIRY_NOT_48H')
    if expires_at<=now+timedelta(minutes=10):
        raise RuntimeError('TRANSLATION_MANIFEST_TOO_CLOSE_TO_EXPIRY')
    stage_items={str(item['eventKey']):item for item in stage['items']}
    retained={str(row.get('finalEventKey') or ''):str(row.get('primaryArticleId') or '') for row in stage['accounting'] if row.get('disposition')=='retained'}
    expected=set(stage_items)
    if (
        manifest.get('itemCount')!=len(expected)
        or manifest.get('stageItemCount')!=len(expected)
        or manifest.get('expectedKeysSha256')!=expected_keys_hash(stage)
    ):
        raise RuntimeError('TRANSLATION_MANIFEST_COVERAGE_INVALID')
    if set(retained)!=expected: raise RuntimeError('TRANSLATION_RETAINED_MAP_INVALID')
    rows={}
    translations_path=pub/'translations'
    translations_info=translations_path.lstat()
    if not stat.S_ISDIR(translations_info.st_mode) or translations_path.is_symlink():
        raise RuntimeError('TRANSLATION_PUBLIC_ROOT_INVALID')
    translations_root=translations_path.resolve(strict=True)
    for row in manifest['items']:
        if not isinstance(row,dict): raise RuntimeError('TRANSLATION_MANIFEST_ROW_INVALID')
        key=str(row.get('eventKey') or '').strip(); public_file=str(row.get('publicFile') or '').strip()
        url=str(row.get('translatedUrl') or '').strip(); status_value=str(row.get('status') or '')
        primary_id=str(row.get('primaryArticleId') or '').strip(); original_url=str(row.get('originalUrl') or '').strip()
        if not key or key not in expected or key in rows or status_value!='complete':
            raise RuntimeError('TRANSLATION_MANIFEST_ROW_INVALID')
        if row.get('sourceCompleteness')!='full' or row.get('truncated') is not False:
            raise RuntimeError('TRANSLATION_MANIFEST_ROW_NOT_FULL')
        if primary_id!=retained[key] or original_url!=stage_items[key]['sources'][0]['url']:
            raise RuntimeError('TRANSLATION_SOURCE_BINDING_INVALID')
        if not re.fullmatch(r'translations/[a-f0-9]{32,64}\.html',public_file):
            raise RuntimeError('TRANSLATION_PUBLIC_FILE_INVALID')
        expected_url=f'{feed_base}/{public_file}'
        if url!=expected_url: raise RuntimeError('TRANSLATION_PUBLIC_URL_INVALID')
        row_saved=parse_utc(row.get('savedAt'),'TRANSLATION_ROW_SAVED_AT')
        row_expires=parse_utc(row.get('expiresAt'),'TRANSLATION_ROW_EXPIRES_AT')
        if row_saved!=saved_at or row_expires!=expires_at:
            raise RuntimeError('TRANSLATION_ROW_TIME_MISMATCH')
        if row_expires<=now+timedelta(minutes=10): raise RuntimeError('TRANSLATION_ROW_TOO_CLOSE_TO_EXPIRY')
        raw_public_path=pub/public_file
        page_info=raw_public_path.lstat()
        if not stat.S_ISREG(page_info.st_mode) or raw_public_path.is_symlink():
            raise RuntimeError('TRANSLATION_PUBLIC_PAGE_MISSING')
        public_path=raw_public_path.resolve(strict=True)
        if public_path.parent!=translations_root: raise RuntimeError('TRANSLATION_PUBLIC_PAGE_OUTSIDE_ROOT')
        if os.name=='posix' and (page_info.st_uid!=os.geteuid() or stat.S_IMODE(page_info.st_mode)!=0o644):
            raise RuntimeError('TRANSLATION_PUBLIC_PAGE_PERMISSION_INVALID')
        output_hash=str(row.get('outputHash') or '').lower()
        if not re.fullmatch(r'[a-f0-9]{64}',output_hash): raise RuntimeError('TRANSLATION_OUTPUT_HASH_INVALID')
        if hashlib.sha256(public_path.read_bytes()).hexdigest()!=output_hash:
            raise RuntimeError('TRANSLATION_PUBLIC_PAGE_HASH_MISMATCH')
        rows[key]=row
    if set(rows)!=expected: raise RuntimeError('TRANSLATION_EVENTKEY_SET_MISMATCH')
    return rows,manifest

def validate_stage(stage,candidate):
    if stage.get('schemaVersion')!=2 or stage.get('status')!='staged': raise RuntimeError('STAGE_SCHEMA_INVALID')
    if stage.get('edition') not in ('morning','afternoon'): raise RuntimeError('STAGE_EDITION_INVALID')
    if stage.get('candidateRunId')!=candidate.get('runId'): raise RuntimeError('STAGE_CANDIDATE_RUN_MISMATCH')
    if candidate.get('schemaVersion')!=2: raise RuntimeError('CANDIDATE_SCHEMA_INVALID')
    if candidate.get('allPagesComplete') is not True: raise RuntimeError('CANDIDATE_NOT_COMPLETE')
    if stage['edition']!=candidate.get('edition'): raise RuntimeError('STAGE_EDITION_MISMATCH')
    if stage['edition']=='afternoon' and str(stage.get('morningBaselineId') or '')!=str(candidate.get('morningBaselineId') or ''): raise RuntimeError('STAGE_BASELINE_MISMATCH')
    ids={str(c.get('primaryArticleId') or '') for c in candidate.get('candidates') or []}
    if '' in ids or len(ids)!=len(candidate.get('candidates') or []): raise RuntimeError('CANDIDATE_IDS_INVALID')
    acc=stage.get('accounting'); items=stage.get('items')
    if not isinstance(acc,list) or not isinstance(items,list): raise RuntimeError('STAGE_ARRAYS_INVALID')
    if len(items) > (24 if stage['edition']=='morning' else 16): raise RuntimeError('STAGE_ITEM_CAP_EXCEEDED')
    got=[str(a.get('primaryArticleId') or '') for a in acc]
    if len(got)!=len(ids) or set(got)!=ids or len(set(got))!=len(got): raise RuntimeError('STAGE_ACCOUNTING_MISMATCH')
    keys=set()
    retained=set()
    for it in items:
        k=str(it.get('eventKey') or '').strip()
        if len(k)<3 or len(k)>500 or k in keys: raise RuntimeError('STAGE_EVENTKEY_INVALID')
        keys.add(k)
        if it.get('priority') not in ('S','A','B','C'): raise RuntimeError('STAGE_PRIORITY_INVALID')
        if str(it.get('category') or '').strip() not in ALLOWED_CATEGORIES: raise RuntimeError('STAGE_CATEGORY_INVALID')
        for req in ('title','category','summary','whyImportant','impact'):
            if not str(it.get(req) or '').strip(): raise RuntimeError('STAGE_ITEM_REQUIRED_TEXT_MISSING')
        src=it.get('sources')
        if not isinstance(src,list) or not 1<=len(src)<=5 or any(not valid_url(str(s.get('url') or '')) for s in src): raise RuntimeError('STAGE_SOURCE_URL_INVALID')
    for a in acc:
        disp=a.get('disposition'); fk=str(a.get('finalEventKey') or '')
        if disp in ('retained','merged'):
            if fk not in keys: raise RuntimeError('STAGE_ACCOUNTING_EVENTKEY_MISSING')
            if disp=='retained': retained.add(fk)
        elif disp!='filtered': raise RuntimeError('STAGE_DISPOSITION_INVALID')
    if keys-retained: raise RuntimeError('STAGE_ITEM_WITHOUT_RETAINED_PRIMARY')
    md=str(stage.get('reportMarkdown') or '')
    if not 1<=len(md)<=100000: raise RuntimeError('STAGE_REPORT_LENGTH_INVALID')

def commit_bootstrap_state(candidate, env, published_at):
    path=Path(env.get('RSS_BOOTSTRAP_STATE','/home/ubuntu/.openclaw/rss-intelligence/bootstrap-state-v52.json'))
    if path.exists():
        state=load_json(path)
        if (
            state.get('schemaVersion')!=2
            or not isinstance(state.get('feeds'),dict)
            or not isinstance(state.get('quarantinedArticleIds',{}),dict)
        ):
            raise RuntimeError('BOOTSTRAP_STATE_INVALID')
        state.setdefault('quarantinedArticleIds',{})
    else:
        state={'schemaVersion':2,'feeds':{},'quarantinedArticleIds':{}}

    changed=False
    released_count=0
    released_seen=set()
    for raw_id in candidate.get('bootstrapQuarantineReleasedArticleIds') or []:
        article_id=str(raw_id or '').strip()
        if not article_id or article_id in released_seen:
            raise RuntimeError('BOOTSTRAP_QUARANTINE_RELEASE_ID_INVALID')
        released_seen.add(article_id)
        if article_id in state['quarantinedArticleIds']:
            del state['quarantinedArticleIds'][article_id]
            released_count += 1
            changed=True

    feed_count=0
    for feed_key in candidate.get('bootstrapFeedIds') or []:
        key=str(feed_key or '').strip()
        if not key:
            raise RuntimeError('BOOTSTRAP_FEED_KEY_INVALID')
        feed_count += 1
        if key not in state['feeds']:
            state['feeds'][key]={'committedAt':published_at,'firstSuccessfulRunId':candidate.get('runId')}
            changed=True

    quarantine_count=0
    quarantine_file=str(candidate.get('bootstrapQuarantineFile') or '').strip()
    if quarantine_file:
        qp=Path(quarantine_file)
        if qp.name!='bootstrap-quarantine.json' or not qp.is_file():
            raise RuntimeError('BOOTSTRAP_QUARANTINE_FILE_INVALID')
        rows=load_json(qp)
        if not isinstance(rows,list):
            raise RuntimeError('BOOTSTRAP_QUARANTINE_SCHEMA_INVALID')
        expected=int(candidate.get('hostBootstrapBackfillArticles',len(rows)))
        if len(rows)!=expected:
            raise RuntimeError('BOOTSTRAP_QUARANTINE_COUNT_MISMATCH')
        seen=set()
        for row in rows:
            article_id=str(row.get('id') or '').strip()
            feed_key=str(row.get('feedKey') or '').strip()
            fingerprint=str(row.get('contentFingerprint') or '').strip()
            if not article_id or not feed_key or not re.fullmatch(r'[A-Fa-f0-9]{64}',fingerprint) or article_id in seen:
                raise RuntimeError('BOOTSTRAP_QUARANTINE_ROW_INVALID')
            seen.add(article_id)
            quarantine_count += 1
            if article_id not in state['quarantinedArticleIds']:
                state['quarantinedArticleIds'][article_id]={
                    'feedKey':feed_key,
                    'contentFingerprint':fingerprint,
                    'committedAt':published_at,
                    'firstSuccessfulRunId':candidate.get('runId'),
                }
                changed=True

    if changed or not path.exists():
        atomic_json(path,state,0o600)
    return feed_count,quarantine_count,released_count

def sanitized_historical_markdown(value):
    text=str(value or '')
    replacements={
        '# 08:00 情报晨报':'# 08:00 晨间内容',
        '# 14:00 情报更新':'# 14:00 午间更新',
        'AI / Agent重要变化':'智能工具与自动化重要变化',
        'AI / Agent重大变化':'智能工具与自动化重大变化',
    }
    for old,new in replacements.items(): text=text.replace(old,new)
    return text


def historical_brief(report):
    markdown=sanitized_historical_markdown(report.get('markdown') or '')
    rows=[]
    for raw in markdown.splitlines():
        line=clean_text(re.sub(r'^(?:#+|[-*]|\d+[.)])\s*','',raw))
        if not line or line.startswith(('原始 ','本时段','分类：','摘要：','为什么重要：','影响：','机会：','风险：','来源：')): continue
        if line.startswith('[') or len(line)>=18:
            sentence=one_sentence(line,line)
            if sentence not in rows: rows.append(sentence)
        if len(rows)>=24: break
    if not rows: rows=['本期历史内容请打开完整页面查看。']
    return '# '+str(report.get('title') or '历史内容')+' · 精简版\n\n'+''.join(f'{i}. {row}\n' for i,row in enumerate(rows,1))


def candidate_for_stage(stage):
    edition=str(stage.get('edition') or '')
    run_id=str(stage.get('candidateRunId') or '')
    current=CANDIDATE_ROOT/f'{edition}-candidates.json'
    candidate=None
    if current.is_file() and not current.is_symlink():
        candidate=load_json(current)
    if not isinstance(candidate,dict) or candidate.get('runId')!=run_id:
        immutable=CANDIDATE_ROOT.parent/'runs'/run_id/'combined.json'
        if not immutable.is_file() or immutable.is_symlink():
            raise RuntimeError('CANDIDATE_RUN_FILE_MISSING')
        candidate=load_json(immutable)
    return candidate


@contextmanager
def publish_lock(data):
    ensure_dir(data,0o700)
    lock_path=data/'.publish.lock'
    descriptor=os.open(lock_path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode): raise RuntimeError('PUBLISH_LOCK_INVALID')
        os.chmod(lock_path,0o600)
        if fcntl is not None: fcntl.flock(descriptor,fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            try: fcntl.flock(descriptor,fcntl.LOCK_UN)
            except OSError: pass
        os.close(descriptor)


def active_translation(value,pub,feed_base,now):
    try:
        if (
            value.get('translationStatus')!='complete'
            or value.get('translationSourceCompleteness')!='full'
            or value.get('translationTruncated') is not False
            or parse_utc(value.get('translationExpiresAt'),'ITEM_TRANSLATION_EXPIRES_AT')<=now
        ):
            return False
        public_file=str(value.get('translationPublicFile') or '')
        if not re.fullmatch(r'translations/[a-f0-9]{64}\.html',public_file): return False
        if value.get('translatedUrl')!=f'{feed_base}/{public_file}': return False
        output_hash=str(value.get('translationOutputHash') or '').lower()
        if not re.fullmatch(r'[a-f0-9]{64}',output_hash): return False
        path=pub/public_file
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink(): return False
        if os.name=='posix' and (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o644): return False
        return hashlib.sha256(path.read_bytes()).hexdigest()==output_hash
    except (OSError,RuntimeError,ValueError,TypeError):
        return False


def active_report(value,pub,feed_base,now):
    rows=value.get('translationItems')
    if not isinstance(rows,list) or value.get('itemCount')!=len(rows) or not rows: return False
    if parse_utc(value.get('translationExpiresAt'),'REPORT_TRANSLATION_EXPIRES_AT')<=now: return False
    seen=set()
    for row in rows:
        if not isinstance(row,dict): return False
        key=str(row.get('eventKey') or '')
        if not key or key in seen: return False
        seen.add(key)
        probe={
            'translationStatus':'complete',
            'translationSourceCompleteness':'full',
            'translationTruncated':False,
            'translationExpiresAt':row.get('expiresAt'),
            'translationPublicFile':row.get('publicFile'),
            'translatedUrl':row.get('translatedUrl'),
            'translationOutputHash':row.get('outputHash'),
        }
        if not active_translation(probe,pub,feed_base,now): return False
    return True


def project_public(data,pub,feed_base,now=None):
    if now is None:
        configured=os.environ.get('RSS_NOW_ISO','').strip()
        now=parse_utc(configured,'RSS_NOW_ISO') if configured else datetime.now(timezone.utc)
    items_dir=data/'items'; reports_dir=data/'reports'
    stored=[]
    for path in items_dir.glob('*.json'):
        try:
            value=load_json(path)
            if value.get('schemaVersion')==1: stored.append(value)
        except Exception: pass
    stored.sort(key=lambda x:x.get('lastSeenAt',''),reverse=True)
    stored=stored[:MAX_ITEMS]
    keep={f'{x["id"]}.json' for x in stored}
    for path in items_dir.glob('*.json'):
        if path.name not in keep: path.unlink(missing_ok=True)
    live=[value for value in stored if active_translation(value,pub,feed_base,now)]
    atomic_text(pub/'all.xml',rss('内容精选',f'{feed_base}/all.xml',[rss_item(x) for x in live]),0o644)
    priority=[x for x in live if x.get('priority')=='S' or x.get('isHighValueOpportunity') is True]
    atomic_text(pub/'priority.xml',rss('重点内容',f'{feed_base}/priority.xml',[rss_item(x) for x in priority]),0o644)

    reports=[]
    for path in reports_dir.glob('*.json'):
        try:
            value=load_json(path)
            if value.get('schemaVersion')==1 and value.get('reportType') in ('morning','afternoon'): reports.append(value)
        except Exception: pass
    reports.sort(key=lambda x:x.get('generatedAt',''),reverse=True)
    reports=reports[:MAX_REPORTS]
    keep_reports={f'{x["dateKey"]}-{x["reportType"]}.json' for x in reports}
    for path in reports_dir.glob('*.json'):
        if path.name not in keep_reports: path.unlink(missing_ok=True)
    keep_html=set()
    for row in reports:
        row['title']=report_title(row['reportType'])
        full_markdown=sanitized_historical_markdown(renderable_report_markdown(row,False))
        brief_markdown=renderable_report_markdown(row,True) if row.get('briefMarkdown') else historical_brief(row)
        full_name=f'{row["dateKey"]}-{row["reportType"]}.html'
        brief_name=f'{row["dateKey"]}-{row["reportType"]}-brief.html'
        keep_html.update((full_name,brief_name))
        atomic_text(pub/full_name,page_html(row,full_markdown,False),0o644)
        atomic_text(pub/brief_name,page_html(row,brief_markdown,True),0o644)
    for path in pub.glob('*.html'):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}-(?:morning|afternoon)(?:-brief)?\.html',path.name) and path.name not in keep_html:
            path.unlink(missing_ok=True)
    live_reports=[value for value in reports if active_report(value,pub,feed_base,now)]
    atomic_text(pub/'reports.xml',rss('完整内容',f'{feed_base}/reports.xml',[report_item(x,feed_base,False) for x in live_reports]),0o644)
    atomic_text(pub/'brief.xml',rss('精简内容',f'{feed_base}/brief.xml',[report_item(x,feed_base,True) for x in live_reports]),0o644)
    return {
        'archiveItemCount':len(stored),'allCount':len(live),'priorityCount':len(priority),
        'archiveReportCount':len(reports),'reportCount':len(live_reports),
    }


def runtime_paths():
    env=load_env()
    token=str(env.get('RSS_PUBLISH_TOKEN','')).strip()
    if not re.fullmatch(r'[A-Fa-f0-9]{64}',token): raise RuntimeError('RSS_PUBLISH_TOKEN_INVALID')
    data=Path(env.get('RSS_PUBLISH_DATA_ROOT','/home/ubuntu/.openclaw/rss-intelligence/publisher-data-v52'))
    public=Path(env.get('RSS_PUBLISH_PUBLIC_ROOT','/var/www/rss-intelligence'))
    base=str(env.get('RSS_PUBLISH_BASE_URL','https://rss.example.com/intelligence')).rstrip('/')
    pub=public/token
    ensure_dir(data,0o700); ensure_dir(data/'items',0o700); ensure_dir(data/'reports',0o700); ensure_dir(public,0o2750)
    ensure_dir(pub,0o711); ensure_dir(pub/'translations',0o755); ensure_dir(STATE_ROOT,0o700)
    return env,token,data,pub,f'{base}/{token}'


def publish_stage(stage_file,manifest_file,presentation_only=False):
    stage=load_json(stage_file)
    edition=stage.get('edition')
    candidate=candidate_for_stage(stage)
    validate_stage(stage,candidate)
    env,token,data,pub,feed_base=runtime_paths()
    translation_root=Path(env.get('RSS_TRANSLATION_ROOT','/home/ubuntu/.openclaw/rss-intelligence/translation-v53'))
    translations,manifest=translation_map(manifest_file,stage,feed_base,pub,translation_root)
    gen=iso(stage.get('generatedAt')); saved_now=iso(); items_dir=data/'items'; reports_dir=data/'reports'
    if not presentation_only and (STATE_ROOT/'last-success.json').exists():
        previous=load_json(STATE_ROOT/'last-success.json')
        previous_source=previous.get('sourceFetchFinishedAt'); current_source=candidate.get('sourceFetchFinishedAt')
        if previous_source and current_source and parse_utc(current_source,'CURRENT_SOURCE_FETCH_TIME')<parse_utc(previous_source,'PREVIOUS_SOURCE_FETCH_TIME'):
            raise RuntimeError('SUCCESS_WATERMARK_REGRESSION_REFUSED')
    for raw in stage['items']:
        key=str(raw['eventKey']).strip(); eid=event_id(key); path=items_dir/f'{eid}.json'
        old=load_json(path) if path.exists() else None; trans=translations[key]
        value=dict(raw); value.update({
            'schemaVersion':1,'id':eid,
            'firstSeenAt':old.get('firstSeenAt') if isinstance(old,dict) else gen,
            'lastSeenAt':gen,'lastEdition':edition,
            'translatedUrl':trans['translatedUrl'],
            'translationExpiresAt':trans['expiresAt'],
            'translationStatus':'complete',
            'translationPublicFile':trans['publicFile'],
            'translationOutputHash':trans['outputHash'],
            'translationSourceCompleteness':'full',
            'translationTruncated':False,
        })
        for field in ('publishedAt','eventAt'):
            if value.get(field): value[field]=iso(value[field])
            else: value.pop(field,None)
        atomic_json(path,value,0o600)
    counts={key:sum(item.get('priority')==key for item in stage['items']) for key in ('S','A','B','C')}
    datekey=date_key(gen)
    translation_items=[{
        'eventKey':str(item['eventKey']),
        'translatedUrl':translations[str(item['eventKey'])]['translatedUrl'],
        'publicFile':translations[str(item['eventKey'])]['publicFile'],
        'outputHash':translations[str(item['eventKey'])]['outputHash'],
        'expiresAt':translations[str(item['eventKey'])]['expiresAt'],
    } for item in stage['items']]
    report={
        'schemaVersion':1,'presentationVersion':PRESENTATION_VERSION,'reportType':edition,
        'title':report_title(edition),'generatedAt':gen,'savedAt':saved_now,'dateKey':datekey,
        'candidateRunId':stage.get('candidateRunId'),'stageId':stage.get('stageId'),
        'itemCount':len(stage['items']),'priorityCounts':counts,
        'markdown':build_report_markdown(stage,translations),
        'briefMarkdown':build_brief_markdown(stage,translations),
        'translationExpiresAt':manifest['expiresAt'],'translationItems':translation_items,
    }
    atomic_json(reports_dir/f'{datekey}-{edition}.json',report,0o600)
    projection=project_public(data,pub,feed_base)
    html_name=f'{datekey}-{edition}.html'; brief_name=f'{datekey}-{edition}-brief.html'
    bootstrap_committed=quarantine_committed=quarantine_released=0
    if not presentation_only:
        bootstrap_committed,quarantine_committed,quarantine_released=commit_bootstrap_state(candidate,env,saved_now)
        success={'schemaVersion':2,'status':'ok','edition':edition,'candidateRunId':candidate['runId'],'stageId':stage.get('stageId'),'sourceFetchRunId':candidate.get('sourceFetchRunId'),'sourceFetchFinishedAt':candidate.get('sourceFetchFinishedAt'),'normalCutoffTimestampSec':candidate.get('normalCutoffTimestampSec'),'effectiveCutoffTimestampSec':candidate.get('effectiveCutoffTimestampSec',candidate.get('cutoffTimestampSec')),'publishedAt':saved_now,'reportDateKey':datekey}
        if edition=='morning':
            latest=dict(stage); latest['dateKey']=datekey; latest['publishedAt']=saved_now; latest['publicUrl']=f'{feed_base}/{html_name}'; latest['briefPublicUrl']=f'{feed_base}/{brief_name}'; atomic_json(STATE_ROOT/'latest-morning-final.json',latest,0o600)
        atomic_json(STATE_ROOT/'last-success.json',success,0o600)
    output={
        'published':True,'presentationOnly':presentation_only,'edition':edition,
        'candidateRunId':candidate['runId'],'itemCount':len(stage['items']),
        **projection,'bootstrapFeedsCommitted':bootstrap_committed,
        'bootstrapQuarantineArticlesCommitted':quarantine_committed,
        'bootstrapQuarantineArticlesReleased':quarantine_released,
        'translationCompleteCount':len(translations),'translationLimitedCount':0,
        'publicFiles':['all.xml','priority.xml','reports.xml','brief.xml',html_name,brief_name],
        'watermarkUpdated':not presentation_only,
    }
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage_file',nargs='?')
    parser.add_argument('--translation-manifest')
    parser.add_argument('--presentation-only',action='store_true')
    parser.add_argument('--reconcile-expired',action='store_true')
    args=parser.parse_args()
    env,token,data,pub,feed_base=runtime_paths()
    with publish_lock(data):
        if args.reconcile_expired:
            if args.stage_file or args.translation_manifest or args.presentation_only:
                raise RuntimeError('RECONCILE_ARGUMENT_CONFLICT')
            output={'reconciled':True,**project_public(data,pub,feed_base),'watermarkUpdated':False}
        else:
            if not args.stage_file or not args.translation_manifest:
                raise RuntimeError('STAGE_AND_TRANSLATION_MANIFEST_REQUIRED')
            output=publish_stage(args.stage_file,args.translation_manifest,args.presentation_only)
    print(json.dumps(output,ensure_ascii=False))

if __name__=='__main__': main()
