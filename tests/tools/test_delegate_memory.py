import sys
sys.path.insert(0, '/root/hermes-agent')

def test_memory_none_strips_memory_toolset(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', True)
    result = dt._compute_child_toolsets(['terminal', 'memory', 'file'], 'none')
    assert 'memory' not in result
    assert 'terminal' in result

def test_memory_read_keeps_memory_when_sm_available(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', True)
    result = dt._compute_child_toolsets(['terminal', 'memory'], 'read')
    assert 'memory' in result

def test_memory_read_strips_memory_when_sm_unavailable(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', False)
    result = dt._compute_child_toolsets(['terminal', 'memory'], 'read')
    assert 'memory' not in result

def test_always_blocked_are_always_stripped(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', True)
    result = dt._compute_child_toolsets(['delegation', 'clarify', 'code_execution', 'terminal'], 'read-write')
    assert 'delegation' not in result
    assert 'clarify' not in result
    assert 'code_execution' not in result
    assert 'terminal' in result

def test_strip_blocked_tools_backward_compat(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', True)
    result = dt._strip_blocked_tools(['terminal', 'memory', 'delegation'])
    assert 'memory' not in result
    assert 'delegation' not in result
    assert 'terminal' in result
