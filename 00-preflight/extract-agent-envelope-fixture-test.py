#!/usr/bin/env python3
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PREPARE = HERE / 'prepare-daily.py'
if not PREPARE.exists():
    PREPARE = HERE.parent / '04-daily-pipeline' / 'prepare-daily.py'
sys.path.insert(0, str(PREPARE.parent))
spec = importlib.util.spec_from_file_location('prepare_daily', PREPARE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


VALID = {
    'schemaVersion': 2,
    'edition': 'morning',
    'batch': 1,
    'rawCount': 0,
    'backfillCount': 0,
    'filteredLowValueCount': 0,
    'mergedDuplicateArticleCount': 0,
    'candidateArticleCount': 0,
    'accounting': [],
    'candidates': [],
}
VALID_TEXT = json.dumps(VALID, ensure_ascii=False)


def envelope(value):
    return json.dumps(value, ensure_ascii=False)


def expect_ok(name, value):
    result = module.decode_agent_stdout(envelope(value))
    assert result == VALID, f'{name}: unexpected result'
    print(f'PASS {name}')


def expect_error(name, stdout, marker):
    try:
        module.decode_agent_stdout(stdout)
    except RuntimeError as exc:
        assert str(exc) == marker, f'{name}: got {exc!s}'
        print(f'PASS {name} marker={marker}')
        return
    raise AssertionError(f'{name}: expected {marker}')


expect_ok(
    'latest_non_empty_payload',
    {'payloads': [{'text': ''}, {'text': 'stale'}, {'text': VALID_TEXT}]},
)
expect_ok(
    'final_precedes_payloads',
    {'payloads': [{'text': 'not-json'}], 'final': VALID_TEXT},
)
expect_ok(
    'output_text_precedes_payloads',
    {'payloads': [{'text': 'not-json'}], 'outputText': VALID_TEXT},
)
expect_ok(
    'response_text_precedes_payloads',
    {'payloads': [{'text': 'not-json'}], 'responseText': VALID_TEXT},
)
expect_ok(
    'nested_result',
    {'payloads': [{'text': ''}], 'result': {'payloads': [{'text': ''}, {'text': VALID_TEXT}]}},
)
expect_ok(
    'canonical_visible_meta',
    {'result': {'meta': {'finalAssistantVisibleText': VALID_TEXT}, 'payloads': [{'text': ''}]}},
)
expect_ok(
    'canonical_raw_meta',
    {'result': {'meta': {'finalAssistantVisibleText': '', 'finalAssistantRawText': VALID_TEXT}}},
)
expect_ok(
    'markdown_json_fence',
    {'final': f'```json\n{VALID_TEXT}\n```'},
)
expect_ok(
    'single_valid_fence_after_preamble',
    {'final': f'I will return the requested object.\n```json\n{VALID_TEXT}\n```'},
)
expect_error(
    'all_text_empty',
    envelope({'payloads': [{'text': ''}, {'text': '   '}], 'result': {'final': ''}}),
    'BATCH_AGENT_TEXT_EMPTY',
)
expect_error(
    'model_text_bad_json',
    envelope({'final': 'not-json'}),
    'BATCH_AGENT_BAD_JSON',
)
expect_error(
    'ambiguous_multiple_json_fences',
    envelope({'final': f'```json\n{VALID_TEXT}\n```\n```json\n{VALID_TEXT}\n```'}),
    'BATCH_AGENT_BAD_JSON',
)
expect_error(
    'envelope_bad_json',
    'not-json',
    'BATCH_AGENT_ENVELOPE_BAD_JSON',
)


batch = {
    'batch': 1,
    'articles': [
        {'id': 'a1', 'url': 'https://example.com/a1'},
        {'id': 'a2', 'url': 'https://example.com/a2'},
    ],
}
candidate = {
    'primaryArticleId': 'a1',
    'eventKeyCandidate': 'event-a1',
    'title': '测试标题',
    'priority': 'A',
    'category': 'A-AI-Agent',
    'summary': '摘要',
    'whyImportant': '重要性',
    'impact': '影响',
    'sources': [{'name': 'Example', 'url': 'https://example.com/a1'}],
}
normalizable = {
    'schemaVersion': 2,
    'edition': 'morning',
    'batch': 1,
    'rawCount': 999,
    'backfillCount': 999,
    'filteredLowValueCount': 999,
    'mergedDuplicateArticleCount': 999,
    'candidateArticleCount': 999,
    'accounting': [
        {'id': 'a1', 'disposition': 'candidate'},
        {'id': 'a2', 'disposition': 'filtered'},
    ],
    'candidates': [candidate],
}
normalized = module.validate_classifier_result('morning', batch, normalizable)
assert normalized['rawCount'] == 2
assert normalized['candidateArticleCount'] == 1
assert normalized['filteredLowValueCount'] == 1
assert normalized['backfillCount'] == 0
assert normalized['mergedDuplicateArticleCount'] == 0
assert normalized['candidates'][0]['isHighValueOpportunity'] is False
print('PASS host_derived_counts_and_conservative_boolean_default')


def expect_validation_error(name, value, marker):
    import copy
    try:
        module.validate_classifier_result('morning', batch, copy.deepcopy(value))
    except ValueError as exc:
        assert marker in str(exc), f'{name}: got {exc!s}'
        print(f'PASS {name} marker={marker}')
        return
    raise AssertionError(f'{name}: expected {marker}')


for bad_value in (None, 'false', 0):
    bad = json.loads(json.dumps(normalizable, ensure_ascii=False))
    bad['candidates'][0]['isHighValueOpportunity'] = bad_value
    expect_validation_error('opportunity_type_rejected', bad, 'isHighValueOpportunity must be boolean')

bad_row = json.loads(json.dumps(normalizable, ensure_ascii=False))
bad_row['accounting'][0] = None
expect_validation_error('non_object_accounting_rejected', bad_row, 'BAD_BATCH_ACCOUNTING_ROW')

bad_candidate = json.loads(json.dumps(normalizable, ensure_ascii=False))
bad_candidate['candidates'][0] = None
expect_validation_error('non_object_candidate_rejected', bad_candidate, 'BAD_BATCH_CANDIDATE')

bad_url = json.loads(json.dumps(normalizable, ensure_ascii=False))
bad_url['candidates'][0]['sources'][0]['url'] = 'https://outside.example/item'
expect_validation_error('outside_source_url_rejected', bad_url, 'CANDIDATE_SOURCE_URL_NOT_IN_BATCH')

print('P49_EXTRACT_FIXTURES_OK')
