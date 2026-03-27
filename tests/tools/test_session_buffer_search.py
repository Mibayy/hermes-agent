"""Tests for tools/session_buffer_search_tool.py and the SessionDB buffer methods."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from tools.session_buffer_search_tool import (
    session_buffer_search,
    _format_timestamp,
    _resolve_root_session_id,
    SESSION_BUFFER_SEARCH_SCHEMA,
)
from hermes_state import SessionDB


# =========================================================================
# Helpers / fixtures
# =========================================================================

@pytest.fixture()
def db(tmp_path):
    """Fresh in-memory-style SessionDB backed by a temp file."""
    db_path = tmp_path / "test_state.db"
    _db = SessionDB(db_path=db_path)
    yield _db
    _db.close()


def _make_session(db, session_id="sess_root", parent=None):
    db.create_session(
        session_id=session_id,
        source="cli",
        model="test-model",
        parent_session_id=parent,
    )
    return session_id


def _messages(roles_contents):
    """Build a minimal message list from (role, content) pairs."""
    return [{"role": r, "content": c} for r, c in roles_contents]


# =========================================================================
# _format_timestamp
# =========================================================================

class TestFormatTimestamp:
    def test_none_returns_unknown(self):
        assert _format_timestamp(None) == "unknown"

    def test_float_ts(self):
        ts = time.time()
        result = _format_timestamp(ts)
        assert isinstance(result, str) and len(result) > 4

    def test_zero_ts_does_not_crash(self):
        result = _format_timestamp(0.0)
        assert isinstance(result, str)


# =========================================================================
# SessionDB.archive_compressed_messages
# =========================================================================

class TestArchiveCompressedMessages:
    def test_basic_archive(self, db):
        _make_session(db)
        msgs = _messages([
            ("user", "Tell me about Tavily"),
            ("assistant", "Tavily is a search API"),
        ])
        count = db.archive_compressed_messages("sess_root", msgs, compression_round=1)
        assert count == 2

    def test_skips_system_messages(self, db):
        _make_session(db)
        msgs = _messages([
            ("system", "You are a helpful assistant"),
            ("user", "hello"),
        ])
        count = db.archive_compressed_messages("sess_root", msgs)
        assert count == 1  # system skipped

    def test_skips_empty_content(self, db):
        _make_session(db)
        msgs = [{"role": "user", "content": ""}, {"role": "user", "content": "   "}]
        count = db.archive_compressed_messages("sess_root", msgs)
        assert count == 0

    def test_skips_pruned_placeholder(self, db):
        _make_session(db)
        msgs = _messages([
            ("tool", "[Tool output pruned — see context summary]"),
            ("user", "real content"),
        ])
        count = db.archive_compressed_messages("sess_root", msgs)
        assert count == 1  # pruned tool msg skipped

    def test_returns_zero_for_no_archivable_messages(self, db):
        _make_session(db)
        count = db.archive_compressed_messages("sess_root", [])
        assert count == 0


# =========================================================================
# SessionDB.get_compressed_buffer_count
# =========================================================================

class TestGetCompressedBufferCount:
    def test_count_zero_initially(self, db):
        _make_session(db)
        assert db.get_compressed_buffer_count("sess_root") == 0

    def test_count_after_archive(self, db):
        _make_session(db)
        msgs = _messages([("user", "hello"), ("assistant", "world")])
        db.archive_compressed_messages("sess_root", msgs)
        assert db.get_compressed_buffer_count("sess_root") == 2

    def test_count_scoped_to_session(self, db):
        _make_session(db, "sess_a")
        _make_session(db, "sess_b")
        db.archive_compressed_messages("sess_a", _messages([("user", "A content")]))
        assert db.get_compressed_buffer_count("sess_b") == 0
        assert db.get_compressed_buffer_count("sess_a") == 1


# =========================================================================
# SessionDB.search_compressed_buffer
# =========================================================================

class TestSearchCompressedBuffer:
    def test_search_finds_match(self, db):
        _make_session(db)
        db.archive_compressed_messages(
            "sess_root",
            _messages([("user", "What is Tavily?"), ("assistant", "Tavily is a search API for AI agents")]),
        )
        results = db.search_compressed_buffer("Tavily", root_session_id="sess_root")
        assert len(results) >= 1
        assert any("Tavily" in r.get("snippet", "") for r in results)

    def test_search_no_match_returns_empty(self, db):
        _make_session(db)
        db.archive_compressed_messages("sess_root", _messages([("user", "hello world")]))
        results = db.search_compressed_buffer("Tavily", root_session_id="sess_root")
        assert results == []

    def test_search_scoped_to_root_session(self, db):
        _make_session(db, "sess_a")
        _make_session(db, "sess_b")
        db.archive_compressed_messages("sess_a", _messages([("user", "secret content here")]))
        db.archive_compressed_messages("sess_b", _messages([("user", "other content here")]))
        results = db.search_compressed_buffer("secret", root_session_id="sess_b")
        assert results == []  # sess_b doesn't have "secret"

    def test_search_returns_empty_for_empty_query(self, db):
        _make_session(db)
        db.archive_compressed_messages("sess_root", _messages([("user", "hello")]))
        results = db.search_compressed_buffer("", root_session_id="sess_root")
        assert results == []

    def test_search_role_filter(self, db):
        _make_session(db)
        db.archive_compressed_messages(
            "sess_root",
            _messages([
                ("user", "user mentions Tavily here"),
                ("assistant", "assistant also mentions Tavily"),
            ]),
        )
        results = db.search_compressed_buffer("Tavily", root_session_id="sess_root", role_filter=["user"])
        assert all(r["role"] == "user" for r in results)

    def test_search_respects_limit(self, db):
        _make_session(db)
        msgs = _messages([(("user" if i % 2 == 0 else "assistant"), f"keyword occurrence {i}") for i in range(10)])
        db.archive_compressed_messages("sess_root", msgs)
        results = db.search_compressed_buffer("keyword", root_session_id="sess_root", limit=3)
        assert len(results) <= 3


# =========================================================================
# SessionDB.clear_compressed_buffer
# =========================================================================

class TestClearCompressedBuffer:
    def test_clear_removes_entries(self, db):
        _make_session(db)
        db.archive_compressed_messages("sess_root", _messages([("user", "hello")]))
        assert db.get_compressed_buffer_count("sess_root") == 1
        deleted = db.clear_compressed_buffer("sess_root")
        assert deleted == 1
        assert db.get_compressed_buffer_count("sess_root") == 0

    def test_clear_does_not_affect_other_sessions(self, db):
        _make_session(db, "sess_a")
        _make_session(db, "sess_b")
        db.archive_compressed_messages("sess_a", _messages([("user", "A")]))
        db.archive_compressed_messages("sess_b", _messages([("user", "B")]))
        db.clear_compressed_buffer("sess_a")
        assert db.get_compressed_buffer_count("sess_b") == 1


# =========================================================================
# session_buffer_search (tool function)
# =========================================================================

class TestSessionBufferSearchTool:
    def test_no_db_returns_error(self):
        result = json.loads(session_buffer_search("Tavily", db=None, current_session_id="x"))
        assert result["success"] is False
        assert "not available" in result["error"]

    def test_no_session_id_with_query_returns_error(self, db):
        result = json.loads(session_buffer_search("Tavily", db=db, current_session_id=None))
        assert result["success"] is False

    def test_empty_query_stats_mode(self, db):
        _make_session(db)
        result = json.loads(session_buffer_search("", db=db, current_session_id="sess_root"))
        assert result["success"] is True
        assert result["mode"] == "stats"
        assert "archived_message_count" in result

    def test_empty_query_no_session_id_returns_error(self, db):
        result = json.loads(session_buffer_search("", db=db, current_session_id=None))
        assert result["success"] is False

    def test_search_finds_archived_content(self, db):
        _make_session(db)
        db.archive_compressed_messages(
            "sess_root",
            _messages([("user", "We discussed Tavily at 09:30")]),
        )
        result = json.loads(session_buffer_search("Tavily", db=db, current_session_id="sess_root"))
        assert result["success"] is True
        assert result["count"] >= 1
        assert any("Tavily" in r["snippet"] for r in result["results"])

    def test_search_no_results_returns_empty_with_hint(self, db):
        _make_session(db)
        result = json.loads(session_buffer_search("nonexistent_xyz", db=db, current_session_id="sess_root"))
        assert result["success"] is True
        assert result["count"] == 0
        assert "session_search" in result.get("message", "")

    def test_search_walks_parent_chain(self, db):
        """Buffer search resolves to root session even when called with child session_id."""
        _make_session(db, "sess_root")
        _make_session(db, "sess_child", parent="sess_root")
        db.archive_compressed_messages(
            "sess_root",
            _messages([("user", "original Tavily discussion")]),
        )
        # Call with child session ID — should find root buffer
        result = json.loads(session_buffer_search("Tavily", db=db, current_session_id="sess_child"))
        assert result["success"] is True
        assert result["count"] >= 1

    def test_limit_capped_at_30(self, db):
        _make_session(db)
        msgs = _messages([("user", f"keyword content {i}") for i in range(40)])
        db.archive_compressed_messages("sess_root", msgs)
        result = json.loads(session_buffer_search("keyword", limit=100, db=db, current_session_id="sess_root"))
        assert result["count"] <= 30

    def test_result_includes_metadata_fields(self, db):
        _make_session(db)
        db.archive_compressed_messages("sess_root", _messages([("user", "something notable here")]))
        result = json.loads(session_buffer_search("notable", db=db, current_session_id="sess_root"))
        assert result["success"] is True
        if result["count"] > 0:
            r = result["results"][0]
            assert "role" in r
            assert "snippet" in r
            assert "archived_at" in r
            assert "compression_round" in r


# =========================================================================
# Tool schema
# =========================================================================

class TestSessionBufferSearchSchema:
    def test_schema_name(self):
        assert SESSION_BUFFER_SEARCH_SCHEMA["name"] == "session_buffer_search"

    def test_description_distinguishes_from_session_search(self):
        desc = SESSION_BUFFER_SEARCH_SCHEMA["description"]
        assert "session_search" in desc
        assert "current" in desc.lower()

    def test_query_param_not_required(self):
        # query is optional (call with no query returns stats)
        required = SESSION_BUFFER_SEARCH_SCHEMA["parameters"].get("required", [])
        assert "query" not in required

    def test_limit_param_present(self):
        props = SESSION_BUFFER_SEARCH_SCHEMA["parameters"]["properties"]
        assert "limit" in props


# =========================================================================
# _resolve_root_session_id
# =========================================================================

class TestResolveRootSessionId:
    def test_root_resolves_to_itself(self, db):
        _make_session(db, "root")
        assert _resolve_root_session_id(db, "root") == "root"

    def test_child_resolves_to_root(self, db):
        _make_session(db, "root")
        _make_session(db, "child", parent="root")
        assert _resolve_root_session_id(db, "child") == "root"

    def test_grandchild_resolves_to_root(self, db):
        _make_session(db, "root")
        _make_session(db, "child", parent="root")
        _make_session(db, "grandchild", parent="child")
        assert _resolve_root_session_id(db, "grandchild") == "root"

    def test_unknown_session_returns_input(self, db):
        result = _resolve_root_session_id(db, "nonexistent_id")
        assert result == "nonexistent_id"
