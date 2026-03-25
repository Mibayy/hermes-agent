# hermes-memory: Structured Memory via MCP

Persistent, structured memory for Hermes sessions that survives context compression.

## The problem

During long sessions, context compression removes older messages. Constraints decided
at turn 5 vanish by turn 50. The agent forgets what was agreed, re-asks questions,
and contradicts earlier decisions.

The existing `memory` tool (MEMORY.md / USER.md) stores free-text entries injected
at session start. It works well for user preferences and environment facts, but has
no structure, no search, no scope lifecycle, and no automatic pressure management.

## What hermes-memory adds

A structured fact store with typed notation, FTS5 search, scoped lifecycle,
and automatic gauge-based memory management:

- **7 MCP tools**: write, search, tick, status, reflect, export, purge
- **Typed facts**: C[target] (constraints), D[target] (decisions), V[target] (values)
- **Scope lifecycle**: auto-cooling after 6 turns of silence, topic shift detection
- **Gauge pressure**: automatic dedup at 70%, archival at 85%, synthesis at 95%
- **Zero infra**: SQLite + FTS5, no cloud, no embedding model, no API keys

## Installation

```bash
pip install hermes-memory
```

## Configuration

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hermes-memory:
    command: "hermes-memory"
    env:
      HERMES_MEMORY_DB: "~/.hermes/memory.db"
```

That's it. Hermes discovers the 7 tools at startup.

## How the agent uses it

At session start, `memory_status` injects a compact block (~180 tokens):

```
[MEMORY_SPEC v1.0]

NOTATION
C[t]: constraint  D[t]: decision  V[t]: value
?[t]: unknown     ✓[t]: done      ~[t]: obsolete

ABBREVS
cfg impl msg req usr resp prod feat dev deps auth err db btn ...

RULES
- call memory_write() for any C/D/V/? detected
- call memory_search() before answering on known topics
- call memory_tick(turn, message) on every user message
- call memory_reflect(topic) when user asks about history

[MEMORY 42% (4.2k/10k)]

C[db.id]: UUID mndtry, nvr autoincrement
D[auth]: JWT 7j refresh 6j
V[srv.prod]: api.example.com:3005
```

The agent then calls `memory_write` whenever it detects a constraint, decision,
or value in the conversation. The notation achieves 65-78% token savings vs raw
messages.

## Relationship to existing memory tool

hermes-memory is **complementary**, not a replacement:

| | memory tool (MEMORY.md) | hermes-memory |
|---|---|---|
| Storage | flat text file | SQLite + FTS5 |
| Search | substring match | full-text search |
| Structure | free-form entries | typed notation (C/D/V/?/✓/~) |
| Scoping | none | auto-scoped lifecycle |
| Pressure | manual char limit | automatic gauge (merge/archive/synthesis) |
| Use case | user prefs, env facts | project constraints, decisions, values |

Both can run simultaneously. The memory tool handles "who is the user" and
"what's the environment". hermes-memory handles "what did we decide about auth"
and "what are the project constraints".

## Managing MEMORY.md pressure

MEMORY.md is injected into every turn — keeping it compact directly reduces token
cost. hermes-memory acts as a relief valve: structured facts that accumulate in
MEMORY.md can be migrated to the DB and replaced with a single `memory_search()`
call on-demand.

### Compression guidelines

When MEMORY.md approaches its character limit, apply these rules in order:

1. **Abbreviate first** — most entries can be cut 40-60% with standard shorthands:
   `pr`=pour, `req`=requis, `chg`=changement, `cfg`=config, `dep`=dépendance,
   `w/`=avec, `→`=vers/puis, `↑`=upgrade, `0`=zéro, drop articles and filler words.

2. **Migrate structured facts** — any C/D/V entry that isn't needed every turn
   belongs in hermes-memory, not MEMORY.md:
   ```
   # Before (in MEMORY.md, ~120 chars):
   "Database IDs must always be UUID, never autoincrement. Decided in sprint 3."

   # After (migrated to hermes-memory):
   memory_write("C[db.id]: UUID mndtry, nvr autoincrement")
   # MEMORY.md entry removed — retrieved with memory_search("db id") when needed
   ```

3. **Remove duplicates** — facts already in hermes-memory DB don't need to be in
   MEMORY.md. Keep only the pointer: `memory_search() pour détails projet`.

### Automated pressure relief

A cron job can automate this at scheduled intervals:

```
# Example cron prompt (runs 2x/day):
Check MEMORY.md usage via memory_status(). If MEMORY > 55% or USER > 55%:
- Abbreviate verbose entries
- Migrate structured C/D/V facts to memory_write()
- Remove entries already covered in hermes-memory DB
Target: both stores below 45%.
If already below threshold, do nothing.
```

This keeps injection cost stable even across long-running projects.

## Links

- PyPI: https://pypi.org/project/hermes-memory/
- Spec: `MEMORY_SPEC.md` in the hermes-memory repository
