#!/usr/bin/env python3
"""
Structured Memory Tool — native hermes-agent tool replacing the hermes-memory MCP server.

Exposes 7 agent-callable tools backed by the hermes-memory SQLite store:
    mcp_memory_write    store a typed fact (C/D/V/?/checkmark/tilde notation)
    mcp_memory_search   FTS5 search over hot + cold facts
    mcp_memory_reflect  synthesis grouped by type for a topic
    mcp_memory_export   dump all facts as plain notation
    mcp_memory_purge    hard-delete superseded / archived facts
    mcp_memory_optimize compress MEMORY.md/USER.md + migrate facts to DB
    mcp_memory_gauge    return current gauge state (pct, used_chars, max_chars)

memory_tick is NOT a tool — it is called automatically by the agent loop via
tick_structured_memory(). memory_status is also not a tool — it is injected
into the system prompt at session start via get_structured_memory_injection().

Thread-safety: one SQLite connection per OS thread via threading.local().
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy import guard — hermes_memory may not be installed in all envoys
# ---------------------------------------------------------------------------
try:
    from hermes_memory.core import db as _db
    from hermes_memory.core import facts as _facts
    from hermes_memory.core import gauge as _gauge
    from hermes_memory.core import scopes as _scopes
    from hermes_memory.core.db import ABBREV_DICT, GAUGE_WARN
    from hermes_memory.core.facts import TYPE_DISPLAY, MemoryFullError

    _HM_AVAILABLE = True
except ImportError:
    _HM_AVAILABLE = False


# ---------------------------------------------------------------------------
# Connection pool — one connection per thread
# ---------------------------------------------------------------------------

_DB_PATH = Path(os.getenv("HERMES_MEMORY_DB", str(
    Path.home() / ".hermes" / "memory.db"
)))

_local = threading.local()


def _conn():
    """Return a per-thread SQLite connection (lazy init)."""
    if not _HM_AVAILABLE:
        raise RuntimeError("hermes_memory package is not installed.")
    if not getattr(_local, "conn", None):
        _local.conn = _db.get_connection(_DB_PATH)
    return _local.conn


# ---------------------------------------------------------------------------
# Default session ID — one UUID per process; callers can override per call
# ---------------------------------------------------------------------------

_SESSION_ID = str(uuid.uuid4())


def _resolve_session(kwargs: dict) -> str:
    return kwargs.get("session_id") or _SESSION_ID


def _ensure_session(session_id: str) -> None:
    c = _conn()
    existing = c.execute(
        "SELECT id FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not existing:
        c.execute(
            "INSERT INTO sessions (id, started_at, last_turn) VALUES (?,?,0)",
            (session_id, _db.now()),
        )
        c.commit()


# ---------------------------------------------------------------------------
# Tool handler functions (return plain str, not TextContent)
# ---------------------------------------------------------------------------

def _handle_mcp_memory_write(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY WRITE FAILED] hermes_memory package is not installed."

    try:
        c = _conn()
        session_id = args.get("session_id") or _resolve_session(kw)
        _ensure_session(session_id)

        raw_content = args.get("content", "").strip()
        if not raw_content:
            return "[MEMORY WRITE FAILED] content is required."

        scope_label = args.get("scope")
        scope_id = None

        if scope_label:
            scope_id = _scopes.get_or_create(c, scope_label, session_id)
        else:
            row = c.execute(
                "SELECT active_scope_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row:
                scope_id = row["active_scope_id"]

        # Run pressure relief BEFORE the write so the store has headroom.
        _gauge.check_and_act(c)

        try:
            result = _facts.write(c, raw_content, scope_id=scope_id)
        except MemoryFullError as exc:
            g = _gauge.read(c)
            return (
                f"[MEMORY WRITE FAILED] {exc}\n"
                f"gauge: {g['pct']:.0f}%\n"
                "action: call mcp_memory_purge to reclaim space, "
                "or inform the user that memory is full."
            )
        except ValueError as exc:
            return f"[MEMORY WRITE FAILED] Invalid notation: {exc}"
        except Exception as exc:
            return (
                f"[MEMORY WRITE FAILED] Unexpected error: {exc}\n"
                "The fact was NOT stored. Retry or inform the user."
            )

        # Touch scope with current turn so silence cooling tracks real activity
        if scope_id and result.get("status") == "created":
            current_turn = c.execute(
                "SELECT last_turn FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            turn_val = current_turn["last_turn"] if current_turn else 0
            _scopes.touch(c, scope_id, turn_val)

        # Auto-close scope if a checkmark fact was written
        if result.get("status") == "created" and raw_content.startswith("\u2713"):
            if scope_id:
                _scopes.close(c, scope_id)

        # Re-read gauge after write
        gauge_result = _gauge.check_and_act(c)

        lines = [
            f"stored: {result['id'][:8]}",
            f"gauge:  {gauge_result['pct']:.0f}%",
        ]
        if result.get("truncated"):
            lines.append(
                f"[truncated] content exceeded {_db.MAX_FACT_CHARS} chars and was shortened. "
                "Consider splitting into multiple facts."
            )
        if result.get("conflict_resolved"):
            lines.append(f"superseded: {result['conflict_resolved'][:8]}")
        if gauge_result.get("actions"):
            lines.append("pressure: " + ", ".join(gauge_result["actions"]))
        if gauge_result["pct"] >= GAUGE_WARN:
            lines.append(
                f"[MEMORY WARNING] Store at {gauge_result['pct']:.0f}% capacity. "
                "Consider calling mcp_memory_purge or informing the user to /compress."
            )

        return "\n".join(lines)

    except Exception as exc:
        return f"[MEMORY WRITE FAILED] Internal error: {exc}"


def _handle_mcp_memory_search(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY SEARCH FAILED] hermes_memory package is not installed."

    try:
        c = _conn()
        query = args.get("query", "").strip()
        if not query:
            return "[MEMORY SEARCH FAILED] query is required."

        scope_label = args.get("scope")
        limit = min(int(args.get("limit", 5)), 20)

        scope_id = None
        if scope_label:
            row = c.execute(
                "SELECT id FROM scopes WHERE label=? "
                "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC LIMIT 1",
                (scope_label,)
            ).fetchone()
            if row:
                scope_id = row["id"]

        results = _facts.search(c, query, scope_id=scope_id, limit=limit)

        if not results:
            return "no results"

        lines = []
        for r in results:
            status_tag = "" if r["status"] == "active" else f" [{r['status']}]"
            sym = TYPE_DISPLAY.get(r["type"], r["type"])
            lines.append(f"{sym}[{r['target']}]: {r['content']}{status_tag}")

        return "\n".join(lines)

    except Exception as exc:
        return f"[MEMORY SEARCH FAILED] {exc}"


def _handle_mcp_memory_reflect(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY REFLECT FAILED] hermes_memory package is not installed."

    try:
        c = _conn()
        topic = args.get("topic", "").strip()
        if not topic:
            return "[MEMORY REFLECT FAILED] topic is required."

        limit = min(int(args.get("limit", 20)), 20)

        results = _facts.search(c, topic, limit=limit)

        if not results:
            return f"no facts found for topic: {topic}"

        groups: dict[str, list[str]] = {}
        for r in results:
            sym = TYPE_DISPLAY.get(r["type"], r["type"])
            groups.setdefault(sym, []).append(
                f"  [{r['target']}]: {r['content']}"
                + ("" if r["status"] == "active" else f"  ({r['status']})")
            )

        type_order = ["C", "D", "V", "\u2713", "~", "?"]
        lines = [f"reflection: {topic}  ({len(results)} facts)", ""]
        for sym in type_order:
            if sym not in groups:
                continue
            label = {
                "C": "Constraints", "D": "Decisions", "V": "Values",
                "\u2713": "Resolved", "~": "Obsolete", "?": "Open questions",
            }.get(sym, sym)
            lines.append(f"{label}:")
            lines.extend(groups[sym])
            lines.append("")

        return "\n".join(lines).rstrip()

    except Exception as exc:
        return f"[MEMORY REFLECT FAILED] {exc}"


def _handle_mcp_memory_export(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY EXPORT FAILED] hermes_memory package is not installed."

    try:
        c = _conn()
        scope_label = args.get("scope")
        status_filter = args.get("status", "all")

        scope_id = None
        if scope_label:
            row = c.execute(
                "SELECT id FROM scopes WHERE label=?", (scope_label,)
            ).fetchone()
            if row:
                scope_id = row["id"]

        if status_filter == "active":
            statuses = ("active",)
        elif status_filter == "cold":
            statuses = ("cold",)
        else:
            statuses = ("active", "cold")

        conditions = [f"status IN ({','.join('?' * len(statuses))})"]
        params: list = list(statuses)

        if scope_id:
            conditions.append("scope_id = ?")
            params.append(scope_id)

        rows = c.execute(
            f"SELECT type, target, content FROM facts "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, updated_at DESC",
            params,
        ).fetchall()

        if not rows:
            return "(no facts to export)"

        lines = []
        for r in rows:
            sym = TYPE_DISPLAY.get(r["type"], r["type"])
            lines.append(f"{sym}[{r['target']}]: {r['content']}")

        return "\n".join(lines)

    except Exception as exc:
        return f"[MEMORY EXPORT FAILED] {exc}"


def _handle_mcp_memory_purge(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY PURGE FAILED] hermes_memory package is not installed."

    try:
        c = _conn()
        scope_label = args.get("scope")
        older_than = args.get("older_than_days")

        scope_id = None
        if scope_label:
            row = c.execute(
                "SELECT id FROM scopes WHERE label=?", (scope_label,)
            ).fetchone()
            if row:
                scope_id = row["id"]

        count = _facts.purge(
            c,
            scope_id=scope_id,
            older_than_days=int(older_than) if older_than is not None else None,
        )
        g = _gauge.read(c)

        return f"purged: {count} fact(s)\ngauge: {g['pct']}%"

    except Exception as exc:
        return f"[MEMORY PURGE FAILED] {exc}"


def _handle_mcp_memory_optimize(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY OPTIMIZE FAILED] hermes_memory package is not installed."

    try:
        from hermes_memory.core.optimize import optimize

        c = _conn()
        session_id = args.get("session_id") or _resolve_session(kw)
        _ensure_session(session_id)

        threshold = float(args.get("threshold_pct", 55))
        dry_run = bool(args.get("dry_run", False))

        result = optimize(c, session_id, threshold_pct=threshold, dry_run=dry_run)

        if not result["action_taken"]:
            return (
                f"no action needed\n"
                f"MEMORY: {result['memory_before']}%  USER: {result['user_before']}%\n"
                f"(both below {threshold:.0f}% threshold)"
            )

        lines = [
            "optimized:" + (" (dry run)" if dry_run else ""),
            f"  MEMORY: {result['memory_before']}% -> {result['memory_after']}%"
            + (f"  ({result['memory_migrated']} facts migrated)" if result["memory_migrated"] else ""),
            f"  USER:   {result['user_before']}% -> {result['user_after']}%"
            + (f"  ({result['user_migrated']} facts migrated)" if result["user_migrated"] else ""),
        ]
        total_migrated = result["memory_migrated"] + result["user_migrated"]
        if total_migrated:
            lines.append(f"  {total_migrated} fact(s) moved to hermes-memory DB")

        return "\n".join(lines)

    except Exception as exc:
        return f"[MEMORY OPTIMIZE FAILED] {exc}"


def _handle_mcp_memory_gauge(args: dict, **kw) -> str:
    if not _HM_AVAILABLE:
        return "[MEMORY GAUGE FAILED] hermes_memory package is not installed."

    try:
        c = _conn()
        gauge_result = _gauge.check_and_act(c)
        g = _gauge.read(c)

        lines = [
            f"pct: {g['pct']}%",
            f"used_chars: {g['used_chars']}",
            f"max_chars: {g['max_chars']}",
        ]
        if gauge_result.get("actions"):
            lines.append("actions: " + ", ".join(gauge_result["actions"]))
        else:
            lines.append("actions: (none)")

        return "\n".join(lines)

    except Exception as exc:
        return f"[MEMORY GAUGE FAILED] {exc}"


# ---------------------------------------------------------------------------
# Public helpers called by the agent framework (not tool calls)
# ---------------------------------------------------------------------------

def get_structured_memory_injection(session_id: str = None) -> str:
    """
    Build the system prompt injection block for structured memory.

    Called at agent startup. Returns a compact formatted string showing
    the gauge state, hot facts, and active scopes.
    Returns empty string if the DB is unavailable or there are no facts.
    """
    if not _HM_AVAILABLE:
        return ""

    try:
        c = _conn()
        sid = session_id or _SESSION_ID
        _ensure_session(sid)

        g = _gauge.read(c)
        hot = _facts.get_hot(c)
        active_scopes = _scopes.get_active(c)

        if not hot and not active_scopes:
            return ""

        lines = [
            f"[STRUCTURED MEMORY — {g['pct']}% ({g['used_chars']}/{g['max_chars']})]"
        ]

        for f in hot:
            type_sym = TYPE_DISPLAY.get(f["type"], f["type"])
            lines.append(f"{type_sym}[{f['target']}]: {f['content']}")

        if active_scopes:
            scope_labels = [s["label"] for s in active_scopes]
            lines.append(f"active scopes: {', '.join(scope_labels)}")

        return "\n".join(lines)

    except Exception:
        return ""


def tick_structured_memory(
    turn: int, message_text: str = "", session_id: str = None
) -> None:
    """
    Advance the turn counter and trigger scope auto-cooling.

    Called by the agent loop on every user message. Never raises.
    """
    if not _HM_AVAILABLE:
        return

    try:
        c = _conn()
        sid = session_id or _SESSION_ID
        _ensure_session(sid)
        _scopes.tick(c, turn, message_text=message_text, session_id=sid)
    except Exception:
        pass  # Silent — never interrupt the agent loop


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_MCP_MEMORY_WRITE_SCHEMA = {
    "name": "mcp_memory_write",
    "description": (
        "Store a structured fact using MEMORY_SPEC notation.\n"
        "Format: TYPE[target]: content\n"
        "Types: C=constraint  D=decision  V=value  ?=unknown  \u2713=done  ~=obsolete\n"
        "Examples:\n"
        "  C[db.id]: UUID mndtry, nvr autoincrement\n"
        "  D[auth]: JWT 7j refresh 6j\n"
        "  V[srv.prod]: api.example.com:3005\n"
        "  \u2713[auth]: deployed prod\n"
        "Call this whenever a constraint, decision, or value is established. "
        "Scope auto-cooling and pressure relief are handled automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Fact in MEMORY_SPEC notation, e.g. C[db.id]: UUID mndtry",
            },
            "scope": {
                "type": "string",
                "description": "Scope label (e.g. 'auth-refactor', 'phase-b'). Inherits active scope if omitted.",
            },
            "session_id": {
                "type": "string",
                "description": "Session identifier. Uses process default if omitted.",
            },
        },
        "required": ["content"],
    },
}

_MCP_MEMORY_SEARCH_SCHEMA = {
    "name": "mcp_memory_search",
    "description": (
        "Search active and cold facts by keyword or phrase.\n"
        "Call this before answering on any topic that may have been discussed before.\n"
        "Returns up to `limit` facts (default 5, max 20) sorted by relevance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'UUID database' or 'auth JWT'",
            },
            "scope": {
                "type": "string",
                "description": "Restrict search to a specific scope label.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (1-20, default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

_MCP_MEMORY_REFLECT_SCHEMA = {
    "name": "mcp_memory_reflect",
    "description": (
        "Synthesize all facts (hot + cold) related to a topic into a concise summary.\n"
        "Use this when the user asks 'what did we decide about X?' or before making\n"
        "a decision on a topic with long history. Does not write to memory.\n"
        "Returns a structured synthesis grouped by fact type."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic to reflect on, e.g. 'auth' or 'database schema'",
            },
            "limit": {
                "type": "integer",
                "description": "Max facts to include in reflection (default 20).",
                "default": 20,
            },
        },
        "required": ["topic"],
    },
}

_MCP_MEMORY_EXPORT_SCHEMA = {
    "name": "mcp_memory_export",
    "description": (
        "Export all facts (hot + cold) as plain MEMORY_SPEC notation, one per line.\n"
        "Use for: context snapshot before a long session, transferring memory between\n"
        "agents or sessions, debugging what is stored. Read-only, no writes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "Export only facts belonging to this scope label.",
            },
            "status": {
                "type": "string",
                "enum": ["active", "cold", "all"],
                "description": "Which facts to include. Default: all (active + cold).",
                "default": "all",
            },
        },
    },
}

_MCP_MEMORY_PURGE_SCHEMA = {
    "name": "mcp_memory_purge",
    "description": (
        "Hard-delete superseded and archived facts. "
        "Use to reclaim space after a scope is fully closed, "
        "or as periodic garbage collection."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "Purge only facts belonging to this scope label.",
            },
            "older_than_days": {
                "type": "integer",
                "description": "Only purge facts older than N days with no recent access.",
            },
        },
    },
}

_MCP_MEMORY_OPTIMIZE_SCHEMA = {
    "name": "mcp_memory_optimize",
    "description": (
        "Compress MEMORY.md and USER.md to reduce injection cost, and migrate "
        "any C/D/V/? facts found in those files into the hermes-memory DB.\n"
        "Only acts when MEMORY.md > threshold% or USER.md > threshold% (default 55%).\n"
        "If both are below threshold, returns immediately with no changes.\n"
        "Safe to call on a schedule (e.g. 2x/day via cron)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "threshold_pct": {
                "type": "number",
                "description": "Trigger threshold in percent (default 55). Only act if either file exceeds this.",
                "default": 55,
            },
            "dry_run": {
                "type": "boolean",
                "description": "If true, compute what would change but do not write files.",
                "default": False,
            },
            "session_id": {"type": "string"},
        },
    },
}

_MCP_MEMORY_GAUGE_SCHEMA = {
    "name": "mcp_memory_gauge",
    "description": (
        "Return the current memory gauge state: percentage used, char counts, "
        "and any pressure-relief actions taken. Use this for a quick health check "
        "or to decide whether to call mcp_memory_purge."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def check_structured_memory_requirements() -> bool:
    """Returns True if the hermes_memory package is installed and usable."""
    return _HM_AVAILABLE


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

from tools.registry import registry  # noqa: E402 — must come after definitions

registry.register(
    name="mcp_memory_write",
    toolset="structured_memory",
    schema=_MCP_MEMORY_WRITE_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_write(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\U0001f9e0",
)

registry.register(
    name="mcp_memory_search",
    toolset="structured_memory",
    schema=_MCP_MEMORY_SEARCH_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_search(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\U0001f50d",
)

registry.register(
    name="mcp_memory_reflect",
    toolset="structured_memory",
    schema=_MCP_MEMORY_REFLECT_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_reflect(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\U0001f4ad",
)

registry.register(
    name="mcp_memory_export",
    toolset="structured_memory",
    schema=_MCP_MEMORY_EXPORT_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_export(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\U0001f4e4",
)

registry.register(
    name="mcp_memory_purge",
    toolset="structured_memory",
    schema=_MCP_MEMORY_PURGE_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_purge(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\U0001f5d1",
)

registry.register(
    name="mcp_memory_optimize",
    toolset="structured_memory",
    schema=_MCP_MEMORY_OPTIMIZE_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_optimize(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\u2699",
)

registry.register(
    name="mcp_memory_gauge",
    toolset="structured_memory",
    schema=_MCP_MEMORY_GAUGE_SCHEMA,
    handler=lambda args, **kw: _handle_mcp_memory_gauge(args, **kw),
    check_fn=check_structured_memory_requirements,
    emoji="\U0001f4ca",
)
