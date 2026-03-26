from io import StringIO

import pytest
from rich.console import Console

from hermes_cli.skills_hub import do_check, do_install, do_list, do_search, do_update, handle_skills_slash


class _DummyLockFile:
    def __init__(self, installed):
        self._installed = installed

    def list_installed(self):
        return self._installed


@pytest.fixture()
def hub_env(monkeypatch, tmp_path):
    """Set up isolated hub directory paths and return (monkeypatch, tmp_path)."""
    import tools.skills_hub as hub

    hub_dir = tmp_path / "skills" / ".hub"
    monkeypatch.setattr(hub, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(hub, "HUB_DIR", hub_dir)
    monkeypatch.setattr(hub, "LOCK_FILE", hub_dir / "lock.json")
    monkeypatch.setattr(hub, "QUARANTINE_DIR", hub_dir / "quarantine")
    monkeypatch.setattr(hub, "AUDIT_LOG", hub_dir / "audit.log")
    monkeypatch.setattr(hub, "TAPS_FILE", hub_dir / "taps.json")
    monkeypatch.setattr(hub, "INDEX_CACHE_DIR", hub_dir / "index-cache")

    return hub_dir


# ---------------------------------------------------------------------------
# Fixtures for common skill setups
# ---------------------------------------------------------------------------

_HUB_ENTRY = {"name": "hub-skill", "source": "github", "trust_level": "community"}

_ALL_THREE_SKILLS = [
    {"name": "hub-skill", "category": "x", "description": "hub"},
    {"name": "builtin-skill", "category": "x", "description": "builtin"},
    {"name": "local-skill", "category": "x", "description": "local"},
]

_BUILTIN_MANIFEST = {"builtin-skill": "abc123"}


@pytest.fixture()
def three_source_env(monkeypatch, hub_env):
    """Populate hub/builtin/local skills for source-classification tests."""
    import tools.skills_hub as hub
    import tools.skills_sync as skills_sync
    import tools.skills_tool as skills_tool

    monkeypatch.setattr(hub, "HubLockFile", lambda: _DummyLockFile([_HUB_ENTRY]))
    monkeypatch.setattr(skills_tool, "_find_all_skills", lambda: list(_ALL_THREE_SKILLS))
    monkeypatch.setattr(skills_sync, "_read_manifest", lambda: dict(_BUILTIN_MANIFEST))

    return hub_env


def _capture(source_filter: str = "all") -> str:
    """Run do_list into a string buffer and return the output."""
    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    do_list(source_filter=source_filter, console=console)
    return sink.getvalue()


def _capture_check(monkeypatch, results, name=None) -> str:
    import tools.skills_hub as hub

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    monkeypatch.setattr(hub, "check_for_skill_updates", lambda **_kwargs: results)
    do_check(name=name, console=console)
    return sink.getvalue()


def _capture_update(monkeypatch, results) -> tuple[str, list[tuple[str, str, bool]]]:
    import tools.skills_hub as hub
    import hermes_cli.skills_hub as cli_hub

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)
    installs = []

    monkeypatch.setattr(hub, "check_for_skill_updates", lambda **_kwargs: results)
    monkeypatch.setattr(hub, "HubLockFile", lambda: type("L", (), {
        "get_installed": lambda self, name: {"install_path": "category/" + name}
    })())
    monkeypatch.setattr(cli_hub, "do_install", lambda identifier, category="", force=False, console=None: installs.append((identifier, category, force)))

    do_update(console=console)
    return sink.getvalue(), installs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_do_list_initializes_hub_dir(monkeypatch, hub_env):
    import tools.skills_sync as skills_sync
    import tools.skills_tool as skills_tool

    monkeypatch.setattr(skills_tool, "_find_all_skills", lambda: [])
    monkeypatch.setattr(skills_sync, "_read_manifest", lambda: {})

    hub_dir = hub_env
    assert not hub_dir.exists()

    _capture()

    assert hub_dir.exists()
    assert (hub_dir / "lock.json").exists()
    assert (hub_dir / "quarantine").is_dir()
    assert (hub_dir / "index-cache").is_dir()


