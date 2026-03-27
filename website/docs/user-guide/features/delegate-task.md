# delegate_task

Spawn one or more subagent instances to work on tasks in isolated contexts.
Each subagent gets its own conversation, terminal session, and restricted toolset.
Only the final summary is returned -- intermediate tool calls never enter your context window.

## Quick start

Existing usage is unchanged. All new behavior requires explicit opt-in.

```json
// Single task (unchanged)
{
  "goal": "Write a Python script that parses CSV files",
  "context": "Use pandas. Output to stdout.",
  "toolsets": ["terminal", "file"]
}

// Batch (unchanged)
{
  "tasks": [
    {"goal": "Task A"},
    {"goal": "Task B"},
    {"goal": "Task C"}
  ]
}
```

## New parameters (v2)

### skills

Pass skill names to inject their content into the child's system prompt.

```json
{
  "tasks": [
    {
      "goal": "Implement the auth endpoint",
      "skills": ["test-driven-development", "nextjs-route-auth-cookies"]
    }
  ]
}
```

Skills are loaded from `skills/` in the project root and `~/.hermes/skills/`.
Missing skills are silently skipped.

### verify

Run a critic subagent after the generator to validate the result.
The critic reads the generator's summary and responds with `VERDICT: valid` or `VERDICT: invalid`.

```json
{
  "tasks": [
    {
      "goal": "Fix the memory leak in the event handler",
      "verify": true
    }
  ]
}
```

Result will include `verdict` ("valid" / "invalid" / "unknown") and `critic_summary`.

Enable globally via config:
```yaml
delegation:
  verify:
    enabled: true
    model: ""  # empty = inherit parent model
```

### depends_on (DAG)

Express task dependencies. Enable DAG mode in config, then use `id` and `depends_on` fields.

```json
{
  "tasks": [
    {"id": "schema", "goal": "Design the database schema"},
    {"id": "api", "goal": "Build the API", "depends_on": ["schema"]},
    {"id": "tests", "goal": "Write integration tests", "depends_on": ["api"]}
  ]
}
```

Tasks are topologically sorted before execution. Predecessor summaries are automatically
injected into dependent task context. Cycle detection raises an error immediately.

Enable via config:
```yaml
delegation:
  dag:
    enabled: true
```

### Retry

Retry failed tasks with failure context injected into subsequent attempts.

```yaml
delegation:
  retry:
    max_retries: 2
    inject_failure_context: true
```

On each retry, the previous error and partial summary are prepended to the task context
so the subagent can try a different approach.

### max_depth

Default is 1 (subagents cannot spawn further subagents).
Increase only when you need nested delegation.

```yaml
delegation:
  max_depth: 2
```

### memory_access

Controls whether subagents can access structured memory (requires #3093).

```yaml
delegation:
  memory_access: none       # default -- memory tool stripped
  # memory_access: read     -- read-only (graceful no-op without structured memory)
  # memory_access: read-write
```

### Blackboard

Shared key-value store visible to all siblings in a batch. Subagents can read
the current blackboard state from their system prompt.

```yaml
delegation:
  blackboard:
    enabled: true
```

### Checkpointing

Save subagent conversation state to SQLite every N iterations.
On crash, the parent can inspect the checkpoint to resume work.

```yaml
delegation:
  checkpoint:
    enabled: true
    interval_iterations: 10
    db_path: ""  # empty = ~/.hermes/state.db
```

### Observability

Detailed per-tool timing and status in the `tool_trace` field.

```yaml
delegation:
  observability:
    detailed_trace: true
```

When enabled, `tool_trace` entries include `duration_ms` (when timing is available)
and `status` ("ok" / "error") matched from tool result content.

## Full config reference

```yaml
delegation:
  max_depth: 1
  memory_access: none          # none | read | read-write
  checkpoint:
    enabled: false
    interval_iterations: 10
    db_path: ""
  retry:
    max_retries: 0
    inject_failure_context: true
  verify:
    enabled: false
    model: ""
  dag:
    enabled: false
  blackboard:
    enabled: false
  semantic_cache:
    enabled: false
  observability:
    detailed_trace: false
```

## Full example (all features)

```json
{
  "tasks": [
    {
      "id": "design",
      "goal": "Design the notification system schema",
      "skills": ["nextjs-supabase-notifications"],
      "verify": true
    },
    {
      "id": "impl",
      "goal": "Implement the notification system",
      "depends_on": ["design"],
      "skills": ["test-driven-development"],
      "verify": true
    }
  ]
}
```

Config:
```yaml
delegation:
  dag:
    enabled: true
  retry:
    max_retries: 1
  verify:
    enabled: false   # per-task verify above takes precedence
  blackboard:
    enabled: true
```

## Backward compatibility

All existing `delegate_task` calls work unchanged.
Every new feature requires explicit opt-in via config or task-level parameter.

## Relationship to other PRs

- Memory access modes depend on #3093 (graceful no-op when not merged)
- Skill loading references the skills directory maintained by #3294
