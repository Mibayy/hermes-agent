# Subagent Architecture v2 — Phase 1: Foundations

> **Context:** This plan implements the subagent v2 feature set for `tools/delegate_tool.py` and related files.
> Depends on: #3093 (structured memory, graceful no-op if absent), #3294 (codebase-index docs/skill).
> All new features are opt-in / backward-compatible.

**Goal:** Transform delegate_task from a simple fork into a collaborative multi-agent orchestrator with memory access, skill inheritance, task dependencies, generator-critic verification, intelligent retry, shared blackboard, structured observability, and opt-in checkpointing.

**Architecture overview:**
- `tools/delegate_tool.py` — core changes (all features land here + helpers)
- `tools/delegate_checkpoint.py` — new file, checkpointing logic
- `tools/delegate_dag.py` — new file, DAG/dependency resolver
- `config.yaml` delegation section extended
- `tests/tools/test_delegate_*.py` — test files per feature group

**Tech stack:** Python stdlib (threading, json, sqlite3), existing AIAgent, existing sm_facts API from #3093.

---

## Task 1: Extend delegation config schema

**Objective:** Add all new config keys under `delegation:` so every feature has a documented on/off switch.

**Files:**
- Modify: `hermes_cli/config.py` (delegation defaults section)

**What to add:**

```python
# In the delegation defaults dict (around line 338)
"delegation": {
    # existing keys preserved
    "provider": "",
    "model": "",
    "base_url": "",
    "max_iterations": 50,
    # NEW
    "max_depth": 1,             # max recursion depth (was hardcoded to 1)
    "memory_access": "none",    # none | read | read-write
    "checkpoint": {
        "enabled": False,
        "interval_iterations": 10,  # save every N iterations
        "db_path": "",              # empty = use state.db
    },
    "retry": {
        "max_retries": 0,           # 0 = disabled
        "inject_failure_context": True,
    },
    "verify": {
        "enabled": False,           # generator-critic loop
        "model": "",                # separate model for critic (optional)
    },
    "dag": {
        "enabled": False,           # task dependency resolution
    },
    "blackboard": {
        "enabled": False,           # shared dict between siblings
    },
    "observability": {
        "detailed_trace": False,    # per-tool timing + token breakdown
    },
}
```

**Step 1: Write failing test**

```python
# tests/tools/test_delegate_config.py
def test_delegation_config_has_new_keys():
    from hermes_cli.config import load_config
    cfg = load_config()
    d = cfg.get("delegation", {})
    assert "max_depth" in d
    assert "memory_access" in d
    assert d["memory_access"] == "none"
    assert "checkpoint" in d
    assert d["checkpoint"]["enabled"] is False
    assert "retry" in d
    assert d["retry"]["max_retries"] == 0
    assert "verify" in d
    assert "dag" in d
    assert "blackboard" in d
    assert "observability" in d
```

**Step 2:** `pytest tests/tools/test_delegate_config.py -v` → FAIL

**Step 3:** Apply the config changes above.

**Step 4:** `pytest tests/tools/test_delegate_config.py -v` → PASS

**Step 5:** `git commit -m "feat(delegate): extend delegation config schema for v2 features"`

---

## Task 2: Configurable max_depth

**Objective:** Replace `MAX_DEPTH = 1` hardcoded constant with value from config.

**Files:**
- Modify: `tools/delegate_tool.py` (top of file + `delegate_task` body)

**Step 1: Write failing test**

```python
# tests/tools/test_delegate_depth.py
def test_max_depth_read_from_config(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool._load_config",
        lambda: {"delegation": {"max_depth": 2, "max_iterations": 50}})
    from tools.delegate_tool import _get_max_depth
    assert _get_max_depth() == 2

def test_max_depth_default_is_1(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool._load_config", lambda: {})
    from tools.delegate_tool import _get_max_depth
    assert _get_max_depth() == 1
```

**Step 2:** Run → FAIL

**Step 3:**

```python
# In delegate_tool.py — replace MAX_DEPTH constant with:
def _get_max_depth() -> int:
    cfg = _load_config()
    return cfg.get("delegation", {}).get("max_depth", 1)

# In delegate_task(), replace:
#   if depth >= MAX_DEPTH:
# with:
#   if depth >= _get_max_depth():
```

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): configurable max_depth via delegation.max_depth"`

---

## Task 3: Memory access mode for subagents

**Objective:** Replace `skip_memory=True` binary flag with `memory_mode: none | read | read-write`. In `read` mode, subagent gets hot facts injected + can call search/reflect. In `read-write` mode, full access. Graceful no-op if #3093 not present.

**Files:**
- Modify: `tools/delegate_tool.py` — `_strip_blocked_tools`, `_build_child_agent`
- Modify: `run_agent.py` — `AIAgent.__init__` to accept `memory_mode` instead of `skip_memory`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_memory.py
def test_memory_none_strips_memory_toolset():
    from tools.delegate_tool import _compute_child_toolsets
    result = _compute_child_toolsets(["terminal", "memory"], memory_mode="none")
    assert "memory" not in result

