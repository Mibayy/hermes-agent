"""
SQLite connection helper and schema for structured memory.
Tables live in the same state.db used by hermes_state.SessionDB.
Zero external dependencies beyond stdlib sqlite3.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from hermes_constants import get_hermes_home
from .constants import MAX_ACTIVE_CHARS

SM_DB_PATH: Path = get_hermes_home() / "state.db"

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-4096;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------- sm_scopes ---
-- Must be created before sm_facts due to FK reference
CREATE TABLE IF NOT EXISTS sm_scopes (
    id              TEXT    PRIMARY KEY,
    label           TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','cold','closed')),
    last_referenced INTEGER NOT NULL,
    current_turn    INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    closed_at       INTEGER
);

-- Prevent duplicate active scopes with the same label (race condition guard)
CREATE UNIQUE INDEX IF NOT EXISTS idx_sm_scopes_active_label
    ON sm_scopes(label) WHERE status = 'active';

-- --------------------------------------------------------------- sm_sessions ---
CREATE TABLE IF NOT EXISTS sm_sessions (
    id              TEXT    PRIMARY KEY,
    started_at      INTEGER NOT NULL,
    last_turn       INTEGER NOT NULL DEFAULT 0,
    active_scope_id TEXT    REFERENCES sm_scopes(id)
);

-- ---------------------------------------------------------------- sm_facts ---
CREATE TABLE IF NOT EXISTS sm_facts (
    id            TEXT    PRIMARY KEY,
    content       TEXT    NOT NULL,
    type          TEXT    NOT NULL CHECK(type IN ('C','D','V','?','done','obs')),
    target        TEXT    NOT NULL,
    scope_id      TEXT    REFERENCES sm_scopes(id),
    status        TEXT    NOT NULL DEFAULT 'active'
                          CHECK(status IN ('active','cold','superseded','archived')),
    superseded_by TEXT    REFERENCES sm_facts(id),
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    last_accessed INTEGER,
    access_count  INTEGER NOT NULL DEFAULT 0,
    source_hash   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sm_facts_target      ON sm_facts(target);
CREATE INDEX IF NOT EXISTS idx_sm_facts_scope       ON sm_facts(scope_id);
CREATE INDEX IF NOT EXISTS idx_sm_facts_status      ON sm_facts(status);
CREATE INDEX IF NOT EXISTS idx_sm_facts_updated_at  ON sm_facts(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sm_facts_source_hash ON sm_facts(source_hash);

-- --------------------------------------------------------- sm_facts FTS5 index ---
CREATE VIRTUAL TABLE IF NOT EXISTS sm_facts_fts USING fts5(
    content,
    target,
    content='sm_facts',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS sm_facts_ai AFTER INSERT ON sm_facts BEGIN
    INSERT INTO sm_facts_fts(rowid, content, target)
    VALUES (new.rowid, new.content, new.target);
END;

CREATE TRIGGER IF NOT EXISTS sm_facts_ad AFTER DELETE ON sm_facts BEGIN
    INSERT INTO sm_facts_fts(sm_facts_fts, rowid, content, target)
    VALUES ('delete', old.rowid, old.content, old.target);
END;

CREATE TRIGGER IF NOT EXISTS sm_facts_au AFTER UPDATE ON sm_facts BEGIN
    INSERT INTO sm_facts_fts(sm_facts_fts, rowid, content, target)
    VALUES ('delete', old.rowid, old.content, old.target);
    INSERT INTO sm_facts_fts(rowid, content, target)
    VALUES (new.rowid, new.content, new.target);
END;

-- --------------------------------------------------------------- views ---
CREATE VIEW IF NOT EXISTS sm_hot_facts AS
    SELECT f.*
    FROM sm_facts f
    LEFT JOIN sm_scopes s ON f.scope_id = s.id
    WHERE f.status = 'active'
      AND (s.id IS NULL OR s.status = 'active')
    ORDER BY f.updated_at DESC;

"""


def get_sm_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database and apply schema migrations.
    Returns a connection with row_factory set to sqlite3.Row.
    Idempotent — safe to call multiple times.
    """
    path = Path(db_path) if db_path else SM_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    # Recreate sm_gauge view so MAX_ACTIVE_CHARS changes take effect immediately.
    # CREATE VIEW IF NOT EXISTS is sticky — DROP + CREATE ensures it's current.
    conn.executescript(f"""
    DROP VIEW IF EXISTS sm_gauge;
    CREATE VIEW sm_gauge AS
        SELECT
            COALESCE(SUM(
                LENGTH(type) + 1 + LENGTH(target) + 3 + LENGTH(content)
            ), 0)                                                    AS used_chars,
            {MAX_ACTIVE_CHARS}                                       AS max_chars,
            ROUND(COALESCE(SUM(
                LENGTH(type) + 1 + LENGTH(target) + 3 + LENGTH(content)
            ), 0) * 100.0
                  / {MAX_ACTIVE_CHARS}, 1)                          AS pct
        FROM sm_facts
        WHERE status = 'active';
    """)
    conn.commit()
    return conn


def sm_now() -> int:
    """Current Unix timestamp in seconds."""
    return int(time.time())
