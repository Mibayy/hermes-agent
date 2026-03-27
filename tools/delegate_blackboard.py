"""Shared blackboard for sibling subagents in a delegate_task batch."""
import threading
from typing import Any, Dict, Optional


class Blackboard:
    """Thread-safe key-value store shared across siblings in a batch."""

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        """Return a shallow copy of the current state."""
        with self._lock:
            return dict(self._data)

    def to_context_string(self) -> str:
        """Serialize for injection into child system prompt."""
        snap = self.snapshot()
        if not snap:
            return ""
        lines = ["Shared blackboard state:"]
        for k, v in snap.items():
            lines.append(f"  {k}: {v!r}")
        return "\n".join(lines)
