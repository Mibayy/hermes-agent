"""OpenViking context database integration for Hermes Agent.

Provides semantic search and browsing over memories stored in OpenViking,
including ingested external conversations (e.g., from Open WebUI exports).

OpenViking is a self-hosted memory server (https://github.com/open-viking/openviking).
Configure via environment variables:
  OPENVIKING_ENDPOINT  - server URL (default: http://127.0.0.1:1933)
  OPENVIKING_API_KEY   - optional API key for secured deployments
"""

import json
import os
import httpx
from tools.registry import registry


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_OPENVIKING_ENDPOINT = os.getenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
_OPENVIKING_API_KEY = os.getenv("OPENVIKING_API_KEY", "")
_TIMEOUT = 60


def _client() -> httpx.Client:
    headers = {"Content-Type": "application/json"}
    if _OPENVIKING_API_KEY:
        headers["X-API-Key"] = _OPENVIKING_API_KEY
    return httpx.Client(
        base_url=_OPENVIKING_ENDPOINT,
        headers=headers,
        timeout=_TIMEOUT,
    )


def check_requirements() -> bool:
    """Check if OpenViking server is reachable."""
    try:
        with _client() as c:
            r = c.get("/health")
            return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tool: viking_search
# ---------------------------------------------------------------------------

def viking_search(
    query: str,
    mode: str = "auto",
    target_uri: str = "",
    limit: int = 10,
    task_id: str | None = None,
) -> str:
    """Semantic search across OpenViking memories, resources, and skills."""
    with _client() as c:
        # Choose endpoint based on mode
        if mode == "fast":
            endpoint = "/api/v1/search/find"
        elif mode == "deep":
            endpoint = "/api/v1/search/search"
        else:
            # Auto: use fast search for short queries, deep for complex ones
            words = query.split()
            if len(words) > 8 or len(query) > 80 or "?" in query:
                endpoint = "/api/v1/search/search"
            else:
                endpoint = "/api/v1/search/find"

        payload = {"query": query, "limit": limit}
        if target_uri:
            payload["target_uri"] = target_uri

        r = c.post(endpoint, json=payload)
        data = r.json()

        raw_result = data.get("result", {})

        # Response is {"memories": [...], "resources": [...], "skills": [...], "total": N}
        all_items = []
        if isinstance(raw_result, dict):
            for category in ("memories", "resources", "skills"):
                all_items.extend(raw_result.get(category, []))
        elif isinstance(raw_result, list):
            all_items = raw_result

        if not all_items:
            return json.dumps({"results": [], "count": 0, "message": "No results found."})

        # Format results for the agent
        formatted = []
        for item in all_items:
            entry = {
                "uri": item.get("uri", ""),
                "score": round(item.get("score", 0), 3),
                "type": item.get("context_type", item.get("type", "")),
                "abstract": item.get("abstract", item.get("description", "")),
            }
            name = item.get("name", "")
            if name:
                entry["name"] = name
            formatted.append(entry)

        return json.dumps({"results": formatted, "count": len(formatted)})


# ---------------------------------------------------------------------------
# Tool: viking_read
# ---------------------------------------------------------------------------

def viking_read(
    uri: str,
    level: str = "auto",
    task_id: str | None = None,
) -> str:
    """Read content from a viking:// URI at the specified detail level."""
    with _client() as c:
        # Determine if it's a directory or file via stat
        is_dir = False
        try:
            r = c.get("/api/v1/fs/stat", params={"uri": uri})
            if r.status_code == 200:
                stat = r.json().get("result", {})
                is_dir = stat.get("is_dir", stat.get("isDir", False))
        except Exception:
            pass

        if level == "auto":
            level = "overview" if is_dir else "read"

        # Directories: use abstract/overview endpoints (LLM-generated summaries)
        if is_dir and level in ("abstract", "overview"):
            r = c.get(f"/api/v1/content/{level}", params={"uri": uri})
            if r.status_code == 200:
                content = r.json().get("result", "")
                return json.dumps({"uri": uri, "level": level, "content": content})
            return json.dumps({"error": f"Failed to read {uri}: HTTP {r.status_code}"})

        # Individual files: use grep to get full content
        r = c.post("/api/v1/search/grep", json={"pattern": ".", "uri": uri, "recursive": False})
        if r.status_code == 200:
            data = r.json()
            matches = data.get("result", {})
            if isinstance(matches, dict):
                matches = matches.get("matches", [])
            if isinstance(matches, list):
                lines = [m.get("content", m) if isinstance(m, dict) else str(m) for m in matches]
                content = "\n".join(lines)
                return json.dumps({"uri": uri, "level": "read", "content": content})

        # Fallback: read directly from disk if the file maps to local storage
        data_root = os.path.expanduser("~/.openviking/data/viking/default")
        rel = uri.replace("viking://", "").lstrip("/")
        local_path = os.path.join(data_root, rel)
        if os.path.isfile(local_path):
            with open(local_path) as f:
                content = f.read()
            return json.dumps({"uri": uri, "level": "read", "content": content})

        return json.dumps({"error": f"Could not read content at {uri}"})


