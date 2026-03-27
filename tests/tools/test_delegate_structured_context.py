import sys
sys.path.insert(0, '/root/hermes-agent')
from tools.delegate_tool import _format_structured_context, _build_child_system_prompt


# -- _format_structured_context --

def test_string_passthrough():
    assert _format_structured_context('hello world') == 'hello world'


def test_empty_string():
    assert _format_structured_context('   ').strip() == ''


def test_files_section():
    result = _format_structured_context({'files': ['src/auth.py', 'src/token.py']})
    assert 'Relevant files' in result
    assert 'src/auth.py' in result
    assert 'src/token.py' in result


def test_facts_section():
    result = _format_structured_context({'facts': ['JWT is used', 'tokens expire in 1h']})
    assert 'Known facts' in result
    assert 'JWT is used' in result


def test_constraints_section():
    result = _format_structured_context({'constraints': ['do not modify prod DB', 'read-only access']})
    assert 'Constraints' in result
    assert 'do not modify prod DB' in result


def test_notes_section():
    result = _format_structured_context({'notes': 'extra info here'})
    assert 'Notes' in result
    assert 'extra info here' in result


def test_unknown_keys_rendered():
    result = _format_structured_context({'custom_key': ['val1', 'val2']})
    # key.replace('_', ' ').title() -> 'Custom Key'
    assert 'Custom Key' in result
    assert 'val1' in result


def test_all_fields_combined():
    ctx = {
        'files': ['src/main.py'],
        'facts': ['python 3.12'],
        'constraints': ['no external deps'],
        'notes': 'see readme',
    }
    result = _format_structured_context(ctx)
    assert 'src/main.py' in result
    assert 'python 3.12' in result
    assert 'no external deps' in result
    assert 'see readme' in result


def test_empty_lists_skipped():
    result = _format_structured_context({'files': [], 'facts': ['only fact']})
    assert 'Relevant files' not in result
    assert 'only fact' in result


def test_non_dict_non_str_coerced():
    result = _format_structured_context(42)
    assert '42' in result


# -- Integration with _build_child_system_prompt --

def test_structured_context_in_prompt():
    ctx = {
        'files': ['tools/delegate_tool.py'],
        'constraints': ['must not break existing tests'],
    }
    prompt = _build_child_system_prompt(goal='refactor delegate_tool', context=ctx)
    assert 'tools/delegate_tool.py' in prompt
    assert 'must not break existing tests' in prompt
    assert 'CONTEXT' in prompt


def test_string_context_still_works():
    prompt = _build_child_system_prompt(goal='do thing', context='plain string context')
    assert 'plain string context' in prompt
    assert 'CONTEXT' in prompt


def test_none_context_no_context_section():
    prompt = _build_child_system_prompt(goal='do thing', context=None)
    assert 'CONTEXT' not in prompt
