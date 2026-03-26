"""Tests for /compact (/compress) command enhancements.

Covers:
- `compact` alias resolves to `compress` in COMMAND_REGISTRY
- CLI _manual_compress: --preview, --dry-run, --aggressive flags
- Gateway _handle_compress_command: same flags via event.text
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_cli.commands import resolve_command, COMMAND_REGISTRY


# ---------------------------------------------------------------------------
# COMMAND_REGISTRY alias
# ---------------------------------------------------------------------------

def test_compact_alias_resolves_to_compress():
    cmd = resolve_command("compact")
    assert cmd is not None
    assert cmd.name == "compress"


def test_compress_still_resolves():
    cmd = resolve_command("compress")
    assert cmd is not None
    assert cmd.name == "compress"


def test_compress_args_hint_mentions_flags():
    cmd = resolve_command("compress")
    assert "--preview" in cmd.args_hint
    assert "--aggressive" in cmd.args_hint


# ---------------------------------------------------------------------------
# CLI _manual_compress flags
# ---------------------------------------------------------------------------

def _make_cli_with_history(n_messages: int = 10):
    """Return a minimal HermesCLI-like object with a fake conversation history."""
    history = []
    for i in range(n_messages):
        history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})

    cli = MagicMock()
    cli.conversation_history = history
    cli.agent = MagicMock()
    cli.agent.compression_enabled = True
    cli.agent._cached_system_prompt = ""
    # _compress_context returns (compressed, new_system)
    cli.agent._compress_context = MagicMock(return_value=(history[:4], ""))
    cli.agent._honcho = None
    return cli


def _run_manual_compress(cli, cmd: str):
    """Call the real _manual_compress on a fake CLI object."""
    from cli import HermesCLI
    return HermesCLI._manual_compress(cli, cmd)


def test_manual_compress_preview_does_not_modify_history(capsys):
    cli = _make_cli_with_history(10)
    original = list(cli.conversation_history)
    _run_manual_compress(cli, "/compress --preview")
    assert cli.conversation_history == original
    out = capsys.readouterr().out
    assert "Preview" in out or "dry-run" in out or "No changes" in out


def test_manual_compress_dry_run_does_not_modify_history(capsys):
    cli = _make_cli_with_history(10)
    original = list(cli.conversation_history)
    _run_manual_compress(cli, "/compact --dry-run")
    assert cli.conversation_history == original


def test_manual_compress_aggressive_keeps_last_4(capsys):
    cli = _make_cli_with_history(10)
    expected = cli.conversation_history[-4:]
    _run_manual_compress(cli, "/compact --aggressive")
    assert cli.conversation_history == expected
    out = capsys.readouterr().out
    assert "Aggressive" in out


def test_manual_compress_aggressive_dry_run_no_change(capsys):
    cli = _make_cli_with_history(10)
    original = list(cli.conversation_history)
    _run_manual_compress(cli, "/compact --aggressive --dry-run")
    assert cli.conversation_history == original
    out = capsys.readouterr().out
    assert "No changes" in out


def test_manual_compress_normal_path_calls_agent(capsys):
    cli = _make_cli_with_history(10)
    with patch("agent.model_metadata.estimate_messages_tokens_rough", return_value=1000):
        _run_manual_compress(cli, "/compress")
    cli.agent._compress_context.assert_called_once()


def test_manual_compress_too_few_messages(capsys):
    cli = _make_cli_with_history(2)
    _run_manual_compress(cli, "/compress")
    out = capsys.readouterr().out
    assert "Not enough" in out
    cli.agent._compress_context.assert_not_called()


def test_manual_compress_no_agent(capsys):
    cli = MagicMock()
    cli.conversation_history = [{"role": "user", "content": "hi"}] * 6
    cli.agent = None
    _run_manual_compress(cli, "/compress")
    out = capsys.readouterr().out
    assert "No active agent" in out


def test_manual_compress_disabled(capsys):
    cli = _make_cli_with_history(8)
    cli.agent.compression_enabled = False
    _run_manual_compress(cli, "/compress")
    out = capsys.readouterr().out
    assert "disabled" in out


# ---------------------------------------------------------------------------
# Gateway _handle_compress_command flags
# ---------------------------------------------------------------------------

def _make_gateway_runner():
    """Return a minimal GatewayRunner-like object."""
    from gateway.run import GatewayRunner  # imported for the method
    runner = MagicMock(spec=GatewayRunner)
    runner.session_store = MagicMock()
    runner._session_db = MagicMock()

    history = [
        {"role": "user", "content": f"msg {i}"}
        if i % 2 == 0
        else {"role": "assistant", "content": f"reply {i}"}
        for i in range(8)
    ]
    runner.session_store.load_transcript.return_value = history
    session_entry = MagicMock()
    session_entry.session_id = "test-session"
    session_entry.session_key = "test-key"
    runner.session_store.get_or_create_session.return_value = session_entry
    return runner, history


def _make_event(text: str):
    src = MagicMock()
    src.platform = MagicMock()
    src.platform.value = "test"
    src.user_id = "u1"
    evt = MagicMock()
    evt.source = src
    evt.text = text
    return evt


def _async_run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_gateway_compress_preview_returns_stats():
    from gateway.run import GatewayRunner
    runner, history = _make_gateway_runner()
    evt = _make_event("/compress --preview")

    with patch("agent.model_metadata.estimate_messages_tokens_rough", return_value=5000):
        result = _async_run(GatewayRunner._handle_compress_command(runner, evt))

    assert "Preview" in result or "dry-run" in result
    runner.session_store.rewrite_transcript.assert_not_called()


def test_gateway_compact_dry_run_no_rewrite():
    from gateway.run import GatewayRunner
    runner, history = _make_gateway_runner()
    evt = _make_event("/compact --dry-run")

    with patch("agent.model_metadata.estimate_messages_tokens_rough", return_value=5000):
        result = _async_run(GatewayRunner._handle_compress_command(runner, evt))

    runner.session_store.rewrite_transcript.assert_not_called()


def test_gateway_aggressive_rewrites_last_4():
    from gateway.run import GatewayRunner
    runner, history = _make_gateway_runner()
    evt = _make_event("/compact --aggressive")

    with patch("agent.model_metadata.estimate_messages_tokens_rough", return_value=5000):
        result = _async_run(GatewayRunner._handle_compress_command(runner, evt))

    runner.session_store.rewrite_transcript.assert_called_once()
    written = runner.session_store.rewrite_transcript.call_args[0][1]
    assert len(written) == 4
    assert "Aggressive" in result


def test_gateway_aggressive_dry_run_no_rewrite():
    from gateway.run import GatewayRunner
    runner, history = _make_gateway_runner()
    evt = _make_event("/compact --aggressive --dry-run")

    with patch("agent.model_metadata.estimate_messages_tokens_rough", return_value=5000):
        result = _async_run(GatewayRunner._handle_compress_command(runner, evt))

    runner.session_store.rewrite_transcript.assert_not_called()
    assert "No changes" in result


def test_gateway_too_few_messages():
    from gateway.run import GatewayRunner
    runner, _ = _make_gateway_runner()
    runner.session_store.load_transcript.return_value = [{"role": "user", "content": "hi"}]
    evt = _make_event("/compress")

    result = _async_run(GatewayRunner._handle_compress_command(runner, evt))
    assert "Not enough" in result
