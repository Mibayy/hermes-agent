import sys, threading
sys.path.insert(0, '/root/hermes-agent')
from tools.delegate_blackboard import Blackboard

def test_set_and_get():
    bb = Blackboard()
    bb.set('key', 'value')
    assert bb.get('key') == 'value'

def test_get_missing_returns_default():
    bb = Blackboard()
    assert bb.get('missing') is None
    assert bb.get('missing', 'fallback') == 'fallback'

def test_snapshot_is_copy():
    bb = Blackboard()
    bb.set('x', 1)
    snap = bb.snapshot()
    snap['x'] = 999
    assert bb.get('x') == 1

def test_thread_safe():
    bb = Blackboard()
    errors = []
    def writer(i):
        try:
            bb.set(f'key_{i}', i)
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(bb.snapshot()) == 50

def test_to_context_string_empty():
    bb = Blackboard()
    assert bb.to_context_string() == ''

def test_to_context_string_with_data():
    bb = Blackboard()
    bb.set('auth_url', 'https://example.com')
    s = bb.to_context_string()
    assert 'auth_url' in s
    assert 'example.com' in s
