"""Tests for structured_memory/scopes.py — lifecycle, auto-cooling, topic shift."""

import pytest
from tools.structured_memory.constants import SCOPE_COOL_TURNS
from tools.structured_memory import scopes, facts


def test_get_or_create_new(conn):
    sid = scopes.get_or_create(conn, "auth-refactor")
    assert sid
    row = conn.execute("SELECT * FROM sm_scopes WHERE id=?", (sid,)).fetchone()
    assert row["label"] == "auth-refactor"
    assert row["status"] == "active"


def test_get_or_create_existing(conn):
    sid1 = scopes.get_or_create(conn, "auth-refactor")
    sid2 = scopes.get_or_create(conn, "auth-refactor")
    assert sid1 == sid2


def test_close_moves_facts_to_cold(conn):
    sid = scopes.get_or_create(conn, "phase-b")
    facts.write(conn, "C[dates]: alw ISO8601", scope_id=sid)
    scopes.close(conn, sid)

    scope = conn.execute("SELECT status FROM sm_scopes WHERE id=?", (sid,)).fetchone()
    assert scope["status"] == "closed"

    fact = conn.execute("SELECT status FROM sm_facts WHERE scope_id=?", (sid,)).fetchone()
    assert fact["status"] == "cold"


def test_tick_explicit_closing_signal(conn):
    sid = scopes.get_or_create(conn, "auth-refactor")
    cooled = scopes.tick(conn, turn=5, message_text="auth feature is merged and deployed")
    assert sid in cooled


def test_tick_silence_cooling(conn):
    sid = scopes.get_or_create(conn, "old-scope")
    # Set current_turn low so distance exceeds threshold
    conn.execute("UPDATE sm_scopes SET current_turn=0 WHERE id=?", (sid,))
    conn.commit()
    cooled = scopes.tick(conn, turn=SCOPE_COOL_TURNS + 1, message_text="unrelated topic")
    assert sid in cooled


def test_get_active_returns_only_active(conn):
    sid1 = scopes.get_or_create(conn, "scope-a")
    sid2 = scopes.get_or_create(conn, "scope-b")
    scopes.close(conn, sid2)

    active = scopes.get_active(conn)
    ids = [s["id"] for s in active]
    assert sid1 in ids
    assert sid2 not in ids