# ---------------------------------------------------------------------------
# Tool: viking_browse
# ---------------------------------------------------------------------------

def viking_browse(
    uri: str = "viking://",
    view: str = "tree",
    task_id: str | None = None,
) -> str:
    """Browse the OpenViking filesystem layout."""
    with _client() as c:
        if view == "stat":
            r = c.get("/api/v1/fs/stat", params={"uri": uri})
        elif view == "list":
            r = c.get("/api/v1/fs/ls", params={"uri": uri})
        else:  # tree (default)
            r = c.get("/api/v1/fs/tree", params={"uri": uri, "recursive": "true", "depth": "4"})

        if r.status_code != 200:
            return json.dumps({"error": f"Failed to browse {uri}: HTTP {r.status_code}"})

        data = r.json()
        result = data.get("result", data)
        return json.dumps({"uri": uri, "view": view, "content": result})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="viking_search",
    toolset="openviking",
    schema={
        "name": "viking_search",
        "description": (
            "Search across long-term memories, past conversations, and knowledge "
            "stored in OpenViking. Use this to recall information from previous "
            "sessions, imported conversations, user preferences, learned patterns, "
            "and any other context that has been ingested into the memory database. "
            "Returns semantically relevant results ranked by score."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Can be a question, topic, or keywords.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "fast", "deep"],
                    "description": (
                        "Search mode. 'fast' for quick keyword-like search, "
                        "'deep' for intent-analyzed search with session context, "
                        "'auto' to choose automatically based on query complexity."
                    ),
                },
                "target_uri": {
                    "type": "string",
                    "description": (
                        "Optional viking:// URI to scope the search. "
                        "e.g. 'viking://user/' for user memories only."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 10).",
                },
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: viking_search(
        query=args.get("query", ""),
        mode=args.get("mode", "auto"),
        target_uri=args.get("target_uri", ""),
        limit=args.get("limit", 10),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_requirements,
    description="Search OpenViking long-term memory",
    emoji="🧠",
)

registry.register(
    name="viking_read",
    toolset="openviking",
    schema={
        "name": "viking_read",
        "description": (
            "Read content from a specific viking:// URI. Use after viking_search "
            "to read full details of a search result, or to explore known paths. "
            "Supports three detail levels: 'abstract' (~100 tokens summary), "
            "'overview' (~2k tokens structured summary), 'read' (full content)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The viking:// URI to read.",
                },
                "level": {
                    "type": "string",
                    "enum": ["auto", "abstract", "overview", "read"],
                    "description": (
                        "Detail level. 'abstract' for one-sentence summary, "
                        "'overview' for structured summary, 'read' for full content, "
                        "'auto' to choose based on content type."
                    ),
                },
            },
            "required": ["uri"],
        },
    },
    handler=lambda args, **kw: viking_read(
        uri=args.get("uri", ""),
        level=args.get("level", "auto"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_requirements,
    description="Read content from OpenViking memory",
    emoji="📖",
)

registry.register(
    name="viking_browse",
    toolset="openviking",
    schema={
        "name": "viking_browse",
        "description": (
            "Browse the OpenViking filesystem layout to discover what memories, "
            "resources, and skills are stored. Use 'tree' for a directory tree view, "
            "'list' for a flat listing, or 'stat' for metadata about a specific path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The viking:// URI to browse (default: root).",
                },
                "view": {
                    "type": "string",
                    "enum": ["tree", "list", "stat"],
                    "description": "View type: 'tree', 'list', or 'stat'.",
                },
            },
        },
    },
    handler=lambda args, **kw: viking_browse(
        uri=args.get("uri", "viking://"),
        view=args.get("view", "tree"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_requirements,
    description="Browse OpenViking filesystem",
    emoji="📂",
)
