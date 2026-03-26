"""
Pressure levels and tiered responses to gauge thresholds.

GAUGE_MERGE    (70%) : merge duplicate facts sharing target + scope
GAUGE_ARCHIVE  (85%) : push cold-scope facts from cold -> archived
GAUGE_SYNTHESIS (95%): last resort consolidation (optional LLM call)
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from .constants import (
    GAUGE_ARCHIVE,
    GAUGE_MERGE,
    GAUGE_SYNTHESIS,
    MAX_ACTIVE_CHARS,
)
from .db import sm_now as now


def read(conn: sqlite3.Connection) -> dict:
    """Return current gauge state."""
    row = conn.execute("SELECT used_chars, max_chars, pct FROM sm_gauge").fetchone()
    if not row:
        return {"used_chars": 0, "max_chars": MAX_ACTIVE_CHARS, "pct": 0.0}
    return dict(row)


def check_and_act(
    conn:          sqlite3.Connection,
    llm_call:      Optional[Callable[[str], str]] = None,
) -> dict:
    """
    Read gauge and trigger the appropriate pressure level.
    llm_call is optional; if None the synthesis step is skipped.

    Returns dict with gauge state + actions taken.
    """
    g = read(conn)
    pct = g["pct"]
    actions: list[str] = []

    if pct < GAUGE_MERGE:
        return {**g, "actions": actions}

    merged = _merge_duplicates(conn)
    if merged:
        actions.append(f"merged {merged} duplicate(s)")
    g = read(conn)

    if g["pct"] >= GAUGE_ARCHIVE:
        archived = _archive_cold_scopes(conn)
        if archived:
            actions.append(f"archived {archived} cold-scope fact(s)")
        g = read(conn)

    if g["pct"] >= GAUGE_SYNTHESIS:
        if llm_call:
            synthesized = _force_synthesis(conn, llm_call)
            actions.append(f"synthesis: {synthesized} fact(s) consolidated")
        else:
            pushed = _push_oldest_to_cold(conn, target_pct=85.0)
            actions.append(f"pushed {pushed} oldest fact(s) to cold (no LLM)")
        g = read(conn)

    return {**g, "actions": actions}


def _merge_duplicates(conn: sqlite3.Connection) -> int:
    """
    Find active facts sharing the same (target, scope_id) and merge them.
    Keeps the most recently updated, sets older ones to superseded.
    Returns count of facts superseded.
    """
    rows = conn.execute(
        """
        SELECT type, target, scope_id, COUNT(*) AS cnt
        FROM sm_facts
        WHERE status = 'active' AND type NOT IN ('done','obs')
        GROUP BY type, target, scope_id
        HAVING cnt > 1
        """
    ).fetchall()

    superseded = 0
    ts = now()
    for row in rows:
        fact_type, target, scope_id = row["type"], row["target"], row["scope_id"]
        duplicates = conn.execute(
            """
            SELECT id FROM sm_facts
            WHERE type=? AND target=? AND scope_id IS ? AND status='active'
            ORDER BY updated_at DESC
            """,
            (fact_type, target, scope_id),
        ).fetchall()
        keeper_id = duplicates[0]["id"]
        for dup in duplicates[1:]:
            conn.execute(
                "UPDATE sm_facts SET status='superseded', superseded_by=?, updated_at=? WHERE id=?",
                (keeper_id, ts, dup["id"]),
            )
            superseded += 1

    conn.commit()
    return superseded


def _archive_cold_scopes(conn: sqlite3.Connection) -> int:
    """
    Push facts that are already cold (scope closed) into archived.
    Facts accessed within the last 24h are spared.
    Returns count of facts archived.
    """
    ts      = now()
    cutoff  = ts - 86_400   # 24h grace window
    cur = conn.execute(
        """
        UPDATE sm_facts SET status='archived', updated_at=?
        WHERE status='cold'
          AND scope_id IN (SELECT id FROM sm_scopes WHERE status IN ('cold','closed'))
          AND (last_accessed IS NULL OR last_accessed < ?)
        """,
        (ts, cutoff),
    )
    conn.commit()
    return cur.rowcount


def _push_oldest_to_cold(conn: sqlite3.Connection, target_pct: float) -> int:
    """
    Emergency fallback when synthesis is unavailable.
    """
    g = read(conn)
    if g["pct"] < target_pct or g["used_chars"] == 0:
        return 0

    max_chars     = g["max_chars"]
    target_chars  = int(max_chars * target_pct / 100)
    excess_chars  = g["used_chars"] - target_chars

    avg_row = conn.execute(
        "SELECT AVG(LENGTH(content)) AS avg FROM sm_facts WHERE status='active'"
    ).fetchone()
    avg_len = float(avg_row["avg"] or 50)
    batch   = max(1, int(excess_chars / avg_len) + 1)

    rows = conn.execute(
        """
        SELECT id FROM sm_facts WHERE status='active'
        ORDER BY COALESCE(last_accessed, created_at) ASC
        LIMIT ?
        """,
        (batch,),
    ).fetchall()

    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    ts  = now()
    conn.execute(
        f"UPDATE sm_facts SET status='cold', updated_at=? WHERE id IN ({','.join('?'*len(ids))})",
        [ts, *ids],
    )
    conn.commit()
    return len(ids)


def _force_synthesis(
    conn:     sqlite3.Connection,
    llm_call: Callable[[str], str],
) -> int:
    """
    Last-resort consolidation via LLM.
    Returns count of facts consolidated (archived).
    """
    active = conn.execute(
        "SELECT id, content, type, target, scope_id FROM sm_facts WHERE status='active'"
    ).fetchall()

    if not active:
        return 0

    _code_to_sym = {"C": "C", "D": "D", "V": "V", "?": "?", "done": "✓", "obs": "~"}

    lines = [f"{_code_to_sym.get(r['type'], r['type'])}[{r['target']}]: {r['content']}" for r in active]
    prompt = (
        "You are a memory consolidation engine.\n"
        "Consolidate the following facts into the smallest possible set "
        "using MEMORY_SPEC notation (C/D/V/?/✓/~)[target]: content.\n"
        "Merge facts about the same target. Remove redundancy. "
        "Keep every unique constraint, decision, and value.\n\n"
        + "\n".join(lines)
    )

    try:
        response = llm_call(prompt)
    except Exception:
        return 0

    from .facts import parse_notation
    import uuid

    ts = now()

    new_rows = []
    for line in response.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            type_code, target, content = parse_notation(line)
            new_rows.append((str(uuid.uuid4()), content, type_code, target, None, ts, ts))
        except ValueError:
            continue

    if not new_rows:
        return 0

    for row in new_rows:
        conn.execute(
            """
            INSERT INTO sm_facts
                (id, content, type, target, scope_id, status,
                 superseded_by, created_at, updated_at,
                 last_accessed, access_count, source_hash)
            VALUES (?,?,?,?,?,'active',NULL,?,?,NULL,0,NULL)
            """,
            row,
        )

    ids = [r["id"] for r in active]
    conn.execute(
        f"UPDATE sm_facts SET status='archived', updated_at=? WHERE id IN ({','.join('?'*len(ids))})",
        [ts, *ids],
    )

    conn.commit()
    return len(ids)
