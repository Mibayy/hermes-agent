"""Tests for tts.provider = "none" (disabled TTS).

Covers:
  - check_tts_requirements() returns False when provider is none
  - text_to_speech_tool() returns a clean error rather than trying to generate audio
  - _setup_tts_provider() writes "none" to config and shows it in the summary
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# check_tts_requirements
# ---------------------------------------------------------------------------

def test_check_tts_requirements_returns_false_for_none_provider():
    """check_tts_requirements() must short-circuit when tts.provider == 'none'."""
    from tools.tts_tool import check_tts_requirements

    with patch("tools.tts_tool._load_tts_config", return_value={"provider": "none"}):
        assert check_tts_requirements() is False


def test_check_tts_requirements_unaffected_for_edge():
    """Sanity check: edge provider still works when edge_tts is importable."""
    from tools.tts_tool import check_tts_requirements

    fake_edge = MagicMock()
    with patch("tools.tts_tool._load_tts_config", return_value={"provider": "edge"}), \
         patch("tools.tts_tool._import_edge_tts", return_value=fake_edge):
        assert check_tts_requirements() is True


# ---------------------------------------------------------------------------
# text_to_speech_tool
# ---------------------------------------------------------------------------

def test_text_to_speech_tool_returns_error_for_none_provider():
    """text_to_speech_tool() must return a JSON error without generating any audio."""
    from tools.tts_tool import text_to_speech_tool

    with patch("tools.tts_tool._load_tts_config", return_value={"provider": "none"}):
        result = text_to_speech_tool("hello world")

    data = json.loads(result)
    assert data["success"] is False
    assert "disabled" in data["error"].lower()
    assert "none" in data["error"]


# ---------------------------------------------------------------------------
# _setup_tts_provider — setup wizard
# ---------------------------------------------------------------------------

def test_setup_tts_provider_none_choice_writes_config(tmp_path, monkeypatch):
    """Choosing 'None (disable TTS entirely)' must persist provider='none' in config."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli.config import load_config, save_config
    from hermes_cli.setup import _setup_tts_provider

    config = load_config()

    # Choice index 4 == "None (disable TTS entirely)" in the updated choices list
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: 4)
    # Suppress the print_success call
    monkeypatch.setattr("hermes_cli.setup.print_success", lambda *a, **kw: None)

    _setup_tts_provider(config)

    reloaded = load_config()
    assert reloaded.get("tts", {}).get("provider") == "none"


def test_setup_tts_provider_keep_current_when_none_already_set(tmp_path, monkeypatch):
    """'Keep current' must not change an already-disabled provider."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli.config import load_config, save_config
    from hermes_cli.setup import _setup_tts_provider

    config = load_config()
    config.setdefault("tts", {})["provider"] = "none"
    save_config(config)

    # The "Keep current" option is always the last one (index == len(choices) - 1)
    monkeypatch.setattr(
        "hermes_cli.setup.prompt_choice",
        lambda question, choices, default=0: len(choices) - 1,
    )

    _setup_tts_provider(config)

    reloaded = load_config()
    assert reloaded.get("tts", {}).get("provider") == "none"


# ---------------------------------------------------------------------------
# _print_setup_summary
# ---------------------------------------------------------------------------

def test_print_setup_summary_shows_disabled_for_none_provider(tmp_path, monkeypatch, capsys):
    """Setup summary must list TTS as 'disabled' rather than available."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli.config import load_config, save_config
    from hermes_cli.setup import _print_setup_summary

    config = load_config()
    config.setdefault("tts", {})["provider"] = "none"
    save_config(config)

    _print_setup_summary(config, tmp_path)
    output = capsys.readouterr().out

    assert "Text-to-Speech" in output
    assert "disabled" in output.lower()
