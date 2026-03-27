# Subagent Architecture v2 — Phase 4: Observability & Checkpointing

---

## Task 11: Structured observability (detailed trace)

**Objective:** When `delegation.observability.detailed_trace: true`, enrich each task result with per-tool timing, token breakdown by tool call, and a structured decision tree. Low cost — data already available in `_run_single_child`.

**Files:**
- Modify: `tools/delegate_tool.py` — `_run_single_child`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_observability.py
def test_detailed_trace_includes_timing():
    # Mock a child with messages that have tool_calls
    # Call _build_detailed_trace(messages, start_times)
    # Assert each trace entry has duration_ms
    pass

def test_detailed_trace_includes_token_breakdown():
    # Assert trace has input_tokens / output_tokens per turn
    pass

def test_basic_trace_unchanged_when_detailed_false():
    # Default behavior: tool_trace is still just name + args_bytes + result_bytes
    pass
```

**Step 2:** Run → FAIL

**Step 3:**

```python
def _build_detailed_trace(
    messages: list,
    tool_start_times: Dict[str, float],  # tool_call_id -> start monotonic
    tool_end_times: Dict[str, float],
    token_counts_by_turn: list,
) -> list:
    """
    Build an enriched trace with timing and token breakdown.
    tool_start_times: populated by hooking into the child's tool dispatch.
    """
    trace = []
    trace_by_id = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            turn_tokens = token_counts_by_turn.pop(0) if token_counts_by_turn else {}
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function", {})
                tc_id = tc.get("id", "")
                entry = {
                    "tool": fn.get("name", "unknown"),
                    "args_bytes": len(fn.get("arguments", "")),
                    "input_tokens": turn_tokens.get("input", 0),
                    "output_tokens": turn_tokens.get("output", 0),
                }
                start = tool_start_times.get(tc_id)
                end = tool_end_times.get(tc_id)
                if start and end:
                    entry["duration_ms"] = round((end - start) * 1000)
                trace.append(entry)
                if tc_id:
                    trace_by_id[tc_id] = entry
        elif msg.get("role") == "tool":
            content = msg.get("content", "")
            tc_id = msg.get("tool_call_id")
            target = trace_by_id.get(tc_id) if tc_id else (trace[-1] if trace else None)
            if target is not None:
                target["result_bytes"] = len(content)
                target["status"] = "error" if "error" in content[:80].lower() else "ok"

    return trace
```

Modify `_run_single_child` to:
1. Check `detailed_trace` config flag
2. If enabled, collect `tool_start_times` / `tool_end_times` by hooking the child's tool dispatch (via progress callback)
3. Call `_build_detailed_trace` instead of the current inline trace builder

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): detailed observability trace with per-tool timing and token breakdown"`

---

## Task 12: Opt-in checkpointing

**Objective:** When `delegation.checkpoint.enabled: true`, save the subagent's conversation state to `state.db` every N iterations. On crash/interrupt, the parent can resume from the last checkpoint. Zero impact on existing users (default: disabled).

**Files:**
- New: `tools/delegate_checkpoint.py`
- Modify: `tools/delegate_tool.py`, `run_agent.py` (hook for checkpoint write)

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_checkpoint.py
import sqlite3, tempfile, os
from tools.delegate_checkpoint import CheckpointStore

