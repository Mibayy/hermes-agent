---
sidebar_position: 4
title: "Structured Memory"
description: "Typed fact store with FTS5 search, scope lifecycle, and automatic gauge-based pressure management — no MCP required"
---

# Structured Memory

Structured memory is a typed, searchable fact store built directly into hermes-agent. It complements the flat-file `memory` tool (MEMORY.md / USER.md) with a SQLite-backed store that scales to hundreds of facts without bloating the system prompt.

:::info Relationship to the flat-file memory tool
The two systems are additive, not exclusive. Use `memory` for the handful of critical facts that should always be in context (user preferences, key conventions). Use `structured_memory` for everything else — project constraints, architecture decisions, environment values, open questions — where you need search, not constant injection.
:::

## Quick Start

Enable the toolset in `config.yaml`:

```yaml
toolsets:
  enabled:
    - structured_memory
```

Or pass it at runtime:

```bash
hermes --toolset structured_memory
```

That's it. No MCP server to install. No external process. No configuration beyond the toolset line.

## How It Works

Facts are stored in `~/.hermes/state.db` (the same database used by sessions and session search) in three tables: `sm_facts`, `sm_scopes`, and `sm_sessions`. A FTS5 virtual table enables sub-millisecond keyword search across all stored facts regardless of how many there are.

At session start, the gauge percentage and hot facts (active facts in active scopes) are injected into the system prompt automatically — no tool call required. As the session progresses, `memory_tick` runs on every user message without consuming a tool-call turn, triggering scope auto-cooling when a scope has been silent for 6+ turns.

## MEMORY_SPEC Notation

Facts are written in a compact typed notation:

```
TYPE[target]: content
```

| Type | Symbol | Meaning |
|------|--------|---------|
| Constraint | `C` | Hard rule that must not be violated |
| Decision | `D` | Architectural or product decision made |
| Value | `V` | Environment variable, URL, secret reference |
| Unknown | `?` | Open question or unresolved item |
| Done | `✓` | Completed task or resolved item |
| Obsolete | `~` | Superseded — soft-delete, kept for history |

**Examples:**

```
C[db.id]: UUID mndtry, nvr autoincrement
D[auth]: JWT 7d refresh 6d, stored httpOnly cookie
V[srv.prod]: api.example.com:3005
?[deploy]: unclear if blue/green or rolling — ask Louis
✓[auth]: deployed to prod 2026-03-01
~[db.id]: old autoincrement scheme (superseded)
```

The abbreviation dictionary (`ABBREV_DICT`) is injected into the system prompt as a writing guide so facts stay compact from the start.

## The 7 Tools

### `mcp_memory_write`

Store a typed fact.

```python
mcp_memory_write(content="C[db.id]: UUID mndtry", scope="auth-refactor")
```

- `content` — fact in MEMORY_SPEC notation (required)
- `scope` — scope label, e.g. `"auth-refactor"` (optional, falls back to session default)
- `session_id` — explicit session ID (optional)

Automatically calls `gauge.check_and_act()` before writing to keep pressure in check.

### `mcp_memory_search`

FTS5 full-text search across all facts.

```python
mcp_memory_search(query="auth JWT", limit=5)
```

Returns facts ranked by relevance, including type, target, content, scope, and status. Default limit: 5, max: 20. Use before answering any question about a topic that may have been discussed before.

### `mcp_memory_reflect`

Synthesize facts on a topic into a structured summary.

```python
mcp_memory_reflect(topic="database schema", limit=20)
```

Groups results by fact type. Use when the user asks "what did we decide about X?" or before making a significant decision with prior history.

### `mcp_memory_export`

Dump all facts as plain MEMORY_SPEC notation, one per line.

```python
mcp_memory_export(scope="auth-refactor", status="active")
```

- `scope` — filter to one scope (optional)
- `status` — `"active"`, `"cold"`, or `"all"` (default: `"all"`)

Use for context snapshots before long sessions or transferring state between agents.

### `mcp_memory_purge`

Hard-delete superseded and archived facts.

```python
mcp_memory_purge(older_than_days=30)
```

Use to reclaim space after a scope is fully closed, or as periodic garbage collection.

