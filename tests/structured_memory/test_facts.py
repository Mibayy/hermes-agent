"""Tests for structured_memory/facts.py — CRUD, contradiction detection, dedup, error surfacing."""

import pytest
from tools.structured_memory.constants import MAX_FACT_CHARS, MAX_ACTIVE_CHARS
from tools.structured_memory import facts
from tools.structured_memory.facts import MemoryFullError


def test_parse_notation_constraint(conn):
    t, target, content = facts.parse_notation("C[db.id]: UUID mndtry, nvr autoincrement")
    assert t == "C"
    assert target == "db.id"
    assert content == "UUID mndtry, nvr autoincrement"


def test_parse_notation_done(conn):
    t, target, content = facts.parse_notation("✓[auth]: deployed prod")
    assert t == "done"
    assert target == "auth"


def test_parse_notation_invalid(conn):
    with pytest.raises(ValueError):
        facts.parse_notation("not a valid fact")


def test_write_creates_fact(conn):
    result = facts.write(conn, "C[db.id]: UUID mndtry")
    assert result["status"] == "created"
    assert result["id"]
    row = conn.execute("SELECT * FROM sm_facts WHERE id=?", (result["id"],)).fetchone()
    assert row["type"] == "C"
    assert row["target"] == "db.id"
    assert row["status"] == "active"


def test_write_dedup(conn):
    r1 = facts.write(conn, "C[db.id]: UUID mndtry")
    r2 = facts.write(conn, "C[db.id]: UUID mndtry")
    assert r1["id"] == r2["id"]
    assert r2["status"] == "dedup"
    count = conn.execute("SELECT COUNT(*) FROM sm_facts WHERE target='db.id'").fetchone()[0]
    assert count == 1


def test_write_contradiction_supersedes(conn):
    r1 = facts.write(conn, "D[auth]: sessions Redis 30j")
    r2 = facts.write(conn, "D[auth]: JWT 7j refresh 6j")

    assert r2["conflict_resolved"] == r1["id"]

    old = conn.execute("SELECT status FROM sm_facts WHERE id=?", (r1["id"],)).fetchone()
    assert old["status"] == "superseded"

    new = conn.execute("SELECT status FROM sm_facts WHERE id=?", (r2["id"],)).fetchone()
    assert new["status"] == "active"


def test_search_finds_fact(conn):
    facts.write(conn, "C[db.id]: UUID mndtry, nvr autoincrement")
    results = facts.search(conn, "UUID")
    assert len(results) == 1
    assert results[0]["target"] == "db.id"


def test_search_excludes_superseded(conn):
    facts.write(conn, "D[auth]: sessions Redis 30j")
    facts.write(conn, "D[auth]: JWT 7j refresh 6j")
    results = facts.search(conn, "auth")
    assert all(r["status"] != "superseded" for r in results)


def test_search_updates_access_count(conn):
    facts.write(conn, "V[srv.prod]: 1.2.3.4:3005")
    facts.search(conn, "srv.prod")
    row = conn.execute("SELECT access_count FROM sm_facts WHERE target='srv.prod'").fetchone()
    assert row["access_count"] == 1


def test_purge_removes_superseded(conn):
    facts.write(conn, "D[auth]: old")
    facts.write(conn, "D[auth]: new")
    count = facts.purge(conn)
    assert count == 1
    remaining = conn.execute("SELECT COUNT(*) FROM sm_facts WHERE status='superseded'").fetchone()[0]
    assert remaining == 0


def test_write_truncates_oversized_content(conn):
    long_content = "x" * (MAX_FACT_CHARS + 100)
    result = facts.write(conn, f"C[big]: {long_content}")
    assert result["status"] == "created"
    assert result["truncated"] is True
    row = conn.execute("SELECT content FROM sm_facts WHERE id=?", (result["id"],)).fetchone()
    assert len(row["content"]) <= MAX_FACT_CHARS
    assert row["content"].endswith("…")


def test_write_short_content_not_truncated(conn):
    result = facts.write(conn, "C[small]: short content")
    assert result["truncated"] is False
    row = conn.execute("SELECT content FROM sm_facts WHERE id=?", (result["id"],)).fetchone()
    assert not row["content"].endswith("…")


def test_write_raises_memory_full_error(conn):
    chunk = "a" * (MAX_FACT_CHARS - 10)
    needed = (MAX_ACTIVE_CHARS // MAX_FACT_CHARS) + 2
    for i in range(needed):
        try:
            facts.write(conn, f"V[flood.{i}]: {chunk}")
        except MemoryFullError:
            break

    with pytest.raises(MemoryFullError, match="Memory store is full"):
        facts.write(conn, "C[overflow]: this should fail")
