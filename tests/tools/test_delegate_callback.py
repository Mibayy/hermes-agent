import sys
sys.path.insert(0, '/root/hermes-agent')
from tools.delegate_tool import _run_single_child

def test_on_task_done_signature_accepted():
    """delegate_task function should accept on_task_done parameter."""
    import inspect
    from tools.delegate_tool import delegate_task
    sig = inspect.signature(delegate_task)
    assert 'on_task_done' in sig.parameters

def test_on_task_done_default_is_none():
    import inspect
    from tools.delegate_tool import delegate_task
    sig = inspect.signature(delegate_task)
    assert sig.parameters['on_task_done'].default is None
