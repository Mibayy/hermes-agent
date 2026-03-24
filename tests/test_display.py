"""Tests for agent/display.py — build_tool_preview() and _detect_tool_failure()."""

import pytest
from agent.display import build_tool_preview, _detect_tool_failure
import json


class TestBuildToolPreview:
    """Tests for build_tool_preview defensive handling and normal operation."""

    def test_none_args_returns_none(self):
        """PR #453: None args should not crash, should return None."""
        assert build_tool_preview("terminal", None) is None

    def test_empty_dict_returns_none(self):
        """Empty dict has no keys to preview."""
        assert build_tool_preview("terminal", {}) is None

    def test_known_tool_with_primary_arg(self):
        """Known tool with its primary arg should return a preview string."""
        result = build_tool_preview("terminal", {"command": "ls -la"})
        assert result is not None
        assert "ls -la" in result

    def test_web_search_preview(self):
        result = build_tool_preview("web_search", {"query": "hello world"})
        assert result is not None
        assert "hello world" in result

    def test_read_file_preview(self):
        result = build_tool_preview("read_file", {"path": "/tmp/test.py", "offset": 1})
        assert result is not None
        assert "/tmp/test.py" in result

    def test_unknown_tool_with_fallback_key(self):
        """Unknown tool but with a recognized fallback key should still preview."""
        result = build_tool_preview("custom_tool", {"query": "test query"})
        assert result is not None
        assert "test query" in result

    def test_unknown_tool_no_matching_key(self):
        """Unknown tool with no recognized keys should return None."""
        result = build_tool_preview("custom_tool", {"foo": "bar"})
        assert result is None

    def test_long_value_truncated(self):
        """Preview should truncate long values."""
        long_cmd = "a" * 100
        result = build_tool_preview("terminal", {"command": long_cmd}, max_len=40)
        assert result is not None
        assert len(result) <= 43  # max_len + "..."

    def test_process_tool_with_none_args(self):
        """Process tool special case should also handle None args."""
        assert build_tool_preview("process", None) is None

    def test_process_tool_normal(self):
        result = build_tool_preview("process", {"action": "poll", "session_id": "abc123"})
        assert result is not None
        assert "poll" in result

    def test_todo_tool_read(self):
        result = build_tool_preview("todo", {"merge": False})
        assert result is not None
        assert "reading" in result

    def test_todo_tool_with_todos(self):
        result = build_tool_preview("todo", {"todos": [{"id": "1", "content": "test", "status": "pending"}]})
        assert result is not None
        assert "1 task" in result

    def test_memory_tool_add(self):
        result = build_tool_preview("memory", {"action": "add", "target": "user", "content": "test note"})
        assert result is not None
        assert "user" in result

    def test_session_search_preview(self):
        result = build_tool_preview("session_search", {"query": "find something"})
        assert result is not None
        assert "find something" in result

    def test_false_like_args_zero(self):
        """Non-dict falsy values should return None, not crash."""
        assert build_tool_preview("terminal", 0) is None
        assert build_tool_preview("terminal", "") is None
        assert build_tool_preview("terminal", []) is None


class TestDetectToolFailure:
    """Tests for _detect_tool_failure — memory write failure detection and surfacing."""

    def test_memory_full_detected(self):
        result = json.dumps({"success": False, "error": "Memory at 2200/2200 chars. Adding this entry would exceed the limit."})
        is_fail, suffix = _detect_tool_failure("memory", result)
        assert is_fail is True
        assert suffix == " [full]"

    def test_memory_full_keyword_detected(self):
        result = json.dumps({"success": False, "error": "Memory store is full (100%). Run memory_purge to reclaim space."})
        is_fail, suffix = _detect_tool_failure("memory", result)
        assert is_fail is True
        assert suffix == " [full]"

    def test_memory_generic_error_detected(self):
        result = json.dumps({"success": False, "error": "Failed to write memory file: permission denied"})
        is_fail, suffix = _detect_tool_failure("memory", result)
        assert is_fail is True
        assert suffix == " [error]"

    def test_memory_success_not_flagged(self):
        result = json.dumps({"success": True, "target": "memory", "entries": [], "usage": "0% — 0/2200 chars"})
        is_fail, suffix = _detect_tool_failure("memory", result)
        assert is_fail is False
        assert suffix == ""

    def test_memory_with_injected_instruction_strips_cleanly(self):
        """Agent instruction suffix injected for LLM routing must not break JSON parsing."""
        base = json.dumps({"success": False, "error": "Adding this entry would exceed the limit."})
        result = base + "\n\n[AGENT INSTRUCTION — DO NOT SKIP] The memory write just failed..."
        is_fail, suffix = _detect_tool_failure("memory", result)
        assert is_fail is True
        assert suffix == " [full]"

    def test_terminal_exit_code_failure(self):
        result = json.dumps({"exit_code": 1, "output": "command not found"})
        is_fail, suffix = _detect_tool_failure("terminal", result)
        assert is_fail is True
        assert "1" in suffix

    def test_terminal_exit_zero_success(self):
        result = json.dumps({"exit_code": 0, "output": "ok"})
        is_fail, suffix = _detect_tool_failure("terminal", result)
        assert is_fail is False

    def test_none_result_never_fails(self):
        is_fail, suffix = _detect_tool_failure("memory", None)
        assert is_fail is False
        assert suffix == ""
