"""
structured_memory — native Python port of hermes-memory.
No MCP dependencies, no subprocess, pure stdlib SQLite.
"""

from .db import get_sm_connection, sm_now
from .facts import MemoryFullError, FactTooLargeError, parse_notation
from .constants import ABBREV_DICT, TYPE_DISPLAY, GAUGE_WARN, MAX_FACT_CHARS

__all__ = [
    "get_sm_connection",
    "sm_now",
    "MemoryFullError",
    "FactTooLargeError",
    "parse_notation",
    "ABBREV_DICT",
    "TYPE_DISPLAY",
    "GAUGE_WARN",
    "MAX_FACT_CHARS",
]
