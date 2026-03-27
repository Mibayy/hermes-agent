def test_delegation_config_has_new_keys():
    import sys, os
    sys.path.insert(0, '/root/hermes-agent')
    # Load the delegation defaults directly from config module
    from hermes_cli.config import DEFAULT_CONFIG
    d = DEFAULT_CONFIG.get('delegation', {})
    assert 'max_depth' in d, 'max_depth missing'
    assert 'memory_access' in d, 'memory_access missing'
    assert d['memory_access'] == 'none'
    assert 'checkpoint' in d
    assert d['checkpoint']['enabled'] is False
    assert 'retry' in d
    assert d['retry']['max_retries'] == 0
    assert 'verify' in d
    assert 'dag' in d
    assert 'blackboard' in d
    assert 'semantic_cache' in d
    assert 'observability' in d
