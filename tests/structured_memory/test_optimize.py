"""Tests for memory_optimize — compression and migration logic."""
import textwrap
from pathlib import Path

import pytest

from tools.structured_memory import optimize as opt_module
from tools.structured_memory.optimize import (
    MEMORY_LIMIT,
    USER_LIMIT,
    THRESHOLD_PCT,
    _compress_text,
    _extract_and_migrate,
    optimize,
    MEMORY_PATH,
    USER_PATH,
)
from tools.structured_memory.db import get_sm_connection


# ----------------------------------------------------------------- helpers --

def make_conn(tmp_path):
    path = tmp_path / "test.db"
    return get_sm_connection(str(path))


def fill_file(path: Path, pct: float, limit: int) -> str:
    """Write a file that is `pct`% of `limit` chars. Returns the content."""
    target = int(limit * pct / 100)
    # Use realistic-looking memory entries
    entry = "Some verbose environment fact that takes up space in the file.\n§\n"
    content = (entry * (target // len(entry) + 1))[:target]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content


# ------------------------------------------------------------------- tests --

class TestCompressText:
    def test_french_abbrevs(self):
        text = "Ne jamais modifier pour rien, configuration obligatoire."
        result = _compress_text(text)
        assert "pr" in result or "pour" not in result
        assert "cfg" in result or "configuration" not in result

    def test_no_double_spaces(self):
        result = _compress_text("hello   world  test")
        assert "  " not in result

    def test_preserves_content(self):
        text = "Keep this important fact intact."
        result = _compress_text(text)
        assert "Keep" in result
        assert "intact" in result


class TestExtractAndMigrate:
    def test_migrates_c_fact(self, tmp_path):
        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        text = "Some normal text.\nC[db.id]: UUID mndtry\nMore text."
        cleaned, migrated = _extract_and_migrate(text, conn, "s1")

        assert len(migrated) == 1
        assert "C[db.id]" in migrated[0]
        assert "C[db.id]" not in cleaned
        assert "Some normal text." in cleaned
        assert "More text." in cleaned

    def test_migrates_multiple_types(self, tmp_path):
        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        text = "C[auth]: JWT req\nD[db]: postgres chosen\nV[port]: 3007\nNormal line."
        cleaned, migrated = _extract_and_migrate(text, conn, "s1")

        assert len(migrated) == 3
        assert "Normal line." in cleaned
        assert "C[auth]" not in cleaned

    def test_keeps_non_fact_lines(self, tmp_path):
        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        text = "Regular entry without MEMORY_SPEC notation.\n§\nAnother entry."
        cleaned, migrated = _extract_and_migrate(text, conn, "s1")

        assert len(migrated) == 0
        assert cleaned == text


class TestOptimize:
    def test_no_action_below_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opt_module, "MEMORY_PATH", tmp_path / "MEMORY.md")
        monkeypatch.setattr(opt_module, "USER_PATH",   tmp_path / "USER.md")

        # 30% — well below 55% threshold
        fill_file(tmp_path / "MEMORY.md", 30, MEMORY_LIMIT)
        fill_file(tmp_path / "USER.md",   30, USER_LIMIT)

        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        result = optimize(conn, "s1")
        assert result["action_taken"] is False
        assert result["memory_migrated"] == 0
        assert result["user_migrated"] == 0

    def test_action_above_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opt_module, "MEMORY_PATH", tmp_path / "MEMORY.md")
        monkeypatch.setattr(opt_module, "USER_PATH",   tmp_path / "USER.md")

        # 70% — above threshold, with compressible text
        mem_path = tmp_path / "MEMORY.md"
        compressible = ("configuration pour le développement, jamais modifier sans validation. " * 30)
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.write_text(compressible[:int(MEMORY_LIMIT * 0.70)], encoding="utf-8")
        fill_file(tmp_path / "USER.md", 30, USER_LIMIT)

        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        result = optimize(conn, "s1")
        assert result["action_taken"] is True
        assert result["memory_after"] < result["memory_before"]

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        mem_path = tmp_path / "MEMORY.md"
        user_path = tmp_path / "USER.md"
        monkeypatch.setattr(opt_module, "MEMORY_PATH", mem_path)
        monkeypatch.setattr(opt_module, "USER_PATH",   user_path)

        original = fill_file(mem_path, 70, MEMORY_LIMIT)
        fill_file(user_path, 30, USER_LIMIT)

        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        optimize(conn, "s1", dry_run=True)
        # File must be unchanged
        assert mem_path.read_text(encoding="utf-8") == original

    def test_migrates_facts_above_threshold(self, tmp_path, monkeypatch):
        mem_path = tmp_path / "MEMORY.md"
        monkeypatch.setattr(opt_module, "MEMORY_PATH", mem_path)
        monkeypatch.setattr(opt_module, "USER_PATH",   tmp_path / "USER.md")

        # Build a 70% file that contains a C/D/V line
        padding = "x" * int(MEMORY_LIMIT * 0.68)
        content = f"C[db.id]: UUID mndtry\n{padding}"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.write_text(content, encoding="utf-8")
        fill_file(tmp_path / "USER.md", 20, USER_LIMIT)

        conn = make_conn(tmp_path)
        conn.execute("INSERT INTO sm_sessions (id, started_at, last_turn) VALUES ('s1', 0, 0)")
        conn.commit()

        result = optimize(conn, "s1")
        assert result["memory_migrated"] >= 1
        # Fact must now be in the DB
        rows = conn.execute("SELECT content FROM sm_facts WHERE target='db.id'").fetchall()
        assert rows
