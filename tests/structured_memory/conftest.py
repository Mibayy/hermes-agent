"""
Shared fixtures for structured memory tests.
"""

import sys
import os
import pytest

# Ensure /root/hermes-agent is on sys.path so `tools.structured_memory` can be imported
sys.path.insert(0, "/root/hermes-agent")

from tools.structured_memory.db import get_sm_connection


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test_sm.db"
    c = get_sm_connection(str(db_path))
    yield c
    c.close()


@pytest.fixture
def session_id(conn):
    """Insert a test session and return its id."""
    from tools.structured_memory.db import sm_now
    sid = "test-session-001"
    conn.execute(
        "INSERT INTO sm_sessions (id, started_at, last_turn) VALUES (?, ?, 0)",
        (sid, sm_now()),
    )
    conn.commit()
    return sid