### `mcp_memory_optimize`

Compress MEMORY.md and USER.md using the compression map, and migrate any MEMORY_SPEC-formatted lines into the structured store.

```python
mcp_memory_optimize(threshold_pct=55, dry_run=False)
```

- `threshold_pct` — only optimize if usage exceeds this percentage (default: 55)
- `dry_run` — preview changes without writing (default: false)

Run this in a cron job or when the flat-file memory approaches capacity.

### `mcp_memory_gauge`

Return current pressure state.

```python
mcp_memory_gauge()
```

Returns `used_chars`, `max_chars` (10,000), `pct`, and any actions triggered by automatic pressure management.

## Automatic Pressure Management

The gauge system prevents the store from silently filling up. At each write, pressure is checked and one of four actions taken automatically:

| Threshold | Action |
|-----------|--------|
| ≥70% | Merge duplicate facts (same target + scope) |
| ≥80% | Warning injected into tool response |
| ≥85% | Archive facts from closed scopes to cold storage |
| ≥95% | Push oldest active facts to cold; LLM synthesis if available |

"Cold" facts are kept in the DB and remain searchable but are not injected into the system prompt. `mcp_memory_purge` permanently removes them.

## Scopes and Lifecycle

Scopes are named workstreams — e.g. `"auth-refactor"`, `"phase-b"`. Facts written to a scope stay hot as long as that scope is active.

A scope auto-cools when it has received no writes for 6 turns (configurable via `SCOPE_COOL_TURNS` in `constants.py`). Cooled scopes move their facts to cold storage, freeing headroom for new work without permanently discarding anything.

```python
# Write a fact to a named scope
mcp_memory_write(content="D[auth]: use Supabase Auth, not custom JWT", scope="auth-refactor")

# Export just that scope
mcp_memory_export(scope="auth-refactor")

# Clean up after the scope is done
mcp_memory_purge(scope="auth-refactor")
```

## System Prompt Injection

At session start, the following is automatically prepended to the system prompt (zero tool calls consumed):

```
[STRUCTURED MEMORY — 23% (2300/10000 chars)]
C[db.id]: UUID mndtry, nvr autoincrement
D[auth]: JWT 7d refresh 6d
active scopes: auth-refactor
```

The injection only appears when there are active facts. If the DB is empty, nothing is added.

## Comparison: `memory` vs `structured_memory`

| Feature | `memory` (flat-file) | `structured_memory` |
|---------|---------------------|---------------------|
| Storage | MEMORY.md / USER.md | SQLite (state.db) |
| Capacity | ~3,575 chars total | 10,000 chars active + unlimited cold |
| Search | None (always in prompt) | FTS5 keyword search |
| Types | Untyped text | C / D / V / ? / ✓ / ~ |
| Scopes | None | Named workstreams with auto-cooling |
| Injection | Full content, every session | Gauge + hot facts only |
| Pressure management | Manual | Automatic (merge / archive / push cold) |
| Best for | Key preferences, short conventions | Architecture decisions, constraints, open questions |

## Abbreviation Guide

The agent is primed to write compact facts using a standard abbreviation dictionary. Examples:

| Full form | Abbreviation |
|-----------|-------------|
| configuration | cfg |
| database | db |
| authentication | auth |
| mandatory | mndtry |
| never | nvr |
| environment | env |
| production | prod |
| repository | repo |

Facts over 400 chars are rejected — if a fact doesn't fit, it should be split or summarized.

## Background: Why Not MCP?

This feature was originally prototyped as an MCP server (`hermes-memory`, PR #2692). The MCP boundary was removed for the native integration because:

- No subprocess or stdio transport overhead
- Zero user configuration beyond the toolset line
- Direct access to `state.db` and the session lifecycle
- `memory_tick` runs automatically inside the agent loop — no tool call consumed
- Gauge and hot facts are injected at prompt-build time — no tool call consumed

The core logic (schema, FTS5, gauge tiers, scope lifecycle, MEMORY_SPEC parser, compression map) is identical to what was developed and tested in #2692 — 52 tests, 8 months of design iteration. The delivery changed; the implementation did not.
