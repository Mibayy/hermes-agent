"""DAG resolver for delegate_task -- topological sort and dependency injection."""
from collections import deque
from typing import Any, Dict, List, Optional


def topological_sort(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sort tasks respecting depends_on constraints using Kahn's algorithm.
    Each task may have an 'id' field (str) and an optional 'depends_on' list of ids.
    If 'id' is missing, the task's index (as string) is used.
    Raises ValueError if a cycle is detected or a dependency id is unknown.
    """
    indexed = []
    for i, t in enumerate(tasks):
        tid = str(t.get('id', i))
        indexed.append((tid, t))

    id_to_task = {tid: t for tid, t in indexed}
    if len(id_to_task) != len(indexed):
        raise ValueError('Duplicate task ids detected')

    in_degree: Dict[str, int] = {tid: 0 for tid in id_to_task}
    adj: Dict[str, List[str]] = {tid: [] for tid in id_to_task}

    for tid, task in id_to_task.items():
        for dep in (task.get('depends_on') or []):
            dep = str(dep)
            if dep not in id_to_task:
                raise ValueError(f"Task '{tid}' depends on unknown task '{dep}'")
            adj[dep].append(tid)
            in_degree[tid] += 1

    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
    sorted_ids: List[str] = []
    while queue:
        node = queue.popleft()
        sorted_ids.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_ids) != len(id_to_task):
        raise ValueError('Cycle detected in task dependency graph')

    return [id_to_task[tid] for tid in sorted_ids]


def resolve_deps(
    task: Dict[str, Any],
    completed_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Inject predecessor summaries into task context.
    completed_results: maps task_id (str) -> result dict with 'summary' field.
    Returns a new task dict (does not mutate original).
    """
    deps = task.get('depends_on') or []
    if not deps:
        return task

    summaries = []
    for dep_id in deps:
        result = completed_results.get(str(dep_id))
        if result and result.get('summary'):
            summaries.append(f"Result from task '{dep_id}':\n{result['summary']}")

    if not summaries:
        return task

    injected = '\n\n'.join(summaries)
    existing = task.get('context') or ''
    new_context = f"{existing}\n\n{injected}".strip() if existing else injected
    return {**task, 'context': new_context}
