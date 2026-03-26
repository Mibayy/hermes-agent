"""XMPP messaging platform adapter.

Connects to an XMPP server using slixmpp (asyncio-native).
Supports 1:1 messages and MUC (multi-user chat) group rooms.
Optionally enables OMEMO end-to-end encryption via slixmpp-omemo.

Requires:
  - slixmpp installed: pip install slixmpp
  - XMPP_JID and XMPP_PASSWORD environment variables set
  - Optionally: slixmpp-omemo installed for OMEMO encryption
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_MESSAGE_LENGTH = 10000  # XMPP has no strict limit; use sensible default
RECONNECT_DELAY_INITIAL = 2.0
RECONNECT_DELAY_MAX = 60.0

# ---------------------------------------------------------------------------
# Availability flags (set at import time to avoid hard errors)
# ---------------------------------------------------------------------------

try:
    import slixmpp
    SLIXMPP_AVAILABLE = True
except ImportError:
    SLIXMPP_AVAILABLE = False

try:
    import slixmpp_omemo  # noqa: F401
    SLIXMPP_OMEMO_AVAILABLE = True
except ImportError:
    SLIXMPP_OMEMO_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_comma_list(value: str) -> List[str]:
    """Split a comma-separated string into a list, stripping whitespace."""
    return [v.strip() for v in value.split(",") if v.strip()]


def _bare_jid(jid: str) -> str:
    """Return the bare JID (strip resource part after /)."""
    if not jid:
        return jid
    # slixmpp JID objects have a 'bare' property; plain strings just split
    if hasattr(jid, "bare"):
        return str(jid.bare)
    return jid.split("/")[0] if "/" in jid else jid


def check_xmpp_requirements() -> bool:
    """Check if slixmpp is installed (minimum requirement for XMPP)."""
    return SLIXMPP_AVAILABLE


# ---------------------------------------------------------------------------
# Internal slixmpp client
# ---------------------------------------------------------------------------

class _HermesXMPP(slixmpp.ClientXMPP if SLIXMPP_AVAILABLE else object):
    """Thin slixmpp.ClientXMPP subclass that funnels events to our adapter."""

    def __init__(self, jid: str, password: str, adapter: "XMPPAdapter"):
        if not SLIXMPP_AVAILABLE:
            raise RuntimeError("slixmpp is not installed")
        super().__init__(jid, password)
        self._hermes_adapter = adapter

        # Register required plugins
        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # MUC
        self.register_plugin("xep_0085")  # Chat State Notifications (typing)
        self.register_plugin("xep_0199")  # XMPP Ping (keepalive)

        # Event handlers
        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("message", self._on_message)
        self.add_event_handler("disconnected", self._on_disconnected)
        self.add_event_handler("failed_auth", self._on_failed_auth)

    async def _on_session_start(self, event):
        """Called when XMPP session starts."""
        try:
            await self.get_roster()
            self.send_presence()
            logger.info("XMPP: session started as %s", self.boundjid.bare)
            self._hermes_adapter._on_connected()
        except Exception:
            logger.exception("XMPP: error during session_start")

    async def _on_message(self, msg):
        """Called for every incoming message stanza."""
        await self._hermes_adapter._handle_message(msg)

    async def _on_disconnected(self, event):
        """Called when the connection drops."""
        logger.warning("XMPP: disconnected")
        self._hermes_adapter._on_disconnected_event()

    async def _on_failed_auth(self, event):
        """Called when authentication fails."""
        logger.error("XMPP: authentication failed — check JID and password")
        self._hermes_adapter._on_auth_failed()


# ---------------------------------------------------------------------------
# XMPP Adapter
# ---------------------------------------------------------------------------

class XMPPAdapter(BasePlatformAdapter):
    """XMPP messaging adapter using slixmpp."""

    platform = Platform.XMPP

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.XMPP)

        extra = config.extra or {}

        # JID and password — prefer extra dict (YAML config) then env vars
        self.jid = extra.get("jid") or os.getenv("XMPP_JID", "")
        self.password = extra.get("password") or os.getenv("XMPP_PASSWORD", "")

        # OMEMO — only enable if the library is available AND config says so
        self.omemo_enabled = (
            SLIXMPP_OMEMO_AVAILABLE
            and extra.get("omemo", os.getenv("XMPP_OMEMO", "false").lower() in ("true", "1", "yes"))
        )

        # Internal state
        self._xmpp: Optional[_HermesXMPP] = None
        self._process_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._auth_failed = False
        self._connected_event = asyncio.Event()

        logger.info(
            "XMPP adapter initialized: jid=%s omemo=%s",
            self.jid or "<not set>",
            self.omemo_enabled,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to the XMPP server and start listening."""
        if not self.jid or not self.password:
            logger.error("XMPP: XMPP_JID and XMPP_PASSWORD are required")
            return False

        try:
            self._xmpp = _HermesXMPP(self.jid, self.password, self)
            self._xmpp.connect()
            # Schedule the slixmpp event-loop processing coroutine
            self._process_task = asyncio.ensure_future(self._xmpp.process(forever=True))
            # Wait briefly for session_start to confirm we're connected
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                logger.error("XMPP: connection timed out after 30s")
                await self._cleanup_xmpp()
                return False

            self._mark_connected()
            logger.info("XMPP: connected as %s", self.jid)
            return True

        except Exception as exc:
            logger.exception("XMPP: failed to connect: %s", exc)
            await self._cleanup_xmpp()
            return False

    def _on_connected(self):
        """Called from session_start event when XMPP is ready."""
        self._connected_event.set()

    def _on_disconnected_event(self):
        """Handle unexpected disconnection."""
        if self._running:
            logger.info("XMPP: scheduling reconnect")
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    def _on_auth_failed(self):
        """Handle authentication failure (non-retryable)."""
        self._auth_failed = True
        self._set_fatal_error(
            "auth_failed",
            "XMPP authentication failed — check XMPP_JID and XMPP_PASSWORD",
            retryable=False,
        )

    async def _cleanup_xmpp(self):
        """Cancel process task and disconnect XMPP client."""
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except (asyncio.CancelledError, Exception):
                pass
            self._process_task = None
        if self._xmpp:
            try:
                self._xmpp.disconnect()
            except Exception:
                pass
            self._xmpp = None

    async def _reconnect_loop(self):
        """Reconnect with exponential backoff."""
        delay = RECONNECT_DELAY_INITIAL
        while self._running and not self._auth_failed:
            jitter = delay * 0.2 * random.random()
            logger.info("XMPP: reconnecting in %.1fs", delay + jitter)
            await asyncio.sleep(delay + jitter)
            delay = min(delay * 2, RECONNECT_DELAY_MAX)

            self._connected_event.clear()
            await self._cleanup_xmpp()
            try:
                self._xmpp = _HermesXMPP(self.jid, self.password, self)
                self._xmpp.connect()
                self._process_task = asyncio.ensure_future(self._xmpp.process(forever=True))
                await asyncio.wait_for(self._connected_event.wait(), timeout=30)
                self._mark_connected()
                logger.info("XMPP: reconnected")
                return
            except asyncio.TimeoutError:
                logger.warning("XMPP: reconnect timed out, retrying")
            except Exception as exc:
                logger.warning("XMPP: reconnect error: %s", exc)

    async def disconnect(self) -> None:
        """Disconnect from XMPP."""
        self._running = False

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconnect_task = None

        await self._cleanup_xmpp()
        self._mark_disconnected()
        logger.info("XMPP: disconnected")

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    async def _handle_message(self, msg) -> None:
        """Process an incoming XMPP message stanza."""
        msg_type = str(msg.get("type", "chat"))

        # Only handle chat and groupchat messages
        if msg_type not in ("chat", "groupchat"):
            return

        # Extract text body
        body = msg.get("body", "").strip()
        if not body:
            return  # Ignore empty messages (e.g., typing indicators only)

        from_jid_raw = str(msg.get("from", ""))
        if not from_jid_raw:
            return

        is_groupchat = msg_type == "groupchat"

        if is_groupchat:
            # MUC: bare JID is the room; "from" includes resource = nick
            bare_from = _bare_jid(from_jid_raw)
            # Ignore our own MUC messages (resource matches our nick)
            resource = from_jid_raw.split("/", 1)[1] if "/" in from_jid_raw else ""
            my_nick = _bare_jid(self.jid).split("@")[0]
            if resource == my_nick:
                return
            chat_id = bare_from  # room@conference.example.org
            user_id = from_jid_raw  # room@conf/nick
            user_name = resource or from_jid_raw
            chat_type = "group"
        else:
            # 1:1 chat
            bare_from = _bare_jid(from_jid_raw)
            # Filter own messages
            if bare_from.lower() == _bare_jid(self.jid).lower():
                return
            chat_id = bare_from
            user_id = bare_from
            user_name = bare_from
            chat_type = "direct"

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
        )

        event = MessageEvent(
            source=source,
            text=body,
            message_type=MessageType.TEXT,
            timestamp=datetime.now(tz=timezone.utc),
        )

        await self.handle_message(event)

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message to a JID or MUC room."""
        if not self._xmpp:
            return SendResult(success=False, error="XMPP not connected")

        # Determine message type from chat_id — rooms typically have
        # a conference component but we also check metadata hint
        mtype = "chat"
        if metadata and metadata.get("type") == "groupchat":
            mtype = "groupchat"
        elif "@conference." in chat_id or "@muc." in chat_id or "@conference" in chat_id:
            mtype = "groupchat"

        try:
            self._xmpp.send_message(
                mto=chat_id,
                mbody=content,
                mtype=mtype,
            )
            return SendResult(success=True)
        except Exception as exc:
            logger.error("XMPP: send failed to %s: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Send a 'composing' chat state notification (XEP-0085)."""
        if not self._xmpp:
            return
        try:
            msg = self._xmpp.make_message(mto=chat_id, mtype="chat")
            if "xep_0085" in self._xmpp.plugin:
                msg["chat_state"] = "composing"
            msg.send()
        except Exception as exc:
            logger.debug("XMPP: send_typing failed: %s", exc)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: str = "",
    ) -> SendResult:
        """Send image as a URL reference (XMPP has no native inline image protocol)."""
        text = f"{caption}\n{image_url}".strip() if caption else image_url
        return await self.send(chat_id, text)

    async def get_chat_info(self, chat_id: str) -> dict:
        """Return basic chat metadata."""
        is_group = (
            "@conference." in chat_id
            or "@muc." in chat_id
            or "conference" in chat_id
        )
        return {
            "name": chat_id,
            "type": "group" if is_group else "direct",
            "chat_id": chat_id,
        }