def test_do_list_distinguishes_hub_builtin_and_local(three_source_env):
    output = _capture()

    assert "hub-skill" in output
    assert "builtin-skill" in output
    assert "local-skill" in output
    assert "1 hub-installed, 1 builtin, 1 local" in output


def test_do_list_filter_local(three_source_env):
    output = _capture(source_filter="local")

    assert "local-skill" in output
    assert "builtin-skill" not in output
    assert "hub-skill" not in output


def test_do_list_filter_hub(three_source_env):
    output = _capture(source_filter="hub")

    assert "hub-skill" in output
    assert "builtin-skill" not in output
    assert "local-skill" not in output


def test_do_list_filter_builtin(three_source_env):
    output = _capture(source_filter="builtin")

    assert "builtin-skill" in output
    assert "hub-skill" not in output
    assert "local-skill" not in output


def test_do_check_reports_available_updates(monkeypatch):
    output = _capture_check(monkeypatch, [
        {"name": "hub-skill", "source": "skills.sh", "status": "update_available"},
        {"name": "other-skill", "source": "github", "status": "up_to_date"},
    ])

    assert "hub-skill" in output
    assert "update_available" in output
    assert "up_to_date" in output


def test_do_check_handles_no_installed_updates(monkeypatch):
    output = _capture_check(monkeypatch, [])

    assert "No hub-installed skills to check" in output


def test_do_update_reinstalls_outdated_skills(monkeypatch):
    output, installs = _capture_update(monkeypatch, [
        {"name": "hub-skill", "identifier": "skills-sh/example/repo/hub-skill", "status": "update_available"},
        {"name": "other-skill", "identifier": "github/example/other-skill", "status": "up_to_date"},
    ])

    assert installs == [("skills-sh/example/repo/hub-skill", "category", True)]
    assert "Updated 1 skill" in output


def test_do_install_scans_with_resolved_identifier(monkeypatch, tmp_path, hub_env):
    import tools.skills_guard as guard
    import tools.skills_hub as hub

    canonical_identifier = "skills-sh/anthropics/skills/frontend-design"

    class _ResolvedSource:
        def inspect(self, identifier):
            return type("Meta", (), {
                "extra": {},
                "identifier": canonical_identifier,
            })()

        def fetch(self, identifier):
            return type("Bundle", (), {
                "name": "frontend-design",
                "files": {"SKILL.md": "# Frontend Design"},
                "source": "skills.sh",
                "identifier": canonical_identifier,
                "trust_level": "trusted",
                "metadata": {},
            })()

    q_path = tmp_path / "skills" / ".hub" / "quarantine" / "frontend-design"
    q_path.mkdir(parents=True)
    (q_path / "SKILL.md").write_text("# Frontend Design")

    scanned = {}

    def _scan_skill(skill_path, source="community"):
        scanned["source"] = source
        return guard.ScanResult(
            skill_name="frontend-design",
            source=source,
            trust_level="trusted",
            verdict="safe",
        )

    monkeypatch.setattr(hub, "ensure_hub_dirs", lambda: None)
    monkeypatch.setattr(hub, "create_source_router", lambda auth: [_ResolvedSource()])
    monkeypatch.setattr(hub, "quarantine_bundle", lambda bundle: q_path)
    monkeypatch.setattr(hub, "HubLockFile", lambda: type("Lock", (), {"get_installed": lambda self, name: None})())
    monkeypatch.setattr(guard, "scan_skill", _scan_skill)
    monkeypatch.setattr(guard, "format_scan_report", lambda result: "scan ok")
    monkeypatch.setattr(guard, "should_allow_install", lambda result, force=False: (False, "stop after scan"))

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_install("skils-sh/anthropics/skills/frontend-design", console=console, skip_confirm=True)

    assert scanned["source"] == canonical_identifier



