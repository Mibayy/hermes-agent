"""Tests for MCP subagent workspace guard (set_project_root / switch_project blocking)."""
import json
import threading
import unittest
from unittest.mock import patch, MagicMock

from tools.mcp_tool import set_subagent_mcp_guard, _make_tool_handler, _SUBAGENT_BLOCKED_TOOL_NAMES


class TestSubagentGuardConstants(unittest.TestCase):
    def test_blocked_names_present(self):
        assert "set_project_root" in _SUBAGENT_BLOCKED_TOOL_NAMES
        assert "switch_project" in _SUBAGENT_BLOCKED_TOOL_NAMES
        assert "reindex" in _SUBAGENT_BLOCKED_TOOL_NAMES

    def test_read_only_tools_not_blocked(self):
        # Common read-only tools must NOT be in the blocked set
        assert "find_symbol" not in _SUBAGENT_BLOCKED_TOOL_NAMES
        assert "get_structure_summary" not in _SUBAGENT_BLOCKED_TOOL_NAMES
        assert "search_codebase" not in _SUBAGENT_BLOCKED_TOOL_NAMES


class TestSubagentGuardHandler(unittest.TestCase):
    """_make_tool_handler should block mutating tools when guard is active."""

    def _make_handler(self, tool_name: str):
        return _make_tool_handler("codebase-index", tool_name, 10.0)

    def test_blocked_when_guard_active(self):
        handler = self._make_handler("set_project_root")
        set_subagent_mcp_guard(True)
        try:
            result = json.loads(handler({"path": "/tmp/foo"}))
            self.assertIn("error", result)
            self.assertIn("blocked", result["error"])
        finally:
            set_subagent_mcp_guard(False)

    def test_switch_project_blocked_when_guard_active(self):
        handler = self._make_handler("switch_project")
        set_subagent_mcp_guard(True)
        try:
            result = json.loads(handler({"name": "my-project"}))
            self.assertIn("error", result)
        finally:
            set_subagent_mcp_guard(False)

    def test_reindex_blocked_when_guard_active(self):
        handler = self._make_handler("reindex")
        set_subagent_mcp_guard(True)
        try:
            result = json.loads(handler({}))
            self.assertIn("error", result)
        finally:
            set_subagent_mcp_guard(False)

    def test_safe_tool_not_blocked_when_guard_active(self):
        """find_symbol should pass through even when guard is active (hits server check instead)."""
        handler = self._make_handler("find_symbol")
        set_subagent_mcp_guard(True)
        try:
            # Will fail with "not connected" — not our blocked error
            result = json.loads(handler({"name": "Foo"}))
            if "error" in result:
                self.assertNotIn("blocked", result["error"])
        finally:
            set_subagent_mcp_guard(False)

    def test_not_blocked_when_guard_inactive(self):
        """Even set_project_root should not be pre-blocked when guard is off."""
        handler = self._make_handler("set_project_root")
        set_subagent_mcp_guard(False)
        # Will fail with "not connected" — not our blocked error
        result = json.loads(handler({"path": "/tmp/foo"}))
        if "error" in result:
            self.assertNotIn("blocked", result["error"])

    def test_guard_is_thread_local(self):
        """Guard active in one thread must not affect another thread."""
        set_subagent_mcp_guard(True)  # active in main thread
        results = {}

        def other_thread():
            handler = _make_tool_handler("codebase-index", "set_project_root", 10.0)  # noqa
            result = json.loads(handler({"path": "/tmp/foo"}))
            results["blocked"] = "blocked" in result.get("error", "")

        t = threading.Thread(target=other_thread)
        t.start()
        t.join()

        # Other thread should NOT see the guard as active
        self.assertFalse(results.get("blocked", True),
                         "Guard from main thread leaked into child thread")
        set_subagent_mcp_guard(False)


class TestSetSubagentMcpGuard(unittest.TestCase):
    def test_toggle(self):
        from tools.mcp_tool import _tl
        set_subagent_mcp_guard(True)
        self.assertTrue(getattr(_tl, "subagent_guard", False))
        set_subagent_mcp_guard(False)
        self.assertFalse(getattr(_tl, "subagent_guard", False))

    def test_default_is_false(self):
        """A fresh thread should have guard=False (no _tl attr set)."""
        results = {}

        def fresh_thread():
            from tools.mcp_tool import _tl
            results["val"] = getattr(_tl, "subagent_guard", False)

        t = threading.Thread(target=fresh_thread)
        t.start()
        t.join()
        self.assertFalse(results["val"])
