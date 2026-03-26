"""
Scope lifecycle management.

A scope represents a unit of work: a feature, a phase, a bug fix.
It opens implicitly on first fact write and closes either:
  - explicitly via a closing signal in the message text
  - automatically after SCOPE_COOL_TURNS turns of silence
  - automatically when topic shift is detected (no shared targets)
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from typing import Optional

from .constants import SCOPE_COOL_TURNS
from .db import sm_now as now

# Patterns that signal a scope should be closed.
_CLOSING_SIGNALS = re.compile(
    r"(?<!not )(?<!isn't )(?<!isn't )(?<!haven't )(?<!hasn't )(?<!never )"
    r"(?<!n't )"
    r"\b("
    r"merged|deployed|shipped|finished|completed|closed|"
    r"fixed|resolved|released|it works|working now|"
    r"phase \w+ (done|complete|finished|over)"
    r")\b",
    re.IGNORECASE,
)


def get_or_create(
    conn:    sqlite3.Connection,
    label:   str,
    session_id: Optional[str] = None,
) -> str:
    """
    Return existing active scope id by label, or create a new one.
    Updates session.active_scope_id if session_id provided.
    """
    ts = now()

    row = conn.execute(
        "SELECT id FROM sm_scopes WHERE label=? AND status='active'",
        (label,),
    ).fetchone()

    if row:
        scope_id = row["id"]
        conn.execute(
            "UPDATE sm_scopes SET last_referenced=? WHERE id=?",
            (ts, scope_id),
        )
    else:
        old = conn.execute(
            "SELECT id FROM sm_scopes WHERE label=? ORDER BY created_at DESC LIMIT 1",
            (label,),
        ).fetchone()

        if old:
            scope_id = old["id"]
            conn.execute(
                """
                UPDATE sm_scopes
                SET status='active', closed_at=NULL, last_referenced=?, current_turn=0
                WHERE id=?
                """,
                (ts, scope_id),
            )
        else:
            scope_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO sm_scopes (id, label, status, last_referenced, current_turn, created_at)
                VALUES (?, ?, 'active', ?, 0, ?)
                """,
                (scope_id, label, ts, ts),
            )

    if session_id:
        conn.execute(
            "UPDATE sm_sessions SET active_scope_id=? WHERE id=?",
            (scope_id, session_id),
        )

    conn.commit()
    return scope_id


def get_active(conn: sqlite3.Connection) -> list[dict]:
    """All currently active scopes."""
    rows = conn.execute(
        "SELECT * FROM sm_scopes WHERE status='active' ORDER BY last_referenced DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def tick(
    conn:         sqlite3.Connection,
    turn:         int,
    message_text: str = "",
    session_id:   Optional[str] = None,
) -> list[str]:
    """
    Called on every incoming user message.
    Returns list of scope ids that were cooled this tick.
    """
    if session_id:
        conn.execute(
            "UPDATE sm_sessions SET last_turn=? WHERE id=?",
            (turn, session_id),
        )
    else:
        conn.execute("UPDATE sm_sessions SET last_turn=?", (turn,))
    conn.commit()

    cooled: list[str] = []
    active_scopes = get_active(conn)

    recent_targets = _recent_write_targets(conn, n_turns=3)

    for scope in active_scopes:
        scope_id = scope["id"]
        distance = turn - scope["current_turn"]

        if message_text and _CLOSING_SIGNALS.search(message_text):
            if session_id:
                row = conn.execute(
                    "SELECT active_scope_id FROM sm_sessions WHERE id=?",
                    (session_id,),
                ).fetchone()
                if row and row["active_scope_id"] == scope_id:
                    _cool_scope(conn, scope_id)
                    cooled.append(scope_id)
                    continue
            else:
                _cool_scope(conn, scope_id)
                cooled.append(scope_id)
                continue

        if distance >= SCOPE_COOL_TURNS:
            _cool_scope(conn, scope_id)
            cooled.append(scope_id)
            continue

        if distance >= 3:
            scope_targets = _scope_targets(conn, scope_id)
            if scope_targets and not scope_targets.intersection(recent_targets):
                _cool_scope(conn, scope_id)
                cooled.append(scope_id)

    return cooled


def _cool_scope(conn: sqlite3.Connection, scope_id: str) -> None:
    """Move scope to cold and push its active facts to cold as well."""
    ts = now()
    conn.execute(
        "UPDATE sm_scopes SET status='cold', closed_at=? WHERE id=?",
        (ts, scope_id),
    )
    conn.execute(
        "UPDATE sm_facts SET status='cold', updated_at=? WHERE scope_id=? AND status='active'",
        (ts, scope_id),
    )
    conn.commit()


def touch(conn: sqlite3.Connection, scope_id: str, turn: int) -> None:
    """Record that this scope was active at `turn`."""
    conn.execute(
        "UPDATE sm_scopes SET current_turn=?, last_referenced=? WHERE id=?",
        (turn, now(), scope_id),
    )
    conn.commit()


def close(conn: sqlite3.Connection, scope_id: str) -> None:
    """Explicitly close a scope."""
    ts = now()
    conn.execute(
        "UPDATE sm_scopes SET status='closed', closed_at=? WHERE id=?",
        (ts, scope_id),
    )
    conn.execute(
        "UPDATE sm_facts SET status='cold', updated_at=? WHERE scope_id=? AND status='active'",
        (ts, scope_id),
    )
    conn.commit()


def _recent_write_targets(conn: sqlite3.Connection, n_turns: int = 3) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT target FROM sm_facts
        WHERE status IN ('active','cold')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (n_turns * 5,),
    ).fetchall()
    return {r["target"] for r in rows}


def _scope_targets(conn: sqlite3.Connection, scope_id: str) -> set[str]:
    """All targets of active facts belonging to this scope."""
    rows = conn.execute(
        "SELECT DISTINCT target FROM sm_facts WHERE scope_id=? AND status='active'",
        (scope_id,),
    ).fetchall()
    return {r["target"] for r in rows}