# ---------------------------------------------------------------------------
# Stale index entry error messages (#3259)
# ---------------------------------------------------------------------------

def _make_stale_source(source_id_val="skills-sh"):
    """A source that returns index metadata but no bundle (stale entry)."""
    class StaleSource:
        def source_id(self):
            return source_id_val
        def inspect(self, identifier):
            return type("Meta", (), {
                "name": "vercel-react-best-practices",
                "description": "Vercel React best practices",
                "source": "skills.sh",
                "identifier": identifier,
                "trust_level": "community",
                "repo": "vercel-labs/agent-skills",
                "path": "vercel-react-best-practices",
                "tags": [],
                "extra": {},
            })()
        def fetch(self, identifier):
            return None  # 404 - file gone from GitHub
    return StaleSource()


def test_do_install_stale_index_shows_helpful_message(monkeypatch, tmp_path, hub_env):
    """When index has metadata but GitHub returns 404, show stale index entry message."""
    import tools.skills_hub as hub

    monkeypatch.setattr(hub, "ensure_hub_dirs", lambda: None)
    monkeypatch.setattr(hub, "create_source_router", lambda auth: [_make_stale_source()])

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_install("skills-sh/vercel-labs/agent-skills/vercel-react-best-practices", console=console)

    output = sink.getvalue()
    assert "stale" in output.lower() or "no longer exist" in output or "removed" in output


def test_do_install_unknown_identifier_shows_generic_message(monkeypatch, tmp_path, hub_env):
    """When neither meta nor bundle is found, show the generic Could not fetch message."""
    import tools.skills_hub as hub

    class EmptySource:
        def source_id(self): return "github"
        def inspect(self, identifier): return None
        def fetch(self, identifier): return None

    monkeypatch.setattr(hub, "ensure_hub_dirs", lambda: None)
    monkeypatch.setattr(hub, "create_source_router", lambda auth: [EmptySource()])

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_install("github/nobody/nowhere/nonexistent-skill", console=console)

    output = sink.getvalue()
    assert "Could not fetch" in output
    assert "stale" not in output.lower()


def test_do_search_shows_skills_sh_caveat(monkeypatch):
    """When search results include skills-sh entries, a caveat note is shown."""
    import tools.skills_hub as hub

    skills_sh_meta = type("Meta", (), {
        "name": "vercel-react-best-practices",
        "description": "Vercel React best practices",
        "source": "skills-sh",
        "identifier": "skills-sh/vercel-labs/agent-skills/vercel-react-best-practices",
        "trust_level": "community",
        "install_count": 251885,
    })()

    monkeypatch.setattr(hub, "create_source_router", lambda auth: [])
    monkeypatch.setattr(hub, "unified_search", lambda query, sources, source_filter, limit: [skills_sh_meta])

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_search("react", console=console)

    output = sink.getvalue()
    assert "skills.sh" in output
    assert "stale" in output.lower() or "removed" in output.lower() or "renamed" in output.lower()


def test_do_search_no_caveat_for_official_only(monkeypatch):
    """When all results are official, no skills.sh caveat is shown."""
    import tools.skills_hub as hub

    official_meta = type("Meta", (), {
        "name": "python-dev",
        "description": "Python development skill",
        "source": "official",
        "identifier": "official/development/python-dev",
        "trust_level": "builtin",
    })()

    monkeypatch.setattr(hub, "create_source_router", lambda auth: [])
    monkeypatch.setattr(hub, "unified_search", lambda query, sources, source_filter, limit: [official_meta])

    sink = StringIO()
    console = Console(file=sink, force_terminal=False, color_system=None)

    do_search("python", console=console)

    output = sink.getvalue()
    assert "stale" not in output.lower()
    assert "removed" not in output.lower()
