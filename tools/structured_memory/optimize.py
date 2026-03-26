"""
memory_optimize — compress MEMORY.md / USER.md and migrate C/D/V facts to DB.

Strategy (applied in order):
1. Detect C/D/V lines in each file -> migrate to structured memory DB, remove from file
2. Abbreviate remaining entries using the compression map
3. Strip redundant whitespace and filler phrases

Thresholds:
    MEMORY_THRESHOLD  = 55%  of 2200 chars -> trigger
    USER_THRESHOLD    = 55%  of 1375 chars -> trigger
    TARGET            = 45%
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

from .constants import COMPRESS_MAP, FACT_RE

# ---------------------------------------------------------------- paths ----

try:
    from hermes_constants import get_hermes_home
    HERMES_HOME = get_hermes_home()
except ImportError:
    HERMES_HOME = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))

MEMORY_PATH  = HERMES_HOME / "memories" / "MEMORY.md"
USER_PATH    = HERMES_HOME / "memories" / "USER.md"

MEMORY_LIMIT  = 2200
USER_LIMIT    = 1375
THRESHOLD_PCT = 55   # trigger above this %
TARGET_PCT    = 45   # aim for this % after compression

# ---------------------------------------------------------------- helpers --

_FACT_LINE_RE = re.compile(
    r"^([C?✓~]|D|V)\[([^\]]+)\]:\s*(.+)$", re.MULTILINE
)


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pct(text: str, limit: int) -> float:
    return len(text) / limit * 100


def _compress_text(text: str) -> str:
    """Apply abbreviation map to a block of text."""
    result = text
    for pattern, replacement in COMPRESS_MAP:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r" {2,}", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _extract_and_migrate(
    text: str,
    conn: "sqlite3.Connection",
    session_id: str,
) -> tuple[str, list[str]]:
    """
    Find C/D/V/? lines, write them to structured memory DB, remove from text.
    Returns (cleaned_text, list_of_migrated_notations).
    """
    from . import facts  # lazy import to avoid circular

    migrated: list[str] = []
    lines_out: list[str] = []

    for line in text.splitlines():
        m = _FACT_LINE_RE.match(line.strip())
        if m:
            notation = line.strip()
            try:
                facts.write(conn, notation, scope_id=None)
                migrated.append(notation)
                continue          # remove from file
            except Exception:
                pass              # keep the line if write fails
        lines_out.append(line)

    cleaned = "\n".join(lines_out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, migrated


# ----------------------------------------------------------------- public --

def optimize(
    conn: "sqlite3.Connection",
    session_id: str,
    *,
    threshold_pct: float = THRESHOLD_PCT,
    target_pct: float    = TARGET_PCT,
    dry_run: bool        = False,
) -> dict:
    """
    Check MEMORY.md and USER.md pressure, compress and migrate if needed.

    Returns a result dict with memory_before, memory_after, user_before,
    user_after, memory_migrated, user_migrated, action_taken.
    """
    memory_text = _read(MEMORY_PATH)
    user_text   = _read(USER_PATH)

    mem_before  = _pct(memory_text, MEMORY_LIMIT)
    user_before = _pct(user_text,   USER_LIMIT)

    needs_memory = mem_before  > threshold_pct
    needs_user   = user_before > threshold_pct

    if not needs_memory and not needs_user:
        return {
            "memory_before":   round(mem_before,  1),
            "memory_after":    round(mem_before,  1),
            "user_before":     round(user_before, 1),
            "user_after":      round(user_before, 1),
            "memory_migrated": 0,
            "user_migrated":   0,
            "action_taken":    False,
        }

    mem_migrated  = 0
    user_migrated = 0
    new_memory    = memory_text
    new_user      = user_text

    if needs_memory:
        new_memory, migrated = _extract_and_migrate(new_memory, conn, session_id)
        mem_migrated = len(migrated)
        new_memory = _compress_text(new_memory)

    if needs_user:
        new_user, migrated = _extract_and_migrate(new_user, conn, session_id)
        user_migrated = len(migrated)
        new_user = _compress_text(new_user)

    mem_after  = _pct(new_memory, MEMORY_LIMIT)
    user_after = _pct(new_user,   USER_LIMIT)

    if not dry_run:
        if needs_memory:
            _write(MEMORY_PATH, new_memory)
        if needs_user:
            _write(USER_PATH, new_user)

    return {
        "memory_before":   round(mem_before,  1),
        "memory_after":    round(mem_after,   1),
        "user_before":     round(user_before, 1),
        "user_after":      round(user_after,  1),
        "memory_migrated": mem_migrated,
        "user_migrated":   user_migrated,
        "action_taken":    True,
    }
