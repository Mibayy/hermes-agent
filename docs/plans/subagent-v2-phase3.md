# Subagent Architecture v2 — Phase 3: Quality & Resilience

---

## Task 8: Generator-critic loop

**Objective:** After a task completes, optionally spawn a second "critic" subagent that reviews the result and returns a confidence verdict. Activated per-task via `verify: true` or globally via `delegation.verify.enabled`.

**Files:**
- Modify: `tools/delegate_tool.py`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_verify.py
def test_verify_spawns_critic(monkeypatch, mock_parent_agent):
    critic_calls = []

    def fake_run_single_child(task_index, goal, child, parent_agent):
        critic_calls.append(goal)
        return {
            "task_index": task_index, "status": "completed",
            "summary": "VERDICT: valid. Logic is correct.",
            "api_calls": 1, "duration_seconds": 0.1,
        }

    monkeypatch.setattr("tools.delegate_tool._run_single_child", fake_run_single_child)
    # call _run_with_verify(generator_result, task, parent_agent, cfg)
    # assert len(critic_calls) == 1
    # assert "VERDICT" in critic_calls[0] or "review" in critic_calls[0].lower()

def test_verify_result_contains_confidence(monkeypatch, mock_parent_agent):
    # result should have verdict field
    pass

def test_verify_disabled_does_not_spawn_critic(monkeypatch, mock_parent_agent):
    # when verify=False, no extra subagent spawned
    pass
```

**Step 2:** Run → FAIL

**Step 3:**

```python
# In delegate_tool.py

_CRITIC_PROMPT_TEMPLATE = """\
You are a critical reviewer. A subagent was given the following goal:

GOAL: {goal}

It produced this result:
{summary}

Review the result carefully. Respond with:
VERDICT: valid   — if the result is correct and complete
VERDICT: invalid — if there are errors, missing cases, or logic flaws

Then explain your reasoning in 2-5 sentences. Be concise and specific.
If invalid, list the exact issues found.
"""

