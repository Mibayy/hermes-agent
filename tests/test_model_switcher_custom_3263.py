"""Tests for model switcher custom endpoint fixes (#3263).

Covers:
1. Silent validation failure surfaces connection errors for custom endpoints
2. _model_flow_custom pre-fills base_url / model from config.yaml
3. runtime_provider honours config.yaml custom endpoint over OPENROUTER_API_KEY
"""
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 1. Silent validation failure — model_switch.py
# ---------------------------------------------------------------------------

def _make_switch_result(raw_input, base_url, exc):
    """Run switch_model with a validate_requested_model that raises exc."""
    from hermes_cli.model_switch import switch_model

    def _fake_resolve(requested=None, **kw):
        return {"provider": "custom", "api_key": "fake", "base_url": base_url,
                "api_mode": "chat_completions", "source": "config"}

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=_fake_resolve), \
         patch("hermes_cli.models.validate_requested_model", side_effect=exc), \
         patch("hermes_cli.models.detect_provider_for_model", return_value=None), \
         patch("hermes_cli.models.parse_model_input", return_value=("custom", "llama3")):
        return switch_model(raw_input, current_provider="custom", current_base_url=base_url)


def test_connection_refused_surfaced_for_localhost():
    result = _make_switch_result(
        "llama3",
        "http://localhost:11434/v1",
        ConnectionError("Connection refused"),
    )
    assert result.success is False
    assert "connection" in result.error_message.lower() or "refused" in result.error_message.lower()


def test_timeout_surfaced_for_custom_endpoint():
    result = _make_switch_result(
        "llama3",
        "http://127.0.0.1:8080/v1",
        TimeoutError("timed out"),
    )
    assert result.success is False
    assert result.error_message != ""


def test_404_surfaced_for_custom_endpoint():
    result = _make_switch_result(
        "llama3",
        "http://localhost:11434/v1",
        Exception("404 Not Found"),
    )
    assert result.success is False


def test_cloud_provider_validation_error_still_accepts():
    """Cloud provider validation errors should NOT block the switch (temporary outages)."""
    from hermes_cli.model_switch import switch_model

    def _fake_resolve(requested=None, **kw):
        return {"provider": "openrouter", "api_key": "sk-test", "base_url": "https://openrouter.ai/api/v1",
                "api_mode": "chat_completions", "source": "env"}

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=_fake_resolve), \
         patch("hermes_cli.models.validate_requested_model", side_effect=Exception("temporary error")), \
         patch("hermes_cli.models.detect_provider_for_model", return_value=None), \
         patch("hermes_cli.models.parse_model_input", return_value=("openrouter", "gpt-4o")):
        result = switch_model("gpt-4o", current_provider="openrouter",
                              current_base_url="https://openrouter.ai/api/v1")

    # Cloud provider: accept despite error (temporary outage tolerance)
    assert result.success is True


# ---------------------------------------------------------------------------
# 2. Pre-fill from config.yaml — _model_flow_custom
# ---------------------------------------------------------------------------

def test_model_flow_custom_prefills_from_config(monkeypatch):
    """When config.yaml has base_url and model.default, they appear as defaults."""
    import hermes_cli.main as _main

    saved_inputs = []

    def _fake_input(prompt=""):
        saved_inputs.append(prompt)
        return ""  # empty — should fall back to config default

    fake_cfg = {
        "model": {
            "base_url": "http://localhost:11434/v1",
            "default": "llama3",
            "provider": "custom",
        }
    }
    applied = {}

    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: fake_cfg)
    monkeypatch.setattr("hermes_cli.config.get_env_value", lambda key: "")

    with patch("hermes_cli.models.probe_api_models",
               return_value={"models": ["llama3"], "probed_url": "http://localhost:11434/v1/models"}), \
         patch("hermes_cli.auth._save_model_choice", side_effect=lambda n: applied.update(model=n)), \
         patch("hermes_cli.config.save_env_value"), \
         patch("hermes_cli.config.save_config"), \
         patch("hermes_cli.auth.deactivate_provider"), \
         patch("hermes_cli.main._save_custom_provider"):
        _main._model_flow_custom(fake_cfg)

    url_prompt = next((p for p in saved_inputs if "base URL" in p), "")
    assert "localhost:11434" in url_prompt, f"URL not pre-filled in prompt: {url_prompt!r}"

    model_prompt = next((p for p in saved_inputs if "Model name" in p), "")
    assert "llama3" in model_prompt, f"Model not pre-filled in prompt: {model_prompt!r}"

    assert applied.get("model") == "llama3", "Empty input should default to config model"


def test_model_flow_custom_uses_config_base_url_when_empty_input(monkeypatch):
    """Empty URL input with existing config should keep the existing URL for probing."""
    import hermes_cli.main as _main

    fake_cfg = {
        "model": {
            "base_url": "http://localhost:11434/v1",
            "default": "llama3",
            "provider": "custom",
        }
    }
    probed = {}

    monkeypatch.setattr("builtins.input", lambda p="": "")
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: fake_cfg)
    monkeypatch.setattr("hermes_cli.config.get_env_value", lambda key: "")

    def _capture_probe(key, url):
        probed["url"] = url
        return {"models": ["llama3"], "probed_url": url}

    with patch("hermes_cli.models.probe_api_models", side_effect=_capture_probe), \
         patch("hermes_cli.auth._save_model_choice"), \
         patch("hermes_cli.config.save_env_value"), \
         patch("hermes_cli.config.save_config"), \
         patch("hermes_cli.auth.deactivate_provider"), \
         patch("hermes_cli.main._save_custom_provider"):
        _main._model_flow_custom(fake_cfg)

    assert probed.get("url") == "http://localhost:11434/v1", (
        f"Expected config URL to be used for probing, got: {probed.get('url')!r}"
    )


# ---------------------------------------------------------------------------
# 3. runtime_provider — config.yaml custom takes precedence over OPENROUTER_API_KEY
# ---------------------------------------------------------------------------

def test_custom_config_wins_over_openrouter_key(monkeypatch):
    """When config has provider:custom + base_url, OPENROUTER_API_KEY must not flip base_url."""
    import os
    import hermes_cli.runtime_provider as rp

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    cfg = {
        "model": {
            "provider": "custom",
            "base_url": "http://localhost:11434/v1",
            "default": "llama3",
        }
    }

    with patch("hermes_cli.runtime_provider.load_config", return_value=cfg):
        result = rp.resolve_runtime_provider(requested="auto")

    assert "openrouter.ai" not in result["base_url"], (
        f"Expected custom base_url to win, got: {result['base_url']!r}"
    )
    assert "localhost" in result["base_url"]


def test_openrouter_key_still_works_without_custom_config(monkeypatch):
    """Without a custom config, OPENROUTER_API_KEY should still route to OpenRouter."""
    import hermes_cli.runtime_provider as rp

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    cfg = {"model": {"provider": "openrouter", "default": "gpt-4o"}}

    with patch("hermes_cli.runtime_provider.load_config", return_value=cfg):
        result = rp.resolve_runtime_provider(requested="auto")

    assert "openrouter.ai" in result["base_url"]
