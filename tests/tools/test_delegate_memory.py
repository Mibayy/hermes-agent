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


# -- Hot facts injection --

def test_get_parent_hot_facts_no_store():
    from tools.delegate_tool import _get_parent_hot_facts
    class FakeParent:
        pass
    assert _get_parent_hot_facts(FakeParent()) is None


def test_get_parent_hot_facts_with_store():
    from tools.delegate_tool import _get_parent_hot_facts
    class FakeStore:
        def format_for_system_prompt(self, target):
            return f'[{target} block]'
    class FakeParent:
        _memory_store = FakeStore()
    result = _get_parent_hot_facts(FakeParent())
    assert result is not None
    assert 'memory' in result
    assert 'user' in result


def test_get_parent_hot_facts_graceful_on_error():
    from tools.delegate_tool import _get_parent_hot_facts
    class BrokenStore:
        def format_for_system_prompt(self, target):
            raise RuntimeError('broken')
    class FakeParent:
        _memory_store = BrokenStore()
    assert _get_parent_hot_facts(FakeParent()) is None


def test_hot_facts_injected_in_prompt_when_read_mode():
    from tools.delegate_tool import _build_child_system_prompt
    prompt = _build_child_system_prompt(
        goal='do thing',
        hot_facts='C[must-use-jwt]: use JWT auth',
    )
    assert 'PARENT MEMORY' in prompt
    assert 'must-use-jwt' in prompt


def test_hot_facts_not_in_prompt_when_none():
    from tools.delegate_tool import _build_child_system_prompt
    prompt = _build_child_system_prompt(goal='do thing', hot_facts=None)
    assert 'PARENT MEMORY' not in prompt
