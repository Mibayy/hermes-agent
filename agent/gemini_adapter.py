"""Google Gemini (AI Studio) native adapter for Hermes Agent.

Translates between Hermes's internal OpenAI-style message/tool format and
Google's GenerativeAI SDK.  Follows the same pattern as anthropic_adapter —
all provider-specific logic is isolated here so the rest of the codebase only
ever sees the .chat.completions.create() interface.

Why a native adapter instead of the OpenAI-compat shim?
  The OpenAI-compatible endpoint Google exposes
  (https://generativelanguage.googleapis.com/v1beta/openai/) works for simple
  completions but is known to produce fragmented / invalid tool-call JSON for
  complex multi-tool agent conversations.  The native SDK handles tool
  serialisation correctly and supports parallel tool calls reliably.

Auth:
  Reads GOOGLE_API_KEY (or GEMINI_API_KEY as an alias).
  Set it at https://aistudio.google.com/apikey
"""

from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy-import guard — google-generativeai is optional
try:
    import google.generativeai as _genai  # type: ignore[import-untyped]
    from google.generativeai import types as _genai_types  # type: ignore[import-untyped]
    _GENAI_AVAILABLE = True
except ImportError:
    _genai = None  # type: ignore[assignment]
    _genai_types = None  # type: ignore[assignment]
    _GENAI_AVAILABLE = False


def _require_genai() -> None:
    if not _GENAI_AVAILABLE:
        raise ImportError(
            "The 'google-generativeai' package is required for the Gemini provider. "
            "Install it with: pip install 'google-generativeai>=0.8.0'"
        )


def resolve_gemini_api_key() -> str:
    """Return the first available Gemini API key, or empty string."""
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        val = os.getenv(var, "").strip()
        if val:
            return val
    return ""


# ---------------------------------------------------------------------------
# Message / Tool format conversion
# ---------------------------------------------------------------------------

def _convert_tools_to_gemini(tools: List[Dict]) -> List[Any]:
    """Convert OpenAI-style tools list to Gemini FunctionDeclarations."""
    _require_genai()
    declarations = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        fn = tool["function"]
        params = fn.get("parameters", {})
        # Gemini expects Schema object or plain dict
        declarations.append(
            _genai_types.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=params,
            )
        )
    if not declarations:
        return []
    return [_genai_types.Tool(function_declarations=declarations)]


def _convert_messages_to_gemini(
    messages: List[Dict],
) -> Tuple[Optional[str], List[Any]]:
    """Convert OpenAI messages list to (system_instruction, gemini_contents).

    Returns:
        system_instruction: str or None (from system messages)
        contents: list of Gemini Content objects
    """
    _require_genai()
    system_parts: List[str] = []
    contents: List[Any] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")
        name = msg.get("name")

        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        system_parts.append(part["text"])
            continue

        # Map OpenAI roles to Gemini roles
        if role in ("user", "tool"):
            gemini_role = "user"
        elif role == "assistant":
            gemini_role = "model"
        else:
            gemini_role = "user"

        parts: List[Any] = []

        # Tool result message (role=tool in OpenAI → function response in Gemini)
        if role == "tool":
            fn_name = name or tool_call_id or "tool"
            # Parse the content if it's JSON
            try:
                result_data = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                result_data = {"result": str(content)}
            parts.append(
                _genai_types.Part(
                    function_response=_genai_types.FunctionResponse(
                        name=fn_name,
                        response=result_data if isinstance(result_data, dict) else {"result": result_data},
                    )
                )
            )

        # Assistant message with tool_calls
        elif role == "assistant" and tool_calls:
            if isinstance(content, str) and content.strip():
                parts.append(_genai_types.Part(text=content))
            for tc in tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                parts.append(
                    _genai_types.Part(
                        function_call=_genai_types.FunctionCall(
                            name=fn.get("name", ""),
                            args=args,
                        )
                    )
                )

        # Regular text content
        else:
            if isinstance(content, str):
                parts.append(_genai_types.Part(text=content or " "))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        ptype = part.get("type")
                        if ptype == "text":
                            parts.append(_genai_types.Part(text=part.get("text", "")))
                        elif ptype == "image_url":
                            # Inline images — best-effort; base64 data URIs only
                            url = part.get("image_url", {}).get("url", "")
                            if url.startswith("data:"):
                                try:
                                    header, b64data = url.split(",", 1)
                                    mime = header.split(":")[1].split(";")[0]
                                    import base64
                                    raw = base64.b64decode(b64data)
                                    parts.append(_genai_types.Part(
                                        inline_data=_genai_types.Blob(mime_type=mime, data=raw)
                                    ))
                                except Exception:
                                    pass  # Skip unconvertible images
            elif content is None:
                parts.append(_genai_types.Part(text=" "))

        if parts:
            contents.append(_genai_types.Content(role=gemini_role, parts=parts))

    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


