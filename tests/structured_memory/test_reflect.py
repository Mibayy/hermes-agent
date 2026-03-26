"""Tests for memory_reflect grouping and synthesis safety."""

import pytest
from tools.structured_memory import facts, gauge


def test_reflect_groups_by_type(conn):
    facts.write(conn, "C[auth]: nvr store plaintext pwd")
    facts.write(conn, "D[auth]: JWT 7j refresh 6j")
    facts.write(conn, "V[auth.secret]: env AUTH_SECRET")

    results = facts.search(conn, "auth", limit=20)
    assert len(results) == 3

    types = {r["type"] for r in results}
    assert "C" in types
    assert "D" in types
    assert "V" in types


def test_reflect_includes_cold_facts(conn):
    from tools.structured_memory import scopes
    sid = scopes.get_or_create(conn, "old-scope")
    facts.write(conn, "D[db]: MySQL legacy", scope_id=sid)
    scopes.close(conn, sid)

    # Fact is now cold
    row = conn.execute("SELECT status FROM sm_facts WHERE target='db'").fetchone()
    assert row["status"] == "cold"

    # search still finds it
    results = facts.search(conn, "MySQL", limit=5)
    assert len(results) == 1
    assert results[0]["status"] == "cold"


def test_synthesis_aborts_on_empty_llm_response(conn):
    facts.write(conn, "C[x]: some constraint")
    facts.write(conn, "D[y]: some decision")

    def bad_llm(prompt: str) -> str:
        return ""   # LLM returns nothing

    consolidated = gauge._force_synthesis(conn, bad_llm)

    # Must return 0 and originals must still be active
    assert consolidated == 0
    active = conn.execute(
        "SELECT COUNT(*) FROM sm_facts WHERE status='active'"
    ).fetchone()[0]
    assert active == 2


def test_synthesis_uses_notation_symbols(conn):
    facts.write(conn, "✓[auth]: deployed")
    facts.write(conn, "~[legacy]: replaced")

    captured = {}

    def capture_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return "C[x]: consolidated"

    gauge._force_synthesis(conn, capture_llm)

    assert "✓[auth]" in captured["prompt"]
    assert "~[legacy]" in captured["prompt"]
    # Must NOT contain raw DB codes
    assert "done[auth]" not in captured["prompt"]
    assert "obs[legacy]" not in captured["prompt"]
