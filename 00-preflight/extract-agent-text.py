#!/usr/bin/env python3
import json, sys

def find_text(obj):
    if isinstance(obj, dict):
        # Prefer authoritative terminal fields over progress payloads.
        for key in ('final', 'outputText', 'responseText'):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
        meta = obj.get('meta')
        if isinstance(meta, dict):
            for key in ('finalAssistantVisibleText', 'finalAssistantRawText'):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        result = obj.get('result')
        if result is not None:
            got = find_text(result)
            if got is not None:
                return got
        payloads = obj.get('payloads')
        if isinstance(payloads, list):
            for item in reversed(payloads):
                if isinstance(item, dict):
                    value = item.get('text')
                    if isinstance(value, str) and value.strip():
                        return value
        # Last-resort recursion, but only through structured fields.
        for key in ('response','data','message'):
            if key in obj:
                got = find_text(obj[key])
                if got is not None: return got
    elif isinstance(obj, list):
        for item in reversed(obj):
            got = find_text(item)
            if got is not None: return got
    return None

try:
    data=json.load(sys.stdin)
except Exception as e:
    raise SystemExit(f'EXTRACT_JSON_ERROR: {e}')
text=find_text(data)
if not isinstance(text, str) or not text.strip():
    raise SystemExit('EXTRACT_TEXT_EMPTY')
sys.stdout.write(text)