def _normalize_gemini_response(response: Any, model: str) -> SimpleNamespace:
    """Convert a Gemini GenerateContentResponse to an OpenAI-style response."""
    tool_calls = []
    text_parts = []
    finish_reason = "stop"

    candidate = response.candidates[0] if response.candidates else None
    if candidate:
        for part in (candidate.content.parts if candidate.content else []):
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                import uuid
                tool_calls.append(SimpleNamespace(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    type="function",
                    function=SimpleNamespace(
                        name=fc.name,
                        arguments=json.dumps(dict(fc.args)),
                    ),
                ))
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)

        # Map Gemini finish reasons to OpenAI equivalents
        fr = getattr(candidate, "finish_reason", None)
        if fr is not None:
            fr_name = fr.name if hasattr(fr, "name") else str(fr)
            if fr_name in ("STOP", "1"):
                finish_reason = "stop"
            elif fr_name in ("MAX_TOKENS", "2"):
                finish_reason = "length"
            elif fr_name in ("SAFETY", "3", "RECITATION", "4"):
                finish_reason = "content_filter"
            elif tool_calls:
                finish_reason = "tool_calls"

    if tool_calls:
        finish_reason = "tool_calls"

    message = SimpleNamespace(
        role="assistant",
        content="\n".join(text_parts) if text_parts else (None if tool_calls else ""),
        tool_calls=tool_calls if tool_calls else None,
    )

    # Usage
    usage = None
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        um = response.usage_metadata
        pt = getattr(um, "prompt_token_count", 0) or 0
        ct = getattr(um, "candidates_token_count", 0) or 0
        usage = SimpleNamespace(
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
        )

    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason=finish_reason,
    )
    return SimpleNamespace(choices=[choice], model=model, usage=usage)


# ---------------------------------------------------------------------------
# Adapter classes
# ---------------------------------------------------------------------------

class _GeminiCompletionsAdapter:
    """OpenAI .chat.completions.create()-compatible adapter over google-generativeai."""

    def __init__(self, api_key: str, model: str):
        _require_genai()
        self._api_key = api_key
        self._model = model
        _genai.configure(api_key=api_key)

    def create(self, **kwargs) -> Any:
        _require_genai()

        messages: List[Dict] = kwargs.get("messages", [])
        model: str = kwargs.get("model") or self._model
        tools_raw: Optional[List[Dict]] = kwargs.get("tools")
        max_tokens: int = (
            kwargs.get("max_tokens")
            or kwargs.get("max_completion_tokens")
            or 8192
        )
        temperature: Optional[float] = kwargs.get("temperature")
        tool_choice = kwargs.get("tool_choice", "auto")

        system_instruction, contents = _convert_messages_to_gemini(messages)
        gemini_tools = _convert_tools_to_gemini(tools_raw) if tools_raw else None

        # Tool calling mode
        tool_config = None
        if gemini_tools:
            if tool_choice == "none":
                mode = "NONE"
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                mode = "ANY"
            else:
                mode = "AUTO"
            tool_config = _genai_types.ToolConfig(
                function_calling_config=_genai_types.FunctionCallingConfig(mode=mode)
            )

        gen_config_kwargs: Dict[str, Any] = {"max_output_tokens": max_tokens}
        if temperature is not None:
            gen_config_kwargs["temperature"] = temperature

        gen_model = _genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction,
            tools=gemini_tools,
            tool_config=tool_config,
            generation_config=_genai_types.GenerationConfig(**gen_config_kwargs),
        )

        response = gen_model.generate_content(contents)
        return _normalize_gemini_response(response, model)


class _GeminiChatShim:
    def __init__(self, adapter: _GeminiCompletionsAdapter):
        self.completions = adapter


class GeminiAuxiliaryClient:
    """OpenAI-client-compatible wrapper over Google Gemini (AI Studio)."""

    def __init__(self, api_key: str, model: str):
        adapter = _GeminiCompletionsAdapter(api_key, model)
        self.chat = _GeminiChatShim(adapter)
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def close(self) -> None:
        pass  # No persistent connection to close


class _AsyncGeminiCompletionsAdapter:
    def __init__(self, sync_adapter: _GeminiCompletionsAdapter):
        self._sync = sync_adapter

    async def create(self, **kwargs) -> Any:
        import asyncio
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncGeminiChatShim:
    def __init__(self, adapter: _AsyncGeminiCompletionsAdapter):
        self.completions = adapter


class AsyncGeminiAuxiliaryClient:
    def __init__(self, sync_wrapper: GeminiAuxiliaryClient):
        sync_adapter = sync_wrapper.chat.completions
        async_adapter = _AsyncGeminiCompletionsAdapter(sync_adapter)
        self.chat = _AsyncGeminiChatShim(async_adapter)
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url
