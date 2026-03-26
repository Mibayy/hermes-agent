"""Tests for current_turn tracking and scope stability under active writes."""

import pytest
from tools.structured_memory.constants import SCOPE_COOL_TURNS
from tools.structured_memory import scopes, facts


def test_active_scope_not_cooled_while_writing(conn):
    """Scope with active writes must NOT cool after SCOPE_COOL_TURNS ticks."""
    sid = scopes.get_or_create(conn, "active-feat")

    for turn in range(1, SCOPE_COOL_TURNS + 3):
        # Write a fact each turn and touch scope to simulate server behavior
        facts.write(conn, f"D[feat.step{turn}]: step {turn}", scope_id=sid)
        scopes.touch(conn, sid, turn)
        cooled = scopes.tick(conn, turn=turn, session_id=None)
        assert sid not in cooled, f"scope cooled at turn {turn} despite active writes"

    # Scope must still be active
    active = [s["id"] for s in scopes.get_active(conn)]
    assert sid in active


def test_scope_cools_after_real_silence(conn):
    """Scope with no writes for SCOPE_COOL_TURNS ticks must cool."""
    sid = scopes.get_or_create(conn, "silent-feat")
    facts.write(conn, "D[silent]: initial write", scope_id=sid)

    # No more writes — just tick
    cooled_ids = []
    for turn in range(1, SCOPE_COOL_TURNS + 2):
        cooled = scopes.tick(conn, turn=turn)
        cooled_ids.extend(cooled)

    assert sid in cooled_ids


def test_get_or_create_refreshes_last_referenced(conn):
    """Calling get_or_create on an existing scope updates last_referenced."""
    from tools.structured_memory.db import sm_now
    import time

    sid = scopes.get_or_create(conn, "my-scope")
    row_before = conn.execute("SELECT last_referenced FROM sm_scopes WHERE id=?", (sid,)).fetchone()
    t_before = row_before["last_referenced"]

    time.sleep(1)  # ensure timestamp advances
    scopes.get_or_create(conn, "my-scope")

    row_after = conn.execute("SELECT last_referenced FROM sm_scopes WHERE id=?", (sid,)).fetchone()
    assert row_after["last_referenced"] >= t_before


def test_no_duplicate_active_scope(conn):
    """Two concurrent get_or_create with same label must return the same scope."""
    sid1 = scopes.get_or_create(conn, "dup-scope")
    sid2 = scopes.get_or_create(conn, "dup-scope")
    assert sid1 == sid2

    count = conn.execute(
        "SELECT COUNT(*) FROM sm_scopes WHERE label='dup-scope' AND status='active'"
    ).fetchone()[0]
    assert count == 1
