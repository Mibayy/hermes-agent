"""
Shared constants for the structured memory package.
Extracted from hermes-memory/core/db.py and hermes-memory/core/optimize.py.
"""

from __future__ import annotations

import re

# Gauge thresholds (percentage of max_chars used by active facts)
GAUGE_MERGE     = 70  # deduplicate facts with same target+scope
GAUGE_WARN      = 80  # surface warning to user (no action yet)
GAUGE_ARCHIVE   = 85  # push closed-scope facts to cold
GAUGE_SYNTHESIS = 95  # last resort: LLM-assisted consolidation
GAUGE_FULL      = 100 # hard cap — writes refused until space freed

# Max chars stored in active facts before we start pushing to cold
MAX_ACTIVE_CHARS = 10_000

# Max chars for a single fact's content
MAX_FACT_CHARS = 400

# Scope auto-cooling: turns of silence before a scope goes cold
SCOPE_COOL_TURNS = 6

# Max facts returned by search
SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT     = 20

# Abbreviation dictionary injected into system prompt
ABBREV_DICT = {
    "cfg": "configuration",   "impl": "implementation",
    "msg": "message",         "req":  "requirement",
    "usr": "user",            "resp": "response",
    "prod": "production",     "feat": "feature",
    "dev": "development",     "deps": "dependencies",
    "auth": "authentication", "err":  "error",
    "db":  "database",        "btn":  "button",
    "env": "environment",     "doc":  "documentation",
    "perf": "performance",    "init": "initialization",
    "mgmt": "management",     "refct": "refactor",
    "mvmt": "movement",       "notif": "notification",
    "perms": "permissions",   "val":  "validation",
    "async": "asynchronous",  "sync": "synchronization",
    "mndtry": "mandatory",    "nvr":  "never",
    "alw": "always",          "tmp":  "temporary",
    "idx": "index",           "tbl":  "table",
    "svc": "service",         "pkg":  "package",
    "repo": "repository",     "api":  "API endpoint",
    "clt": "client",          "srv":  "server",
}

# Compression map: (pattern, replacement) tuples applied in order
# From hermes-memory/core/optimize.py
COMPRESS_MAP: list[tuple[str, str]] = [
    # French
    (r"\bpour\b",          "pr"),
    (r"\btoujours\b",      "tjrs"),
    (r"\bjamais\b",        "jamais"),   # keep — it's already short
    (r"\bchangement\b",    "chg"),
    (r"\bconfiguration\b", "cfg"),
    (r"\bdépendance\b",    "dep"),
    (r"\bexternes?\b",     "ext"),
    (r"\btéléphone\b",     "tel"),
    (r"\bidentifiants?\b", "creds"),
    (r"\bsupprimer\b",     "suppr"),
    (r"\baffichage\b",     "aff"),
    (r"\bmodification\b",  "modif"),
    (r"\breconstruction\b","rebuild"),
    (r"\bobligatoire\b",   "requis"),
    (r"\bchangements?\b",  "chg"),
    # English
    (r"\bconfiguration\b", "cfg"),
    (r"\brequired\b",      "req"),
    (r"\bnever\b",         "nvr"),
    (r"\balways\b",        "alw"),
    (r"\bdependenc(y|ies)\b", "dep"),
    (r"\bexternal\b",      "ext"),
    (r"\bcredentials?\b",  "creds"),
    (r"\bwith\b",          "w/"),
    (r"\bupgrade\b",       "↑"),
    (r"\bthen\b",          "→"),
    (r"\bzero\b",          "0"),
    # Symbols
    (r"\b--\b",            "—"),
    # Drop common filler
    (r"\bIt is worth noting that\b", ""),
    (r"\bNote that\b",     ""),
    (r"\bPlease note\b",   ""),
    (r"\bin order to\b",   "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bat this point in time\b", "now"),
]

# Map raw notation char -> DB type code
TYPE_MAP = {
    "C": "C",
    "D": "D",
    "V": "V",
    "?": "?",
    "✓": "done",
    "~": "obs",
}

# Reverse map for display (DB code -> notation symbol)
TYPE_DISPLAY = {v: k for k, v in TYPE_MAP.items()}

# Regex to parse   TYPE[target]: content
FACT_RE = re.compile(
    r"^(?P<type>[CDV?✓~])\[(?P<target>[^\]]+)\]:\s*(?P<content>.+)$",
    re.UNICODE,
)
