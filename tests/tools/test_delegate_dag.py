import sys
import pytest
sys.path.insert(0, '/root/hermes-agent')
from tools.delegate_dag import topological_sort, resolve_deps


def test_no_deps_preserves_order():
    tasks = [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]
    result = topological_sort(tasks)
    assert [t['id'] for t in result] == ['a', 'b', 'c']


def test_deps_respected():
    tasks = [
        {'id': 'a'},
        {'id': 'b', 'depends_on': ['a']},
        {'id': 'c', 'depends_on': ['a']},
        {'id': 'd', 'depends_on': ['b', 'c']},
    ]
    result = topological_sort(tasks)
    ids = [t['id'] for t in result]
    assert ids.index('a') < ids.index('b')
    assert ids.index('a') < ids.index('c')
    assert ids.index('b') < ids.index('d')
    assert ids.index('c') < ids.index('d')


def test_cycle_raises():
    tasks = [
        {'id': 'a', 'depends_on': ['b']},
        {'id': 'b', 'depends_on': ['a']},
    ]
    with pytest.raises(ValueError, match='[Cc]ycle'):
        topological_sort(tasks)


def test_unknown_dep_raises():
    tasks = [{'id': 'a', 'depends_on': ['nonexistent']}]
    with pytest.raises(ValueError, match='unknown'):
        topological_sort(tasks)


def test_tasks_without_id_use_index():
    tasks = [{'goal': 'a'}, {'goal': 'b'}]
    result = topological_sort(tasks)
    assert len(result) == 2


def test_resolve_deps_no_deps_unchanged():
    task = {'id': 'b', 'goal': 'do thing'}
    result = resolve_deps(task, {})
    assert result is task


def test_resolve_deps_injects_summary():
    task = {'id': 'b', 'goal': 'write tests', 'depends_on': ['a']}
    completed = {'a': {'summary': 'auth flow uses JWT'}}
    result = resolve_deps(task, completed)
    assert 'auth flow uses JWT' in result['context']


def test_resolve_deps_appends_to_existing_context():
    task = {'id': 'b', 'goal': 'x', 'context': 'existing context', 'depends_on': ['a']}
    completed = {'a': {'summary': 'result A'}}
    result = resolve_deps(task, completed)
    assert 'existing context' in result['context']
    assert 'result A' in result['context']


def test_resolve_deps_missing_summary_skipped():
    task = {'id': 'b', 'goal': 'x', 'depends_on': ['a']}
    completed = {'a': {'summary': None}}
    result = resolve_deps(task, completed)
    assert result.get('context') is None or result.get('context') == ''
