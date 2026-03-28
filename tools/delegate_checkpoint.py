"""Opt-in checkpoint store for subagent conversations."""
import json
import threading
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS delegate_checkpoints (
    task_id     TEXT PRIMARY KEY,
    iteration   INTEGER NOT NULL,
    messages    TEXT NOT NULL,
    metadata    TEXT NOT NULL,
    saved_at    REAL NOT NULL
);
"""


class CheckpointStore:
    """
    SQLite-backed checkpoint store for subagent task state.
    Uses ~/.hermes/state.db by default (same as structured memory).

    Uses a single persistent connection per instance, protected by a
    threading.Lock, to avoid connection churn under parallel checkpoint saves.
    """

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = str(Path.home() / ".hermes" / "state.db")
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Return the shared connection, creating it if needed."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            self._get_conn().executescript(_SCHEMA)

    def save(
        self,
        task_id: str,
        iteration: int,
        messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> None:
        with self._lock:
            self._get_conn().execute(
                """INSERT OR REPLACE INTO delegate_checkpoints
                   (task_id, iteration, messages, metadata, saved_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    task_id,
                    iteration,
                    json.dumps(messages, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    time.time(),
                ),
            )
            self._get_conn().commit()

    def load(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM delegate_checkpoints WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "iteration": row["iteration"],
            "messages": json.loads(row["messages"]),
            "metadata": json.loads(row["metadata"]),
            "saved_at": row["saved_at"],
        }

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._get_conn().execute(
                "DELETE FROM delegate_checkpoints WHERE task_id = ?",
                (task_id,),
            )
            self._get_conn().commit()

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT task_id, iteration, metadata, saved_at FROM delegate_checkpoints"
            ).fetchall()
        return [
            {
                "task_id": r["task_id"],
                "iteration": r["iteration"],
                "metadata": json.loads(r["metadata"]),
                "saved_at": r["saved_at"],
            }
            for r in rows
        ]
