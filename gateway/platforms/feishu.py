"""
Feishu (Lark) platform adapter using Long Connection (WebSocket) mode.

Uses the lark-oapi SDK for real-time message reception via WebSocket.
Responses are sent via Feishu's Message API using App Access Token.

Requires:
    pip install lark-oapi httpx
    FEISHU_APP_ID and FEISHU_APP_SECRET env vars

Configuration in config.yaml:
    platforms:
      feishu:
        enabled: true
        extra:
          app_id: "cli_xxxx"        # or FEISHU_APP_ID env var
          app_secret: "your-secret" # or FEISHU_APP_SECRET env var
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import lark_oapi
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )
    LARK_OAPI_AVAILABLE = True
except ImportError:
    LARK_OAPI_AVAILABLE = False
    lark_oapi = None  # type: ignore[assignment]

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 30000
DEDUP_WINDOW_SECONDS = 300
DEDUP_MAX_SIZE = 1000
RECONNECT_BACKOFF = [2, 5, 10, 30, 60]

# Feishu API base URL (international: api.larksuite.com, mainland: open.feishu.cn)
_API_BASE = "https://open.feishu.cn/open-apis"
_LARK_API_BASE = "https://open.larksuite.com/open-apis"


def check_feishu_requirements() -> bool:
    """Check if Feishu dependencies are available and configured."""
    if not LARK_OAPI_AVAILABLE or not HTTPX_AVAILABLE:
        return False
    if not os.getenv("FEISHU_APP_ID") or not os.getenv("FEISHU_APP_SECRET"):
        return False
    return True


class FeishuAdapter(BasePlatformAdapter):
    """Feishu (Lark) chatbot adapter using Long Connection (WebSocket) mode.

    The lark-oapi SDK maintains a persistent WebSocket connection.
    Incoming messages arrive via event subscription callbacks. Replies are
    sent via Feishu's Message API authenticated with an App Access Token.
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.FEISHU)

        extra = config.extra or {}
        self._app_id: str = extra.get("app_id") or os.getenv("FEISHU_APP_ID", "")
        self._app_secret: str = extra.get("app_secret") or os.getenv("FEISHU_APP_SECRET", "")

        # Use Lark international domain when configured
        use_lark = extra.get("use_lark") or os.getenv("FEISHU_USE_LARK_DOMAIN", "").lower() in ("true", "1", "yes")
        self._api_base = _LARK_API_BASE if use_lark else _API_BASE

        self._ws_client: Any = None
        self._ws_task: Optional[asyncio.Task] = None
        self._http_client: Optional["httpx.AsyncClient"] = None

        # App Access Token cache
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        # Message deduplication: event_id -> timestamp
        self._seen_messages: Dict[str, float] = {}

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self) -> bool:
        """Connect to Feishu via Long Connection (WebSocket) mode."""
        if not LARK_OAPI_AVAILABLE:
            logger.warning("[%s] lark-oapi not installed. Run: pip install lark-oapi", self.name)
            return False
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx not installed. Run: pip install httpx", self.name)
            return False
        if not self._app_id or not self._app_secret:
            logger.warning("[%s] FEISHU_APP_ID and FEISHU_APP_SECRET required", self.name)
            return False

        try:
            self._http_client = httpx.AsyncClient(timeout=30.0)

            # Build lark-oapi client
            self._ws_client = (
                lark_oapi.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .build()
            )

            loop = asyncio.get_running_loop()
            self._ws_task = asyncio.create_task(self._run_ws(loop))
            self._mark_connected()
            logger.info("[%s] Connected via Long Connection mode", self.name)
            return True
        except Exception as e:
            logger.error("[%s] Failed to connect: %s", self.name, e)
            return False

    async def _run_ws(self, loop: asyncio.AbstractEventLoop) -> None:
        """Run the WebSocket listener with auto-reconnection."""
        backoff_idx = 0
        while self._running:
            try:
                logger.debug("[%s] Starting WebSocket client...", self.name)

                ws_client = (
                    lark_oapi.ws.Client.builder()
                    .app_id(self._app_id)
                    .app_secret(self._app_secret)
                    .event_handler(
                        lark_oapi.EventDispatcherHandler.builder("", "")
                        .register(
                            lark_oapi.im.v1.P2ImMessageReceiveV1.__event_type__,
                            _make_message_handler(self, loop),
                        )
                        .build()
                    )
                    .build()
                )
                # start() blocks until disconnected
                await asyncio.to_thread(ws_client.start)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.warning("[%s] WebSocket error: %s", self.name, e)

            if not self._running:
                return

            delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
            logger.info("[%s] Reconnecting in %ds...", self.name, delay)
            await asyncio.sleep(delay)
            backoff_idx += 1

    async def disconnect(self) -> None:
        """Disconnect from Feishu."""
        self._running = False
        self._mark_disconnected()

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._ws_client = None
        self._seen_messages.clear()
        logger.info("[%s] Disconnected", self.name)

    # -- Token management ---------------------------------------------------

    async def _get_access_token(self) -> str:
        """Return a valid App Access Token, refreshing if necessary."""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        if not self._http_client:
            return ""

        try:
            resp = await self._http_client.post(
                f"{self._api_base}/auth/v3/app_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=10.0,
            )
            data = resp.json()
            if data.get("code") == 0:
                self._access_token = data["app_access_token"]
                self._token_expires_at = now + data.get("expire", 7200)
                return self._access_token
            logger.warning("[%s] Token refresh failed: %s", self.name, data.get("msg"))
        except Exception as e:
            logger.error("[%s] Token refresh error: %s", self.name, e)
        return ""

    # -- Inbound message processing -----------------------------------------

    async def _on_message(self, event: Any) -> None:
        """Process an incoming Feishu message event."""
        try:
            msg = event.event.message
            sender = event.event.sender
        except AttributeError:
            logger.debug("[%s] Malformed event, skipping", self.name)
            return

        # Deduplication using event_id from the header
        event_id = getattr(getattr(event, "header", None), "event_id", None) or uuid.uuid4().hex
        if self._is_duplicate(event_id):
            logger.debug("[%s] Duplicate event %s, skipping", self.name, event_id)
            return

        # Skip messages sent by the bot itself
        if getattr(sender, "sender_id", None) and getattr(sender.sender_id, "open_id", "") == self._app_id:
            return

        text = self._extract_text(msg)
        if not text:
            logger.debug("[%s] Empty message, skipping", self.name)
            return

        chat_id = getattr(msg, "chat_id", "") or ""
        message_id = getattr(msg, "message_id", "") or uuid.uuid4().hex
        chat_type_raw = getattr(msg, "chat_type", "p2p")
        is_group = chat_type_raw in ("group", "topic_group")
        chat_type = "group" if is_group else "dm"

        sender_id_obj = getattr(sender, "sender_id", None)
        user_id = getattr(sender_id_obj, "open_id", "") if sender_id_obj else ""
        user_id_union = getattr(sender_id_obj, "union_id", "") if sender_id_obj else ""

        source = self.build_source(
            chat_id=chat_id or user_id,
            chat_name=None,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_id,
            user_id_alt=user_id_union if user_id_union else None,
        )

        # Parse timestamp (milliseconds epoch)
        create_time = getattr(msg, "create_time", None)
        try:
            ts = datetime.fromtimestamp(int(create_time) / 1000, tz=timezone.utc) if create_time else datetime.now(tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            ts = datetime.now(tz=timezone.utc)

        event_obj = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=message_id,
            raw_message=event,
            timestamp=ts,
        )

        logger.debug("[%s] Message from %s in %s: %s",
                     self.name, user_id[:16] if user_id else "?",
                     (chat_id or "dm")[:20], text[:50])
        await self.handle_message(event_obj)

    @staticmethod
    def _extract_text(msg: Any) -> str:
        """Extract plain text from a Feishu message object."""
        import json as _json

        msg_type = getattr(msg, "message_type", "text")
        content_raw = getattr(msg, "content", "") or ""

        try:
            content_data = _json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        except Exception:
            return str(content_raw).strip()

        if msg_type == "text":
            return str(content_data.get("text", "")).strip()

        if msg_type == "post":
            # Rich text: flatten all text elements
            parts: List[str] = []
            title = content_data.get("zh_cn") or content_data.get("en_us") or content_data
            if isinstance(title, dict):
                for block in title.get("content", []):
                    for element in block:
                        if element.get("tag") == "text":
                            parts.append(element.get("text", ""))
            return " ".join(parts).strip()

        # Fallback: return raw content as string
        return str(content_raw).strip()

    # -- Deduplication ------------------------------------------------------

    def _is_duplicate(self, event_id: str) -> bool:
        """Check and record an event ID. Returns True if already seen."""
        now = time.time()
        if len(self._seen_messages) > DEDUP_MAX_SIZE:
            cutoff = now - DEDUP_WINDOW_SECONDS
            self._seen_messages = {k: v for k, v in self._seen_messages.items() if v > cutoff}

        if event_id in self._seen_messages:
            return True
        self._seen_messages[event_id] = now
        return False

    # -- Outbound messaging -------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message to a Feishu chat."""
        import json as _json

        token = await self._get_access_token()
        if not token:
            return SendResult(success=False, error="Failed to obtain Feishu access token")

        if not self._http_client:
            return SendResult(success=False, error="HTTP client not initialized")

        truncated = content[:self.MAX_MESSAGE_LENGTH]
        payload_content = _json.dumps({"text": truncated})

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            resp = await self._http_client.post(
                f"{self._api_base}/im/v1/messages",
                headers=headers,
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": payload_content,
                },
                params={"receive_id_type": "chat_id"},
                timeout=15.0,
            )
            data = resp.json()
            if data.get("code") == 0:
                msg_id = data.get("data", {}).get("message_id", uuid.uuid4().hex[:12])
                return SendResult(success=True, message_id=str(msg_id))
            error_msg = data.get("msg", f"HTTP {resp.status_code}")
            logger.warning("[%s] Send failed: code=%s msg=%s", self.name, data.get("code"), error_msg)
            return SendResult(success=False, error=f"Feishu API error {data.get('code')}: {error_msg}")
        except Exception as e:
            logger.error("[%s] Send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Feishu does not support typing indicators."""
        pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about a Feishu chat."""
        token = await self._get_access_token()
        if not token or not self._http_client:
            return {"name": chat_id, "type": "group", "chat_id": chat_id}

        try:
            resp = await self._http_client.get(
                f"{self._api_base}/im/v1/chats/{chat_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            data = resp.json()
            if data.get("code") == 0:
                info = data.get("data", {})
                return {
                    "name": info.get("name", chat_id),
                    "type": "group" if info.get("chat_type") == "group" else "dm",
                    "chat_id": chat_id,
                }
        except Exception as e:
            logger.warning("[%s] get_chat_info error: %s", self.name, e)

        return {"name": chat_id, "type": "group", "chat_id": chat_id}


# ---------------------------------------------------------------------------
# Standalone send function (for send_message tool and cron delivery)
# ---------------------------------------------------------------------------

async def _send_feishu_message(
    app_id: str,
    app_secret: str,
    chat_id: str,
    message: str,
    use_lark: bool = False,
) -> Dict[str, Any]:
    """Send a single Feishu message without a running adapter.

    Uses httpx directly. Suitable for cron delivery and the send_message tool.
    """
    import json as _json

    if not HTTPX_AVAILABLE:
        return {"error": "httpx not installed. Run: pip install httpx"}

    api_base = _LARK_API_BASE if use_lark else _API_BASE

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Obtain token
            token_resp = await client.post(
                f"{api_base}/auth/v3/app_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            token_data = token_resp.json()
            if token_data.get("code") != 0:
                return {"error": f"Token error: {token_data.get('msg', 'unknown')}"}

            token = token_data["app_access_token"]

            # Send message
            payload_content = _json.dumps({"text": message[:MAX_MESSAGE_LENGTH]})
            send_resp = await client.post(
                f"{api_base}/im/v1/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": payload_content,
                },
                params={"receive_id_type": "chat_id"},
                timeout=15.0,
            )
            result = send_resp.json()
            if result.get("code") == 0:
                msg_id = result.get("data", {}).get("message_id", "")
                return {"success": True, "message_id": str(msg_id)}
            return {"error": f"Feishu API error {result.get('code')}: {result.get('msg', '')}"}
    except Exception as e:
        return {"error": f"Feishu send failed: {e}"}


# ---------------------------------------------------------------------------
# Internal event handler factory
# ---------------------------------------------------------------------------

def _make_message_handler(adapter: FeishuAdapter, loop: asyncio.AbstractEventLoop):
    """Return an event handler function for lark-oapi WebSocket dispatch."""

    def handler(data: Any) -> None:
        if loop is None or loop.is_closed():
            logger.error("[Feishu] Event loop unavailable, cannot dispatch message")
            return
        future = asyncio.run_coroutine_threadsafe(adapter._on_message(data), loop)
        try:
            future.result(timeout=60)
        except Exception:
            logger.exception("[Feishu] Error processing incoming message")

    return handler
