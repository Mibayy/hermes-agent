"""Tests for the Google Gemini native adapter (agent/gemini_adapter.py).

All tests mock the google-generativeai SDK so they run offline.
"""

import json
import sys
import types
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal google-generativeai stub so tests work without the real SDK
# ---------------------------------------------------------------------------

def _make_genai_stub():
    """Return a minimal stub of google.generativeai and its types sub-module."""
    types_mod = types.ModuleType("google.generativeai.types")

    class FunctionDeclaration:
        def __init__(self, name, description="", parameters=None):
            self.name = name
            self.description = description
            self.parameters = parameters

    class Tool:
        def __init__(self, function_declarations=None):
            self.function_declarations = function_declarations or []

    class FunctionCallingConfig:
        def __init__(self, mode="AUTO"):
            self.mode = mode

    class ToolConfig:
        def __init__(self, function_calling_config=None):
            self.function_calling_config = function_calling_config

    class GenerationConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Content:
        def __init__(self, role="user", parts=None):
            self.role = role
            self.parts = parts or []

    class Part:
        def __init__(self, text=None, function_call=None, function_response=None, inline_data=None):
            self.text = text
            self.function_call = function_call
            self.function_response = function_response
            self.inline_data = inline_data

    class FunctionCall:
        def __init__(self, name="", args=None):
            self.name = name
            self.args = args or {}

    class FunctionResponse:
        def __init__(self, name="", response=None):
            self.name = name
            self.response = response or {}

    class Blob:
        def __init__(self, mime_type="", data=b""):
            self.mime_type = mime_type
            self.data = data

    for cls in (FunctionDeclaration, Tool, FunctionCallingConfig, ToolConfig,
                GenerationConfig, Content, Part, FunctionCall, FunctionResponse, Blob):
        setattr(types_mod, cls.__name__, cls)

    genai_mod = types.ModuleType("google.generativeai")
    genai_mod.types = types_mod
    genai_mod.configure = MagicMock()

    class GenerativeModel:
        def __init__(self, model_name="", system_instruction=None, tools=None,
                     tool_config=None, generation_config=None):
            self.model_name = model_name

        def generate_content(self, contents):
            # Returns a minimal response — overridden per test via patch
            return _make_simple_response("hello")

    genai_mod.GenerativeModel = GenerativeModel

    google_mod = types.ModuleType("google")
    google_mod.generativeai = genai_mod

    return google_mod, genai_mod, types_mod


def _install_genai_stub():
    google_mod, genai_mod, types_mod = _make_genai_stub()
    sys.modules.setdefault("google", google_mod)
    sys.modules["google.generativeai"] = genai_mod
    sys.modules["google.generativeai.types"] = types_mod
    return genai_mod, types_mod


def _make_simple_response(text: str, finish_reason_name: str = "STOP"):
    fr = SimpleNamespace(name=finish_reason_name)
    part = SimpleNamespace(text=text, function_call=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content, finish_reason=fr)
    usage = SimpleNamespace(
        prompt_token_count=10,
        candidates_token_count=5,
    )
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


def _make_tool_call_response(fn_name: str, args: dict):
    fc = SimpleNamespace(name=fn_name, args=args)
    part = SimpleNamespace(text=None, function_call=fc)
    content = SimpleNamespace(parts=[part])
    fr = SimpleNamespace(name="STOP")
    candidate = SimpleNamespace(content=content, finish_reason=fr)
    usage = SimpleNamespace(prompt_token_count=20, candidates_token_count=10)
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_genai():
    """Install a minimal genai stub before each test and clean up after."""
    _install_genai_stub()
    # Force reimport of gemini_adapter with the stub in place
    sys.modules.pop("agent.gemini_adapter", None)
    yield
    sys.modules.pop("agent.gemini_adapter", None)


# ---------------------------------------------------------------------------
# resolve_gemini_api_key
# ---------------------------------------------------------------------------