def test_checkpoint_save_and_load(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = CheckpointStore(db_path)
    store.save(
        task_id="task_0",
        iteration=5,
        messages=[{"role": "user", "content": "hello"}],
        metadata={"goal": "do something"},
    )
    cp = store.load("task_0")
    assert cp is not None
    assert cp["iteration"] == 5
    assert cp["messages"][0]["content"] == "hello"
    assert cp["metadata"]["goal"] == "do something"

def test_checkpoint_load_missing_returns_none(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    assert store.load("nonexistent") is None

def test_checkpoint_save_overwrites_previous(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    store.save("task_0", 3, [], {})
    store.save("task_0", 7, [{"role": "user", "content": "updated"}], {})
    cp = store.load("task_0")
    assert cp["iteration"] == 7

def test_checkpoint_delete(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    store.save("task_0", 1, [], {})
    store.delete("task_0")
    assert store.load("task_0") is None

def test_checkpoint_list(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    store.save("task_0", 1, [], {"goal": "a"})
    store.save("task_1", 2, [], {"goal": "b"})
    tasks = store.list_checkpoints()
    ids = [t["task_id"] for t in tasks]
    assert "task_0" in ids
    assert "task_1" in ids
```

**Step 2:** Run → FAIL

**Step 3:** Create `tools/delegate_checkpoint.py`:

```python
"""Opt-in checkpoint store for subagent conversations."""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS delegate_checkpoints (
    task_id     TEXT PRIMARY KEY,
    iteration   INTEGER NOT NULL,
    messages    TEXT NOT NULL,   -- JSON
    metadata    TEXT NOT NULL,   -- JSON
    saved_at    REAL NOT NULL
);
"""


class CheckpointStore:
    """
    SQLite-backed checkpoint store for subagent task state.
    Uses the same state.db as structured memory when db_path is empty.
    """

    def __init__(self, db_path: str = ""):
        if not db_path:
            # Default: ~/.hermes/state.db (same as structured memory)
            db_path = str(Path.home() / ".hermes" / "state.db")
        self._db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def save(
        self,
        task_id: str,
        iteration: int,
        messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO delegate_checkpoints
                   (task_id, iteration, messages, metadata, saved_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    task_id,
                    iteration,
                    json.dumps(messages, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    time.time(),
                ),
            )

    def load(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM delegate_checkpoints WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "iteration": row["iteration"],
            "messages": json.loads(row["messages"]),
            "metadata": json.loads(row["metadata"]),
            "saved_at": row["saved_at"],
        }

    def delete(self, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM delegate_checkpoints WHERE task_id = ?",
                (task_id,),
            )

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id, iteration, metadata, saved_at FROM delegate_checkpoints"
            ).fetchall()
        return [
            {
                "task_id": r["task_id"],
                "iteration": r["iteration"],
                "metadata": json.loads(r["metadata"]),
                "saved_at": r["saved_at"],
            }
            for r in rows
        ]
```

Integration in `delegate_tool.py`:
- In `_build_child_agent`, if checkpoint enabled: pass a `checkpoint_hook` callable to the child
- The hook is called by `run_agent.py` every N iterations: `checkpoint_store.save(task_id, iteration, messages, metadata)`
- On task start: check `checkpoint_store.load(task_id)` — if found, prefill messages to resume
- On task completion: `checkpoint_store.delete(task_id)` (clean up)

In `run_agent.py`:
```python
# In the main agent loop, after each iteration:
if self._checkpoint_hook and self._iteration_count % self._checkpoint_interval == 0:
    try:
        self._checkpoint_hook(self._iteration_count, list(self.messages))
    except Exception as e:
        logger.debug("Checkpoint hook failed: %s", e)
```

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): opt-in checkpointing — save/resume subagent state every N iterations"`

---

## Task 13: Wire everything into delegate_task

**Objective:** Update `delegate_task()` to pass all new parameters through and call the right helpers in the right order. Ensure backward compat — all new behavior is gated behind config/task-level flags.

**Files:**
- Modify: `tools/delegate_tool.py`

**The new execution flow:**

```
delegate_task(tasks, ...) called
  ↓
Load config (all new keys)
  ↓
[DAG enabled?] → topological_sort(tasks)
  ↓
[Blackboard enabled?] → bb = Blackboard()
  ↓
For each task (respecting DAG wave order):
  ↓
  [Semantic cache hit?] → return cached, skip spawn
  ↓
  Resolve deps (DAG) → inject predecessor summaries into context
  ↓
  _build_child_agent(memory_mode, skills, blackboard, checkpoint_store, ...)
  ↓
  _run_with_retry(
    task, parent_agent, max_retries,
    inner=_run_single_child(detailed_trace)
  )
  ↓
  _run_with_verify(result, task, parent_agent) → verdict
  ↓
  on_task_done(task_index, result) callback
  ↓
Return all results
```

**Step 1:** Integration tests covering the full flow with all features enabled.

```python
# tests/tools/test_delegate_integration.py
def test_full_flow_all_features_disabled_is_backward_compat(mock_parent):
    """With empty config, behavior is identical to pre-v2."""
    # call delegate_task with a single simple goal
    # assert result has same shape as before
    pass

def test_full_flow_with_dag_and_blackboard(mock_parent, monkeypatch):
    """DAG + blackboard together: task B receives task A's result."""
    pass

def test_full_flow_with_verify(mock_parent, monkeypatch):
    """verify:true task spawns critic, result has verdict field."""
    pass
```

**Step 2:** Run → FAIL

**Step 3:** Wire helpers as described in flow above.

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): wire all v2 features into delegate_task"`

---

## Task 14: Update tool description + docs

**Objective:** Update the `delegate_task` tool description (shown to the LLM), add inline docstrings for all new params, write `website/docs/user-guide/features/delegate-task.md`.

**Files:**
- Modify: `tools/delegate_tool.py` (tool schema description)
- New: `website/docs/user-guide/features/delegate-task.md`

**Key doc sections:**
- Quick start (unchanged usage still works)
- New parameters reference table
- Dependency graph example
- Memory access modes table
- Checkpointing guide
- Generator-critic example
- Relationship to #3093 and #3294

**Step 5:** `git commit -m "docs(delegate): delegate_task v2 documentation and tool description update"`

---

## Task 15: PR description

**Objective:** Write the full PR body referencing #3093 and #3294, explaining the dependency model and the opt-in approach.

**Template structure:**

```markdown
## feat(delegate): subagent architecture v2

Closes: (none — standalone feature)
Depends on: #3093 (graceful no-op if not merged), #3294 (referenced in docs)

### What this PR does

[One paragraph vision]

### Dependency matrix

| Feature | Depends on | Default |
|---------|-----------|---------|
| Memory read-only | #3093 (graceful no-op) | off |
| Skill inheritance | #3294 (docs/skill) | off |
| Blackboard | none | off |
| DAG task deps | none | off |
| Generator-critic | none | off |
| Intelligent retry | none | off |
| Semantic dedup cache | #3093 (graceful no-op) | off |
| Detailed observability | none | off |
| Checkpointing | none | off |
| Configurable max_depth | none | 1 |

### Backward compatibility

All existing `delegate_task` calls work unchanged. Every new feature requires explicit opt-in via config or task-level parameter.

### Tests

[N] new tests across [files]

### Example: full-featured task

[code example showing DAG + skills + verify]
```

**Step 5:** `git commit -m "docs: PR description for subagent v2"`