def _run_with_verify(
    generator_result: Dict[str, Any],
    task: Dict[str, Any],
    parent_agent,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Optionally run a critic subagent after the generator.
    Attaches verdict + critic_summary to the result dict.
    """
    verify_cfg = cfg.get("delegation", {}).get("verify", {})
    # Check task-level override first, then global config
    should_verify = task.get("verify", verify_cfg.get("enabled", False))

    if not should_verify or generator_result.get("status") != "completed":
        return generator_result

    summary = generator_result.get("summary", "")
    if not summary:
        return generator_result

    critic_goal = _CRITIC_PROMPT_TEMPLATE.format(
        goal=task["goal"],
        summary=summary[:4000],  # cap to avoid huge prompts
    )

    # Use dedicated critic model if configured
    critic_model = verify_cfg.get("model") or None

    critic_result = _run_single_child(
        task_index=generator_result["task_index"],
        goal=critic_goal,
        child=_build_child_agent(
            task_index=generator_result["task_index"],
            goal=critic_goal,
            context=None,
            toolsets=["terminal"],  # critic needs minimal tools
            model=critic_model,
            max_iterations=10,      # critic should be fast
            parent_agent=parent_agent,
        ),
        parent_agent=parent_agent,
    )

    critic_summary = critic_result.get("summary", "")
    verdict = "unknown"
    if "VERDICT: valid" in critic_summary:
        verdict = "valid"
    elif "VERDICT: invalid" in critic_summary:
        verdict = "invalid"

    return {
        **generator_result,
        "verdict": verdict,
        "critic_summary": critic_summary,
    }
```

In `_run_single_child` call sites: wrap result with `_run_with_verify(result, task, parent_agent, cfg)`.

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): generator-critic loop — verify:true on task spawns a critic subagent"`

---

## Task 9: Intelligent retry with failure context

**Objective:** When a task fails and `retry.max_retries > 0`, analyze the error and relaunch with injected failure context instead of a blind retry.

**Files:**
- Modify: `tools/delegate_tool.py`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_retry.py
def test_retry_injects_failure_context(monkeypatch, mock_parent_agent):
    call_count = [0]
    goals_seen = []

    def fake_run(task_index, goal, child, parent_agent):
        call_count[0] += 1
        goals_seen.append(goal)
        if call_count[0] == 1:
            return {"task_index": 0, "status": "failed",
                    "error": "timeout on line 42", "summary": None,
                    "api_calls": 5, "duration_seconds": 10}
        return {"task_index": 0, "status": "completed",
                "summary": "done", "api_calls": 3, "duration_seconds": 2}

    monkeypatch.setattr("tools.delegate_tool._run_single_child", fake_run)
    # call _run_with_retry(task, parent_agent, max_retries=1, inject_context=True, ...)
    # assert call_count[0] == 2
    # assert "timeout on line 42" in goals_seen[1] or "previous attempt" in goals_seen[1].lower()

def test_retry_zero_does_not_retry(monkeypatch, mock_parent_agent):
    # max_retries=0 → only one attempt even on failure
    pass

def test_retry_stops_after_max_retries(monkeypatch, mock_parent_agent):
    # fails max_retries+1 times → gives up, returns last error
    pass
```

**Step 2:** Run → FAIL

**Step 3:**

```python
_RETRY_CONTEXT_TEMPLATE = """\
PREVIOUS ATTEMPT FAILED.

Error: {error}

What the previous attempt did (summary): {summary}

Please try a different approach. Avoid repeating the same mistake.
"""

def _run_with_retry(
    task: Dict[str, Any],
    parent_agent,
    child_builder_kwargs: Dict[str, Any],
    max_retries: int = 0,
    inject_failure_context: bool = True,
) -> Dict[str, Any]:
    """Run a task with optional retry on failure."""
    attempts = max_retries + 1
    last_result = None
    extra_context = ""

    for attempt in range(attempts):
        # Inject failure context from previous attempt
        enriched_task = dict(task)
        if attempt > 0 and inject_failure_context and last_result:
            failure_ctx = _RETRY_CONTEXT_TEMPLATE.format(
                error=last_result.get("error", "unknown error"),
                summary=last_result.get("summary") or "no summary available",
            )
            existing = enriched_task.get("context") or ""
            enriched_task["context"] = f"{existing}\n\n{failure_ctx}".strip()

        child = _build_child_agent(
            goal=enriched_task["goal"],
            context=enriched_task.get("context"),
            **child_builder_kwargs,
        )
        result = _run_single_child(
            task_index=task.get("_task_index", 0),
            goal=enriched_task["goal"],
            child=child,
            parent_agent=parent_agent,
        )
        last_result = result

        if result.get("status") == "completed":
            if attempt > 0:
                result["retry_count"] = attempt
            return result

    # All attempts exhausted
    last_result["retry_count"] = max_retries
    return last_result
```

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): intelligent retry with failure context injection"`

---

## Task 10: Semantic deduplication via structured memory

**Objective:** Before spawning a task, search `sm_facts` for a recent result matching the goal keywords. If found and fresh, return cached result. Graceful no-op without #3093.

**Files:**
- Modify: `tools/delegate_tool.py`

**Step 1: Write failing tests**

```python
# tests/tools/test_delegate_dedup.py
def test_dedup_returns_cached_when_found(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool._SM_AVAILABLE", True)
    monkeypatch.setattr(
        "tools.delegate_tool._sm_search_goal",
        lambda goal, limit: [{"value": "cached result for auth flow", "created_at": "2026-01-01"}]
    )
    from tools.delegate_tool import _check_semantic_cache
    hit = _check_semantic_cache("analyse the auth flow")
    assert hit is not None
    assert "cached result" in hit

def test_dedup_returns_none_when_no_match(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool._SM_AVAILABLE", True)
    monkeypatch.setattr("tools.delegate_tool._sm_search_goal", lambda goal, limit: [])
    from tools.delegate_tool import _check_semantic_cache
    assert _check_semantic_cache("brand new task nobody has done") is None

def test_dedup_graceful_without_sm(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool._SM_AVAILABLE", False)
    from tools.delegate_tool import _check_semantic_cache
    assert _check_semantic_cache("anything") is None
```

**Step 2:** Run → FAIL

**Step 3:**

```python
def _sm_search_goal(goal: str, limit: int = 3) -> list:
    """Search structured memory for facts matching this goal. Returns [] if unavailable."""
    if not _SM_AVAILABLE:
        return []
    try:
        from tools.structured_memory.facts import search
        # Search using first 60 chars of goal as keywords
        keywords = goal[:60]
        return search(keywords, limit=limit)
    except Exception:
        return []

def _check_semantic_cache(goal: str) -> Optional[str]:
    """
    Look for a recent cached result for a similar goal in sm_facts.
    Returns the cached summary string, or None if no hit.
    """
    hits = _sm_search_goal(goal, limit=3)
    if not hits:
        return None
    # Return the most recent hit's value
    return hits[0].get("value")
```

In `delegate_task()`, before spawning each task:
```python
if cfg.get("delegation", {}).get("semantic_cache", {}).get("enabled", False):
    cached = _check_semantic_cache(task["goal"])
    if cached:
        results.append({
            "task_index": i, "status": "completed",
            "summary": cached, "api_calls": 0,
            "duration_seconds": 0, "cache_hit": True,
        })
        continue
```

**Step 4:** Run → PASS

**Step 5:** `git commit -m "feat(delegate): semantic deduplication via structured memory cache (opt-in, no-op without #3093)"`