class TestResolveGeminiApiKey:
    def test_reads_google_api_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key-123")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from agent.gemini_adapter import resolve_gemini_api_key
        assert resolve_gemini_api_key() == "google-key-123"

    def test_reads_gemini_api_key_fallback(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-456")
        from agent.gemini_adapter import resolve_gemini_api_key
        assert resolve_gemini_api_key() == "gemini-key-456"

    def test_google_api_key_takes_priority(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "first")
        monkeypatch.setenv("GEMINI_API_KEY", "second")
        from agent.gemini_adapter import resolve_gemini_api_key
        assert resolve_gemini_api_key() == "first"

    def test_returns_empty_when_not_set(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from agent.gemini_adapter import resolve_gemini_api_key
        assert resolve_gemini_api_key() == ""


# ---------------------------------------------------------------------------
# _convert_messages_to_gemini
# ---------------------------------------------------------------------------

class TestConvertMessagesToGemini:
    def _convert(self, messages):
        from agent.gemini_adapter import _convert_messages_to_gemini
        return _convert_messages_to_gemini(messages)

    def test_system_message_extracted(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, contents = self._convert(messages)
        assert system == "Be helpful."
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_user_and_assistant(self):
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hey there!"},
        ]
        _, contents = self._convert(messages)
        assert contents[0].role == "user"
        assert contents[1].role == "model"

    def test_tool_result_produces_function_response(self):
        messages = [
            {"role": "tool", "name": "search", "tool_call_id": "call_1",
             "content": json.dumps({"results": ["a", "b"]})},
        ]
        _, contents = self._convert(messages)
        assert contents[0].role == "user"
        part = contents[0].parts[0]
        assert part.function_response is not None
        assert part.function_response.name == "search"

    def test_assistant_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "tc1", "type": "function",
                     "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
                ],
            }
        ]
        _, contents = self._convert(messages)
        assert contents[0].role == "model"
        part = contents[0].parts[0]
        assert part.function_call is not None
        assert part.function_call.name == "get_weather"
        assert part.function_call.args == {"city": "Paris"}

    def test_no_system_returns_none(self):
        messages = [{"role": "user", "content": "test"}]
        system, _ = self._convert(messages)
        assert system is None


# ---------------------------------------------------------------------------
# _normalize_gemini_response
# ---------------------------------------------------------------------------

class TestNormalizeGeminiResponse:
    def _norm(self, response, model="gemini-test"):
        from agent.gemini_adapter import _normalize_gemini_response
        return _normalize_gemini_response(response, model)

    def test_text_response(self):
        result = self._norm(_make_simple_response("Hello world"))
        assert result.choices[0].message.content == "Hello world"
        assert result.choices[0].finish_reason == "stop"
        assert result.choices[0].message.tool_calls is None

    def test_tool_call_response(self):
        raw = _make_tool_call_response("calculate", {"x": 1, "y": 2})
        result = self._norm(raw)
        assert result.choices[0].finish_reason == "tool_calls"
        tc = result.choices[0].message.tool_calls[0]
        assert tc.function.name == "calculate"
        args = json.loads(tc.function.arguments)
        assert args == {"x": 1, "y": 2}

    def test_usage_populated(self):
        result = self._norm(_make_simple_response("hi"))
        u = result.usage
        assert u is not None
        assert u.prompt_tokens == 10
        assert u.completion_tokens == 5
        assert u.total_tokens == 15


# ---------------------------------------------------------------------------
# GeminiAuxiliaryClient — smoke test via mock
# ---------------------------------------------------------------------------

class TestGeminiAuxiliaryClientSmoke:
    def test_create_returns_openai_compat_response(self, monkeypatch):
        from agent import gemini_adapter

        fake_response = _make_simple_response("Mocked!")

        class FakeModel:
            def generate_content(self, contents):
                return fake_response

        monkeypatch.setattr(
            gemini_adapter._genai, "GenerativeModel",
            lambda **kwargs: FakeModel(),
        )

        client = gemini_adapter.GeminiAuxiliaryClient("fake-key", "gemini-test")
        result = client.chat.completions.create(
            messages=[{"role": "user", "content": "Hello"}],
            model="gemini-test",
        )
        assert result.choices[0].message.content == "Mocked!"

    def test_async_client_wraps_sync(self):
        from agent.gemini_adapter import GeminiAuxiliaryClient, AsyncGeminiAuxiliaryClient
        sync = GeminiAuxiliaryClient("k", "m")
        async_c = AsyncGeminiAuxiliaryClient(sync)
        assert hasattr(async_c.chat, "completions")
        assert hasattr(async_c.chat.completions, "create")
