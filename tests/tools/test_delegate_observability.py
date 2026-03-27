import sys
sys.path.insert(0, '/root/hermes-agent')
from tools.delegate_tool import _build_detailed_trace


def _make_messages(tool_name='my_tool', tc_id='tc1', result='ok result'):
    return [
        {'role': 'assistant', 'tool_calls': [{
            'id': tc_id,
            'function': {'name': tool_name, 'arguments': '{"x": 1}'}
        }]},
        {'role': 'tool', 'tool_call_id': tc_id, 'content': result},
    ]


def test_basic_trace_structure():
    msgs = _make_messages()
    trace = _build_detailed_trace(msgs)
    assert len(trace) == 1
    assert trace[0]['tool'] == 'my_tool'
    assert trace[0]['args_bytes'] == len('{"x": 1}')
    assert trace[0]['result_bytes'] == len('ok result')
    assert trace[0]['status'] == 'ok'


def test_error_status_detected():
    msgs = _make_messages(result='error: something went wrong')
    trace = _build_detailed_trace(msgs)
    assert trace[0]['status'] == 'error'


def test_timing_injected_when_provided():
    msgs = _make_messages(tc_id='tc1')
    timing = {'tc1': (100.0, 100.5)}
    trace = _build_detailed_trace(msgs, tool_timing=timing)
    assert trace[0]['duration_ms'] == 500


def test_empty_messages():
    assert _build_detailed_trace([]) == []
    assert _build_detailed_trace(None) == []


def test_multiple_tools():
    msgs = [
        {'role': 'assistant', 'tool_calls': [
            {'id': 'tc1', 'function': {'name': 'tool_a', 'arguments': 'a'}},
            {'id': 'tc2', 'function': {'name': 'tool_b', 'arguments': 'bb'}},
        ]},
        {'role': 'tool', 'tool_call_id': 'tc1', 'content': 'result_a'},
        {'role': 'tool', 'tool_call_id': 'tc2', 'content': 'result_b'},
    ]
    trace = _build_detailed_trace(msgs)
    assert len(trace) == 2
    assert trace[0]['tool'] == 'tool_a'
    assert trace[1]['tool'] == 'tool_b'
    assert trace[0]['result_bytes'] == len('result_a')
    assert trace[1]['result_bytes'] == len('result_b')
