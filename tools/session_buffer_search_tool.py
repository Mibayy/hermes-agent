#!/usr/bin/env python3
"""
Session Buffer Search Tool — Mid-Session Context Recall (issue #2667)

Searches the current session's compressed buffer: a rolling archive of messages
that were dropped by context compression earlier in this conversation.

Unlike session_search (which searches *past* sessions), this tool searches
*the current session only* — filling the gap between active context and
long-term memory when a long conversation has been compressed multiple times.

Flow:
  1. FTS5 search on compressed_buffer scoped to this session's root_session_id
  2. Returns matching snippets with role, timestamp, and compression round
  3. No LLM call — instant, zero cost
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def _format_timestamp(ts: Union[int, float, None]) -> str:
    """Format a Unix timestamp to a human-readable string."""
    if ts is None:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(float(ts))
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except Exception:
        return str(ts)


def _resolve_root_session_id(db, session_id: str) -> str:
    """Walk up the parent_session_id chain to find the root session."""
    visited: set = set()
    sid = session_id
    while sid and sid not in visited:
        visited.add(sid)
        try:
            s = db.get_session(sid)
            if not s:
                break
            parent = s.get("parent_session_id")
            if parent:
                sid = parent
            else:
                break
        except Exception:
            break
    return sid


def session_buffer_search(
    query: str,
    role_filter: str = None,
    limit: int = 10,
    db=None,
    current_session_id: str = None,
) -> str:
    """
    Search the current session's compressed message buffer.

    Returns matching snippets from messages that were dropped by context
    compression earlier in this conversation.
    """
    if db is None:
        return json.dumps(
            {"success": False, "error": "Session database not available."},
            ensure_ascii=False,
        )

    if not query or not query.strip():
        # No-query mode: return buffer stats
        if not current_session_id:
            return json.dumps(
                {"success": False, "error": "No current_session_id provided."},
                ensure_ascii=False,
            )
        try:
            root_sid = _resolve_root_session_id(db, current_session_id)
            count = db.get_compressed_buffer_count(root_sid)
            return json.dumps(
                {
                    "success": True,
                    "mode": "stats",
                    "root_session_id": root_sid,
                    "archived_message_count": count,
                    "message": (
                        f"{count} messages archived from earlier in this session. "
                        "Use a keyword query to search them."
                        if count
                        else "No messages archived yet (context compression hasn't triggered)."
                    ),
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    query = query.strip()
    limit = min(limit, 30)

    if not current_session_id:
        return json.dumps(
            {"success": False, "error": "No current_session_id provided — cannot scope buffer search."},
            ensure_ascii=False,
        )

    try:
        root_sid = _resolve_root_session_id(db, current_session_id)

        role_list: Optional[List[str]] = None
        if role_filter and role_filter.strip():
            role_list = [r.strip() for r in role_filter.split(",") if r.strip()]

        matches = db.search_compressed_buffer(
            query=query,
            root_session_id=root_sid,
            role_filter=role_list,
            limit=limit,
            offset=0,
        )

        if not matches:
            return json.dumps(
                {
                    "success": True,
                    "query": query,
                    "results": [],
                    "count": 0,
                    "message": "No matches in this session's compressed buffer. Try session_search for past sessions.",
                },
                ensure_ascii=False,
            )

        results = []
        for m in matches:
            results.append(
                {
                    "role": m.get("role", "unknown"),
                    "snippet": m.get("snippet", ""),
                    "tool_name": m.get("tool_name"),
                    "archived_at": _format_timestamp(m.get("archived_at")),
                    "compression_round": m.get("compression_round", 1),
                }
            )

        total_archived = db.get_compressed_buffer_count(root_sid)

        return json.dumps(
            {
                "success": True,
                "query": query,
                "results": results,
                "count": len(results),
                "total_archived_messages": total_archived,
                "note": (
                    "These are messages from earlier in this same conversation that were "
                    "removed from active context by compression. For past sessions, use session_search."
                ),
            },
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error("session_buffer_search failed: %s", e, exc_info=True)
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def check_session_buffer_search_requirements() -> bool:
    """Requires SQLite state database."""
    try:
        from hermes_state import DEFAULT_DB_PATH
        return DEFAULT_DB_PATH.parent.exists()
    except ImportError:
        return False


SESSION_BUFFER_SEARCH_SCHEMA = {
    "name": "session_buffer_search",
    "description": (
        "Search the current session's compressed message archive — messages from "
        "earlier in this conversation that were removed from active context by compression.\n\n"
        "USE THIS when:\n"
        "- The user asks 'what did we say earlier about X?' or 'remember when we discussed Y?'\n"
        "- You need to recall something from the start of a long conversation\n"
        "- Context compression has already occurred (you'll notice a compaction summary in your context)\n\n"
        "DO NOT confuse with session_search (which searches past/completed sessions).\n"
        "This tool searches ONLY the CURRENT conversation's archived turns.\n\n"
        "Returns FTS5 snippets instantly — no LLM cost, no delay.\n\n"
        "Search syntax: keywords, phrases (\"exact phrase\"), boolean (python NOT java), prefix (deploy*).\n"
        "Call with no query to get a count of archived messages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search keywords or phrases to find in archived messages. "
                    "Omit to get a count of archived messages."
                ),
            },
            "role_filter": {
                "type": "string",
                "description": "Optional: filter by message role (comma-separated). E.g. 'user,assistant'.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default: 10, max: 30).",
                "default": 10,
            },
        },
        "required": [],
    },
}


# --- Registry ---
from tools.registry import registry

registry.register(
    name="session_buffer_search",
    toolset="session_search",  # bundled with session_search toolset
    schema=SESSION_BUFFER_SEARCH_SCHEMA,
    handler=lambda args, **kw: session_buffer_search(
        query=args.get("query") or "",
        role_filter=args.get("role_filter"),
        limit=args.get("limit", 10),
        db=kw.get("db"),
        current_session_id=kw.get("current_session_id"),
    ),
    check_fn=check_session_buffer_search_requirements,
    emoji="📼",
)
