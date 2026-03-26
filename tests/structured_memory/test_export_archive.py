"""Tests for memory_export and _archive_cold_scopes grace window."""

import time
import pytest
from tools.structured_memory.db import sm_now
from tools.structured_memory.constants import TYPE_DISPLAY
from tools.structured_memory import facts, gauge, scopes


def test_export_returns_notation(conn):
    facts.write(conn, "C[db.id]: UUID mndtry")
    facts.write(conn, "D[auth]: JWT 7j")
    facts.write(conn, "V[srv]: 1.2.3.4:3005")

    rows = conn.execute(
        "SELECT type, target, content FROM sm_facts WHERE status='active'"
    ).fetchall()
    lines = [f"{TYPE_DISPLAY.get(r['type'], r['type'])}[{r['target']}]: {r['content']}" for r in rows]

    assert any(l.startswith("C[db.id]") for l in lines)
    assert any(l.startswith("D[auth]") for l in lines)
    assert any(l.startswith("V[srv]") for l in lines)


def test_export_includes_cold(conn):
    sid = scopes.get_or_create(conn, "old-feat")
    facts.write(conn, "D[cache]: Redis 30j", scope_id=sid)
    scopes.close(conn, sid)

    row = conn.execute("SELECT status FROM sm_facts WHERE target='cache'").fetchone()
    assert row["status"] == "cold"

    # cold fact should be exportable
    cold_rows = conn.execute(
        "SELECT type, target, content FROM sm_facts WHERE status IN ('active','cold')"
    ).fetchall()
    targets = [r["target"] for r in cold_rows]
    assert "cache" in targets


def test_archive_spares_recently_accessed(conn):
    sid = scopes.get_or_create(conn, "closed-scope")
    facts.write(conn, "C[x]: important fact", scope_id=sid)
    scopes.close(conn, sid)

    # Simulate a recent search that updated last_accessed
    conn.execute(
        "UPDATE sm_facts SET last_accessed=? WHERE target='x'",
        (sm_now(),),
    )
    conn.commit()

    archived = gauge._archive_cold_scopes(conn)

    # Fact was recently accessed — must NOT be archived
    assert archived == 0
    row = conn.execute("SELECT status FROM sm_facts WHERE target='x'").fetchone()
    assert row["status"] == "cold"


def test_archive_removes_stale_cold(conn):
    sid = scopes.get_or_create(conn, "old-scope")
    facts.write(conn, "C[stale]: old fact", scope_id=sid)
    scopes.close(conn, sid)

    # Set last_accessed to 2 days ago
    old_ts = sm_now() - 2 * 86_400
    conn.execute(
        "UPDATE sm_facts SET last_accessed=? WHERE target='stale'",
        (old_ts,),
    )
    conn.commit()

    archived = gauge._archive_cold_scopes(conn)
    assert archived == 1

    row = conn.execute("SELECT status FROM sm_facts WHERE target='stale'").fetchone()
    assert row["status"] == "archived"


def test_scopes_close_atomic(conn):
    """close() must leave no intermediate state — scope=closed, facts=cold."""
    sid = scopes.get_or_create(conn, "feat-x")
    facts.write(conn, "D[feat.x]: use websocket", scope_id=sid)

    scopes.close(conn, sid)

    scope_row = conn.execute("SELECT status FROM sm_scopes WHERE id=?", (sid,)).fetchone()
    fact_row  = conn.execute("SELECT status FROM sm_facts WHERE scope_id=?", (sid,)).fetchone()

    assert scope_row["status"] == "closed"
    assert fact_row["status"] == "cold"
