"""
Fact CRUD, contradiction detection, and classification.

A fact has the form:
    TYPE[target]: content
    e.g.  C[db.id]: UUID mndtry, nvr autoincrement

Types map to short codes stored in the DB:
    C -> C   (constraint)
    D -> D   (decision)
    V -> V   (value)
    ? -> ?   (unknown)
    ✓ -> done
    ~ -> obs  (obsolete)
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from typing import Optional

from .constants import (
    FACT_RE,
    GAUGE_FULL,
    MAX_FACT_CHARS,
    TYPE_DISPLAY,
    TYPE_MAP,
)
from .db import sm_now as now


class MemoryFullError(RuntimeError):
    """Raised when the active memory store is at or above GAUGE_FULL and cannot accept new facts."""


class FactTooLargeError(ValueError):
    """Raised when a single fact's content exceeds MAX_FACT_CHARS after truncation attempts."""


# Backwards-compat aliases
_TYPE_MAP = TYPE_MAP


def parse_notation(raw: str) -> tuple[str, str, str]:
    """
    Parse raw notation string into (type_code, target, content).
    Raises ValueError if the format does not match.
    """
    m = FACT_RE.match(raw.strip())
    if not m:
        raise ValueError(
            f"Invalid fact notation: {raw!r}\n"
            "Expected format: TYPE[target]: content  (e.g. C[db.id]: UUID mndtry)"
        )
    raw_type = m.group("type")
    type_code = TYPE_MAP.get(raw_type, raw_type)
    return type_code, m.group("target").strip(), m.group("content").strip()


def _source_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def write(
    conn:       sqlite3.Connection,
    raw:        str,
    scope_id:   Optional[str] = None,
) -> dict:
    """
    Parse, dedup-check, contradiction-check, then insert a fact.

    Returns a dict with:
        id               new fact id
        status           'created' | 'dedup'
        conflict_resolved  id of superseded fact if a contradiction was found
        gauge_pct        current gauge after write

    Raises:
        MemoryFullError   if the store is at or above GAUGE_FULL
        FactTooLargeError if content exceeds MAX_FACT_CHARS even after truncation
        ValueError        if notation is invalid
    """
    current_pct = _gauge_pct(conn)
    if current_pct >= GAUGE_FULL:
        raise MemoryFullError(
            f"Memory store is full ({current_pct:.0f}%). "
            "Run memory_purge to reclaim space, or ask the user to /compress."
        )

    type_code, target, content = parse_notation(raw)

    truncated = False
    if len(content) > MAX_FACT_CHARS:
        content = content[:MAX_FACT_CHARS - 1] + "…"
        raw = f"{next(k for k,v in TYPE_MAP.items() if v == type_code)}[{target}]: {content}"
        truncated = True

    shash = _source_hash(raw)

    # 1. Dedup: exact same source hash already active?
    existing = conn.execute(
        "SELECT id FROM sm_facts WHERE source_hash = ? AND status = 'active'",
        (shash,),
    ).fetchone()
    if existing:
        return {
            "id":               existing["id"],
            "status":           "dedup",
            "conflict_resolved": None,
            "truncated":        False,
            "gauge_pct":        _gauge_pct(conn),
        }

    # 2. Contradiction detection
    conflict_id = None
    if scope_id:
        conflict = conn.execute(
            """
            SELECT id FROM sm_facts
            WHERE target = ? AND type = ? AND scope_id = ? AND status = 'active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (target, type_code, scope_id),
        ).fetchone()
    else:
        conflict = conn.execute(
            """
            SELECT id FROM sm_facts
            WHERE target = ? AND type = ? AND scope_id IS NULL AND status = 'active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (target, type_code),
        ).fetchone()

    new_id = str(uuid.uuid4())
    ts = now()

    conn.execute(
        """
        INSERT INTO sm_facts
            (id, content, type, target, scope_id, status,
             superseded_by, created_at, updated_at, last_accessed,
             access_count, source_hash)
        VALUES (?,?,?,?,?,'active',NULL,?,?,NULL,0,?)
        """,
        (new_id, content, type_code, target, scope_id, ts, ts, shash),
    )

    if conflict:
        conflict_id = conflict["id"]
        conn.execute(
            "UPDATE sm_facts SET status='superseded', superseded_by=?, updated_at=? WHERE id=?",
            (new_id, ts, conflict_id),
        )

    conn.commit()

    return {
        "id":                new_id,
        "status":            "created",
        "conflict_resolved": conflict_id,
        "truncated":         truncated,
        "gauge_pct":         _gauge_pct(conn),
    }


def search(
    conn:     sqlite3.Connection,
    query:    str,
    scope_id: Optional[str] = None,
    limit:    int = 5,
) -> list[dict]:
    """
    FTS5 full-text search over active + cold facts.
    Superseded and archived facts are excluded.
    Updates last_accessed and access_count on matched rows.
    Returns up to `limit` results sorted by FTS5 rank.
    """
    import re
    limit = min(limit, 20)

    tokens = query.split()
    if not tokens:
        return []

    def _fts_token(tok: str) -> str:
        escaped = tok.replace('"', '""')
        if re.search(r'[^A-Za-z0-9_]', tok):
            return f'"{escaped}"'
        return f"{escaped}*"

    if len(tokens) == 1:
        fts_query = _fts_token(tokens[0])
    else:
        fts_query = " AND ".join(_fts_token(t) for t in tokens)

    scope_filter = ""
    params: list = [fts_query, limit]
    if scope_id:
        scope_filter = "AND f.scope_id = ?"
        params.insert(1, scope_id)

    rows = conn.execute(
        f"""
        SELECT f.id, f.content, f.type, f.target,
               f.scope_id, f.status, f.updated_at, rank
        FROM sm_facts_fts
        JOIN sm_facts f ON sm_facts_fts.rowid = f.rowid
        WHERE sm_facts_fts MATCH ?
          AND f.status IN ('active','cold')
          {scope_filter}
        ORDER BY rank
        LIMIT ?
        """,
        params,
    ).fetchall()

    ids = [r["id"] for r in rows]
    if ids:
        ts = now()
        conn.execute(
            f"""
            UPDATE sm_facts SET last_accessed=?, access_count=access_count+1
            WHERE id IN ({','.join('?'*len(ids))})
            """,
            [ts, *ids],
        )
        conn.commit()

    return [dict(r) for r in rows]


def get_hot(conn: sqlite3.Connection) -> list[dict]:
    """Return all active facts in active scopes (for session injection)."""
    rows = conn.execute("SELECT * FROM sm_hot_facts").fetchall()
    return [dict(r) for r in rows]


def purge(
    conn:             sqlite3.Connection,
    scope_id:         Optional[str] = None,
    older_than_days:  Optional[int] = None,
) -> int:
    """
    Hard-delete superseded / archived facts matching the given filters.
    Returns count of deleted rows.
    """
    conditions = ["status IN ('superseded','archived')"]
    params: list = []

    if scope_id:
        conditions.append("scope_id = ?")
        params.append(scope_id)

    if older_than_days is not None:
        cutoff = now() - older_than_days * 86_400
        conditions.append("updated_at < ?")
        params.append(cutoff)
        conditions.append("(last_accessed IS NULL OR last_accessed < ?)")
        params.append(cutoff)

    where = " AND ".join(conditions)
    cur = conn.execute(f"DELETE FROM sm_facts WHERE {where}", params)
    conn.commit()
    return cur.rowcount


def _gauge_pct(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT pct FROM sm_gauge").fetchone()
    return float(row["pct"]) if row else 0.0
