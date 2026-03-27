# Subagent Architecture v2 — Phase 2: Collaboration Features

---

## Task 5: Shared blackboard between siblings

**Objective:** Provide a thread-safe shared dict passed to all sibling tasks in a batch. Each subagent can read/write named keys. Opt-in via `delegation.blackboard.enabled: true` or per-call `blackboard=True`.

**Files:**
- Modify: `tools/delegate_tool.py`
- New: `tools/delegate_blackboard.py`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_blackboard.py
from tools.delegate_blackboard import Blackboard

def test_blackboard_write_read():
    bb = Blackboard()
    bb.set("auth_url", "https://api.example.com/auth")
    assert bb.get("auth_url") == "https://api.example.com/auth"

def test_blackboard_get_missing_returns_default():
    bb = Blackboard()
    assert bb.get("missing") is None
    assert bb.get("missing", "default") == "default"

def test_blackboard_thread_safe():
    import threading
    bb = Blackboard()
    errors = []
    def writer(i):
        try:
            bb.set(f"key_{i}", i)
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(bb.snapshot()) == 50

def test_blackboard_snapshot_is_copy():
    bb = Blackboard()
    bb.set("x", 1)
    snap = bb.snapshot()
    snap["x"] = 999
    assert bb.get("x") == 1  # original not mutated
```

**Step 2:** Run → FAIL

**Step 3:** Create `tools/delegate_blackboard.py`:

```python
"""Shared blackboard for sibling subagents in a delegate_task batch."""
import threading
from typing import Any, Dict, Optional


class Blackboard:
    """Thread-safe key-value store shared across siblings in a batch."""

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        """Return a shallow copy of the current state."""
        with self._lock:
            return dict(self._data)

    def to_context_string(self) -> str:
        """Serialize for injection into child system prompt."""
        snap = self.snapshot()
        if not snap:
            return ""
        lines = ["Shared blackboard (read/write via tool):\n"]
        for k, v in snap.items():
            lines.append(f"  {k}: {v!r}")
        return "\n".join(lines)
```

Then in `delegate_tool.py`:
- Import `Blackboard`
- In `delegate_task()`, if blackboard enabled: create one `Blackboard()` instance
- Pass it to `_build_child_agent()` as `blackboard=bb`
- In `_build_child_system_prompt()`: inject `bb.to_context_string()` if bb is set
- Expose a `blackboard_set` / `blackboard_get` tool via the child's tool registry, backed by the passed `bb` instance

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): shared blackboard for sibling subagents"`

---

## Task 6: on_task_done callback (partial results)

**Objective:** Allow callers to register a callback fired immediately when each task completes — before the batch finishes. Enables dynamic orchestration (spawn task 4 if task 1 fails, etc.).

**Files:**
- Modify: `tools/delegate_tool.py`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_callback.py
def test_on_task_done_called_per_task(mock_parent_agent):
    completed = []
    def on_done(task_index, result):
        completed.append((task_index, result["status"]))

    # Patch _run_single_child to return immediately
    # ... (use monkeypatch to mock child execution)
    # call delegate_task with tasks=[...], on_task_done=on_done
    # assert len(completed) == number_of_tasks

def test_on_task_done_not_required():
    """Omitting on_task_done should not crash."""
    # call delegate_task without on_task_done kwarg
    pass
```

**Step 2:** Run → FAIL

**Step 3:**

In `delegate_task()` signature:
```python
def delegate_task(
    goal=None, context=None, toolsets=None, tasks=None,
    max_iterations=None, parent_agent=None,
    on_task_done=None,        # NEW: callable(task_index, result_dict) | None
    blackboard=None,          # NEW: Blackboard instance | None
) -> str:
```

In the batch loop after `entry = future.result()`:
```python
if callable(on_task_done):
    try:
        on_task_done(entry["task_index"], entry)
    except Exception as e:
        logger.debug("on_task_done callback raised: %s", e)
```

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): on_task_done callback for partial results"`

---

## Task 7: DAG — task dependencies

**Objective:** Support `depends_on: ["task_id"]` in task dicts. Tasks with unresolved deps wait; resolved deps inject predecessor results into context. Opt-in via `delegation.dag.enabled: true`.

**Files:**
- New: `tools/delegate_dag.py`
- Modify: `tools/delegate_tool.py`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_dag.py
from tools.delegate_dag import topological_sort, resolve_deps

def test_topological_sort_no_deps():
    tasks = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    order = topological_sort(tasks)
    assert [t["id"] for t in order] == ["a", "b", "c"]

def test_topological_sort_with_deps():
    tasks = [
        {"id": "a"},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a"]},
        {"id": "d", "depends_on": ["b", "c"]},
    ]
    order = topological_sort(tasks)
    ids = [t["id"] for t in order]
    assert ids.index("a") < ids.index("b")
    assert ids.index("a") < ids.index("c")
    assert ids.index("b") < ids.index("d")
    assert ids.index("c") < ids.index("d")

def test_topological_sort_detects_cycle():
    tasks = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(ValueError, match="cycle"):
        topological_sort(tasks)

def test_resolve_deps_injects_predecessor_result():
    results = {"a": {"summary": "auth flow uses JWT"}}
    task = {"id": "b", "goal": "write tests", "depends_on": ["a"]}
    enriched = resolve_deps(task, results)
    assert "auth flow uses JWT" in enriched["context"]
```

**Step 2:** Run → FAIL

**Step 3:** Create `tools/delegate_dag.py`:

```python
"""DAG resolver for delegate_task — topological sort + dependency injection."""
from typing import Any, Dict, List


def topological_sort(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Kahn's algorithm. Raises ValueError on cycle."""
    id_to_task = {}
    for i, t in enumerate(tasks):
        tid = t.get("id") or str(i)
        id_to_task[tid] = t

    in_degree = {tid: 0 for tid in id_to_task}
    adj: Dict[str, List[str]] = {tid: [] for tid in id_to_task}

    for tid, task in id_to_task.items():
        for dep in (task.get("depends_on") or []):
            if dep not in id_to_task:
                raise ValueError(f"Task '{tid}' depends on unknown task '{dep}'")
            adj[dep].append(tid)
            in_degree[tid] += 1

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    sorted_ids = []
    while queue:
        node = queue.pop(0)
        sorted_ids.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_ids) != len(id_to_task):
        raise ValueError("Cycle detected in task dependency graph")

    return [id_to_task[tid] for tid in sorted_ids]


def resolve_deps(
    task: Dict[str, Any],
    completed_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Enrich task context with summaries from predecessor tasks.
    Returns a new task dict with updated context.
    """
    deps = task.get("depends_on") or []
    if not deps:
        return task

    predecessor_summaries = []
    for dep_id in deps:
        result = completed_results.get(dep_id)
        if result and result.get("summary"):
            predecessor_summaries.append(
                f"Result from task '{dep_id}':\n{result['summary']}"
            )

    if not predecessor_summaries:
        return task

    injected = "\n\n".join(predecessor_summaries)
    existing_context = task.get("context") or ""
    new_context = f"{existing_context}\n\n{injected}".strip() if existing_context else injected

    return {**task, "context": new_context}
```

In `delegate_tool.py`, when DAG enabled:
1. Call `topological_sort(task_list)` before execution
2. Run tasks in waves (group tasks whose deps are all satisfied)
3. Pass `completed_results` to `resolve_deps` before spawning each task

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): DAG task dependencies with topological sort and context injection"`