def test_memory_read_keeps_memory_toolset():
    from tools.delegate_tool import _compute_child_toolsets
    result = _compute_child_toolsets(["terminal", "memory"], memory_mode="read")
    assert "memory" in result

def test_memory_read_write_keeps_memory_toolset():
    from tools.delegate_tool import _compute_child_toolsets
    result = _compute_child_toolsets(["terminal", "memory"], memory_mode="read-write")
    assert "memory" in result

def test_memory_mode_graceful_if_sm_unavailable(monkeypatch):
    """If structured_memory module not present, read mode silently degrades to none."""
    monkeypatch.setattr("tools.delegate_tool._SM_AVAILABLE", False)
    from tools.delegate_tool import _compute_child_toolsets
    result = _compute_child_toolsets(["terminal", "memory"], memory_mode="read")
    assert "memory" not in result
```

**Step 2:** Run → FAIL

**Step 3:**

```python
# In delegate_tool.py

# Detect if #3093 structured memory is available
try:
    from tools.structured_memory import db as _sm_db  # noqa: F401
    _SM_AVAILABLE = True
except ImportError:
    _SM_AVAILABLE = False

_ALWAYS_BLOCKED = {"delegation", "clarify", "code_execution"}
# memory is now conditional — NOT in this set

def _compute_child_toolsets(
    toolsets: List[str],
    memory_mode: str = "none",
) -> List[str]:
    """
    Filter toolsets for a child agent based on memory_mode.
    - none: strip memory (legacy behavior)
    - read / read-write: keep memory only if _SM_AVAILABLE
    """
    blocked = set(_ALWAYS_BLOCKED)
    if memory_mode == "none" or not _SM_AVAILABLE:
        blocked.add("memory")
    return [t for t in toolsets if t not in blocked]
```

Then update `_build_child_agent` to:
1. Accept `memory_mode: str = "none"` parameter
2. Call `_compute_child_toolsets(child_toolsets, memory_mode)` instead of `_strip_blocked_tools`
3. Pass `skip_memory=(memory_mode == "none")` to AIAgent (or `memory_mode=memory_mode` once AIAgent supports it)

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): memory_mode for subagents (none|read|read-write), graceful no-op without #3093"`

---

## Task 4: Skill inheritance for subagents

**Objective:** Allow passing `skills: ["skill-name"]` in a task dict. Named skills are loaded and injected into the child's system prompt.

**Files:**
- Modify: `tools/delegate_tool.py` — `_build_child_system_prompt`, `_build_child_agent`

**Step 1: Write failing test**

```python
# tests/tools/test_delegate_skills.py
def test_skill_content_injected_in_child_prompt(monkeypatch):
    monkeypatch.setattr(
        "tools.delegate_tool._load_skill_content",
        lambda name: f"SKILL:{name}:content"
    )
    from tools.delegate_tool import _build_child_system_prompt
    prompt = _build_child_system_prompt(
        goal="do something",
        context=None,
        skills=["github-code-review"]
    )
    assert "SKILL:github-code-review:content" in prompt

def test_missing_skill_does_not_crash(monkeypatch):
    monkeypatch.setattr(
        "tools.delegate_tool._load_skill_content",
        lambda name: None  # skill not found
    )
    from tools.delegate_tool import _build_child_system_prompt
    prompt = _build_child_system_prompt("goal", None, skills=["nonexistent"])
    assert "nonexistent" not in prompt  # silently skipped
```

**Step 2:** Run → FAIL

**Step 3:**

```python
def _load_skill_content(name: str) -> Optional[str]:
    """Load a skill's SKILL.md content by name. Returns None if not found."""
    import subprocess, shutil
    # Try hermes skill view mechanism or direct file lookup
    skills_dirs = [
        Path(__file__).parent.parent / "skills",
        Path.home() / ".hermes" / "skills",
    ]
    for base in skills_dirs:
        for skill_md in base.rglob(f"{name}/SKILL.md"):
            try:
                return skill_md.read_text()
            except OSError:
                pass
    return None

def _build_child_system_prompt(
    goal: str,
    context: Optional[str],
    skills: Optional[List[str]] = None,
) -> str:
    parts = [f"You are a focused subagent. Your goal: {goal}"]
    if context:
        parts.append(f"\nContext:\n{context}")
    if skills:
        loaded = []
        for s in skills:
            content = _load_skill_content(s)
            if content:
                loaded.append(f"\n--- Skill: {s} ---\n{content}")
        if loaded:
            parts.append("\nLoaded skills:" + "".join(loaded))
    return "\n".join(parts)
```

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): skill inheritance — pass skills=[] in task dict to inject skill content in child prompt"`
