import sys
sys.path.insert(0, '/root/hermes-agent')


def _make_child_kwargs():
    return {
        'task_index': 0,
        'goal': 'do thing',
        'context': None,
        'toolsets': None,
        'model': None,
        'max_iterations': 5,
        'parent_agent': None,
    }


def test_no_retry_on_success(monkeypatch):
    import tools.delegate_tool as dt
    calls = []
    def fake_run(task_index, goal, child, parent_agent):
        calls.append(1)
        return {'status': 'completed', 'summary': 'done'}
    monkeypatch.setattr(dt, '_run_single_child', fake_run)
    monkeypatch.setattr(dt, '_build_child_agent', lambda **kw: None)
    from tools.delegate_tool import _run_with_retry
    result = _run_with_retry({'goal': 'x'}, None, _make_child_kwargs(), max_retries=2)
    assert len(calls) == 1
    assert result['status'] == 'completed'
    assert 'retry_count' not in result


def test_retries_on_failure(monkeypatch):
    import tools.delegate_tool as dt
    calls = [0]
    def fake_run(task_index, goal, child, parent_agent):
        calls[0] += 1
        if calls[0] < 3:
            return {'status': 'failed', 'error': 'timeout', 'summary': None}
        return {'status': 'completed', 'summary': 'done on 3rd try'}
    monkeypatch.setattr(dt, '_run_single_child', fake_run)
    monkeypatch.setattr(dt, '_build_child_agent', lambda **kw: None)
    from tools.delegate_tool import _run_with_retry
    result = _run_with_retry({'goal': 'x'}, None, _make_child_kwargs(), max_retries=3)
    assert calls[0] == 3
    assert result['status'] == 'completed'
    assert result['retry_count'] == 2


def test_failure_context_injected(monkeypatch):
    import tools.delegate_tool as dt
    contexts_seen = []
    def fake_build(**kw):
        contexts_seen.append(kw.get('context'))
        return None
    def fake_run(task_index, goal, child, parent_agent):
        return {'status': 'failed', 'error': 'boom', 'summary': 'partial'}
    monkeypatch.setattr(dt, '_run_single_child', fake_run)
    monkeypatch.setattr(dt, '_build_child_agent', fake_build)
    from tools.delegate_tool import _run_with_retry
    _run_with_retry({'goal': 'x'}, None, _make_child_kwargs(), max_retries=1)
    assert len(contexts_seen) == 2
    assert contexts_seen[1] is not None
    assert 'boom' in contexts_seen[1]


def test_stops_after_max_retries(monkeypatch):
    import tools.delegate_tool as dt
    calls = [0]
    def fake_run(task_index, goal, child, parent_agent):
        calls[0] += 1
        return {'status': 'failed', 'error': 'err', 'summary': None}
    monkeypatch.setattr(dt, '_run_single_child', fake_run)
    monkeypatch.setattr(dt, '_build_child_agent', lambda **kw: None)
    from tools.delegate_tool import _run_with_retry
    result = _run_with_retry({'goal': 'x'}, None, _make_child_kwargs(), max_retries=2)
    assert calls[0] == 3
    assert result['retry_count'] == 2
    assert result['status'] == 'failed'
