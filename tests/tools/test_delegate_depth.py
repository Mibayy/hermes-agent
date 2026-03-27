import sys
sys.path.insert(0, '/root/hermes-agent')

def test_get_max_depth_reads_from_config(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_load_config', lambda: {'delegation': {'max_depth': 3}})
    assert dt._get_max_depth() == 3

def test_get_max_depth_default_is_2(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_load_config', lambda: {})
    assert dt._get_max_depth() == 2

def test_get_max_depth_fallback_on_missing_key(monkeypatch):
    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, '_load_config', lambda: {'delegation': {}})
    assert dt._get_max_depth() == 2
