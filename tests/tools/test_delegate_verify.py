import sys
sys.path.insert(0, '/root/hermes-agent')


def test_verify_skipped_when_disabled():
    from tools.delegate_tool import _run_with_verify
    result = {'status': 'completed', 'summary': 'done', 'task_index': 0}
    task = {'goal': 'do thing', 'verify': False}
    out = _run_with_verify(result, task, None, {})
    assert out is result
    assert 'verdict' not in out


def test_verify_skipped_when_not_completed():
    from tools.delegate_tool import _run_with_verify
    result = {'status': 'failed', 'summary': None, 'task_index': 0}
    task = {'goal': 'do thing', 'verify': True}
    out = _run_with_verify(result, task, None, {})
    assert out is result


def test_verify_skipped_when_no_summary():
    from tools.delegate_tool import _run_with_verify
    result = {'status': 'completed', 'summary': '', 'task_index': 0}
    task = {'goal': 'do thing', 'verify': True}
    out = _run_with_verify(result, task, None, {})
    assert out is result


def test_verify_extracts_valid_verdict(monkeypatch):
    import tools.delegate_tool as dt
    fake_critic = {'status': 'completed', 'summary': 'VERDICT: valid. Logic looks correct.'}
    monkeypatch.setattr(dt, '_run_single_child', lambda **kw: fake_critic)
    monkeypatch.setattr(dt, '_build_child_agent', lambda **kw: None)
    from tools.delegate_tool import _run_with_verify
    result = {'status': 'completed', 'summary': 'my result', 'task_index': 0}
    task = {'goal': 'do thing', 'verify': True}
    out = _run_with_verify(result, task, object(), {})
    assert out['verdict'] == 'valid'
    assert 'critic_summary' in out


def test_verify_extracts_invalid_verdict(monkeypatch):
    import tools.delegate_tool as dt
    fake_critic = {'status': 'completed', 'summary': 'VERDICT: invalid. Missing edge case.'}
    monkeypatch.setattr(dt, '_run_single_child', lambda **kw: fake_critic)
    monkeypatch.setattr(dt, '_build_child_agent', lambda **kw: None)
    from tools.delegate_tool import _run_with_verify
    result = {'status': 'completed', 'summary': 'my result', 'task_index': 0}
    task = {'goal': 'do thing', 'verify': True}
    out = _run_with_verify(result, task, object(), {})
    assert out['verdict'] == 'invalid'


def test_global_verify_config(monkeypatch):
    import tools.delegate_tool as dt
    fake_critic = {'status': 'completed', 'summary': 'VERDICT: valid.'}
    monkeypatch.setattr(dt, '_run_single_child', lambda **kw: fake_critic)
    monkeypatch.setattr(dt, '_build_child_agent', lambda **kw: None)
    from tools.delegate_tool import _run_with_verify
    result = {'status': 'completed', 'summary': 'done', 'task_index': 0}
    task = {'goal': 'thing'}
    cfg = {'delegation': {'verify': {'enabled': True}}}
    out = _run_with_verify(result, task, object(), cfg)
    assert out.get('verdict') == 'valid'
