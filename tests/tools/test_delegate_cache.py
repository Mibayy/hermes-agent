import sys
sys.path.insert(0, '/root/hermes-agent')


def test_cache_returns_none_when_sm_unavailable(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', False)
    from tools.delegate_tool import _check_semantic_cache
    assert _check_semantic_cache('anything') is None


def test_cache_returns_none_when_no_match(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', True)
    monkeypatch.setattr(dt, '_sm_search_goal', lambda goal, limit=3: [])
    from tools.delegate_tool import _check_semantic_cache
    assert _check_semantic_cache('brand new task') is None


def test_cache_returns_value_when_found(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', True)
    monkeypatch.setattr(
        dt, '_sm_search_goal',
        lambda goal, limit=3: [{'value': 'cached result for auth flow'}]
    )
    from tools.delegate_tool import _check_semantic_cache
    result = _check_semantic_cache('analyse the auth flow')
    assert result == 'cached result for auth flow'


def test_sm_search_goal_returns_empty_when_unavailable(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_SM_AVAILABLE', False)
    from tools.delegate_tool import _sm_search_goal
    assert _sm_search_goal('test goal') == []
