"""Tests for structured_memory/gauge.py — pressure levels and merging."""

import pytest
from tools.structured_memory.constants import MAX_ACTIVE_CHARS
from tools.structured_memory import facts, gauge


def test_gauge_empty(conn):
    g = gauge.read(conn)
    assert g["pct"] == 0.0
    assert g["used_chars"] == 0


def test_gauge_increases_on_write(conn):
    facts.write(conn, "C[db.id]: UUID mndtry, nvr autoincrement")
    g = gauge.read(conn)
    assert g["used_chars"] > 0
    assert g["pct"] > 0


def test_merge_duplicates(conn):
    # Write two active facts with same target (bypass dedup via different content)
    import uuid
    from tools.structured_memory.db import sm_now
    for i in range(2):
        uid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sm_facts (id,content,type,target,scope_id,status,"
            "superseded_by,created_at,updated_at,last_accessed,access_count,source_hash) "
            "VALUES (?,?,?,?,NULL,'active',NULL,?,?,NULL,0,?)",
            (uid, f"val {i}", "C", "db.id", sm_now(), sm_now(), uid[:16]),
        )
    conn.commit()

    merged = gauge._merge_duplicates(conn)
    assert merged == 1

    active_count = conn.execute(
        "SELECT COUNT(*) FROM sm_facts WHERE target='db.id' AND status='active'"
    ).fetchone()[0]
    assert active_count == 1


def test_check_and_act_no_action_below_threshold(conn):
    facts.write(conn, "C[x]: small fact")
    result = gauge.check_and_act(conn)
    assert result["actions"] == []


def test_push_oldest_to_cold(conn):
    # Fill gauge above 95% by inserting large facts directly
    import uuid
    from tools.structured_memory.db import sm_now
    chunk = "x" * 1000
    for i in range(int(MAX_ACTIVE_CHARS * 0.96 / 1000) + 1):
        uid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sm_facts (id,content,type,target,scope_id,status,"
            "superseded_by,created_at,updated_at,last_accessed,access_count,source_hash) "
            "VALUES (?,?,?,?,NULL,'active',NULL,?,?,NULL,0,?)",
            (uid, chunk, "V", f"key{i}", sm_now(), sm_now(), uid[:16]),
        )
    conn.commit()

    pushed = gauge._push_oldest_to_cold(conn, target_pct=85.0)
    assert pushed > 0

    g = gauge.read(conn)
    assert g["pct"] < 95.0
