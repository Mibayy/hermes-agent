"""Tests for memory_status injection payload."""

import pytest
from tools.structured_memory.constants import ABBREV_DICT, TYPE_DISPLAY
from tools.structured_memory import facts, scopes, gauge


def test_abbrev_dict_not_empty():
    assert len(ABBREV_DICT) >= 30


def test_type_display_roundtrip():
    from tools.structured_memory.constants import TYPE_MAP
    for sym, code in TYPE_MAP.items():
        assert TYPE_DISPLAY[code] == sym


def test_search_returns_notation_symbols(conn):
    facts.write(conn, "✓[auth]: deployed prod")
    results = facts.search(conn, "auth", limit=5)
    assert len(results) == 1
    # type should be the DB code, display conversion happens in server
    assert results[0]["type"] == "done"
    # TYPE_DISPLAY should map it back correctly
    assert TYPE_DISPLAY[results[0]["type"]] == "✓"


def test_hot_facts_respect_scope_status(conn):
    # Facts in a closed scope must NOT appear in hot_facts
    sid = scopes.get_or_create(conn, "closed-scope")
    facts.write(conn, "C[test]: some constraint", scope_id=sid)
    scopes.close(conn, sid)

    hot = facts.get_hot(conn)
    targets = [f["target"] for f in hot]
    assert "test" not in targets


def test_hot_facts_no_scope_always_hot(conn):
    # Facts with no scope are always hot (global constraints)
    facts.write(conn, "C[global]: alw English targets")
    hot = facts.get_hot(conn)
    targets = [f["target"] for f in hot]
    assert "global" in targets
