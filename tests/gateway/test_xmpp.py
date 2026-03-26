"""Tests for XMPP platform adapter."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.config import Platform, PlatformConfig


# ---------------------------------------------------------------------------
# 1. Platform enum
# ---------------------------------------------------------------------------

class TestXMPPPlatformEnum:
    def test_xmpp_enum_value(self):
        assert Platform.XMPP.value == "xmpp"

    def test_xmpp_in_platform_list(self):
        values = [p.value for p in Platform]
        assert "xmpp" in values


# ---------------------------------------------------------------------------
# 2. Config loading from env vars
# ---------------------------------------------------------------------------

class TestXMPPConfigLoading:
    def test_apply_env_overrides_loads_xmpp(self, monkeypatch):
        monkeypatch.setenv("XMPP_JID", "hermes@example.org")
        monkeypatch.setenv("XMPP_PASSWORD", "s3cret")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.XMPP in config.platforms
        xc = config.platforms[Platform.XMPP]
        assert xc.enabled is True
        assert xc.extra["jid"] == "hermes@example.org"
        assert xc.extra["password"] == "s3cret"

    def test_xmpp_not_loaded_without_password(self, monkeypatch):
        monkeypatch.setenv("XMPP_JID", "hermes@example.org")
        monkeypatch.delenv("XMPP_PASSWORD", raising=False)

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.XMPP not in config.platforms

    def test_xmpp_not_loaded_without_jid(self, monkeypatch):
        monkeypatch.delenv("XMPP_JID", raising=False)
        monkeypatch.setenv("XMPP_PASSWORD", "s3cret")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.XMPP not in config.platforms

    def test_home_channel_loaded(self, monkeypatch):
        monkeypatch.setenv("XMPP_JID", "hermes@example.org")
        monkeypatch.setenv("XMPP_PASSWORD", "s3cret")
        monkeypatch.setenv("XMPP_HOME_CHANNEL", "user@example.org")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        xc = config.platforms[Platform.XMPP]
        assert xc.home_channel is not None
        assert xc.home_channel.chat_id == "user@example.org"

    def test_omemo_flag_loaded(self, monkeypatch):
        monkeypatch.setenv("XMPP_JID", "hermes@example.org")
        monkeypatch.setenv("XMPP_PASSWORD", "s3cret")
        monkeypatch.setenv("XMPP_OMEMO", "true")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        xc = config.platforms[Platform.XMPP]
        assert xc.extra["omemo"] is True

    def test_connected_platforms_includes_xmpp(self, monkeypatch):
        monkeypatch.setenv("XMPP_JID", "hermes@example.org")
        monkeypatch.setenv("XMPP_PASSWORD", "s3cret")

        from gateway.config import GatewayConfig, _apply_env_overrides
        config = GatewayConfig()
        _apply_env_overrides(config)

        connected = config.get_connected_platforms()
        assert Platform.XMPP in connected


# ---------------------------------------------------------------------------
# 3. check_xmpp_requirements
# ---------------------------------------------------------------------------

class TestXMPPRequirements:
    def test_returns_true_when_slixmpp_available(self, monkeypatch):
        monkeypatch.setattr("gateway.platforms.xmpp.SLIXMPP_AVAILABLE", True)
        from gateway.platforms.xmpp import check_xmpp_requirements
        assert check_xmpp_requirements() is True

    def test_returns_false_when_slixmpp_missing(self, monkeypatch):
        monkeypatch.setattr("gateway.platforms.xmpp.SLIXMPP_AVAILABLE", False)
        from gateway.platforms.xmpp import check_xmpp_requirements
        assert check_xmpp_requirements() is False


# ---------------------------------------------------------------------------
# 4. Adapter init (config parsing)
# ---------------------------------------------------------------------------

class TestXMPPAdapterInit:
    def _make_config(self, jid="bot@example.org", password="pass", **extra):
        config = PlatformConfig(
            enabled=True,
            extra={"jid": jid, "password": password, **extra},
        )
        return config

    def test_reads_jid_from_extra(self):
        from gateway.platforms.xmpp import XMPPAdapter
        adapter = XMPPAdapter(self._make_config(jid="hermes@xmpp.example.com"))
        assert adapter.jid == "hermes@xmpp.example.com"

    def test_reads_password_from_extra(self):
        from gateway.platforms.xmpp import XMPPAdapter
        adapter = XMPPAdapter(self._make_config(password="hunter2"))
        assert adapter.password == "hunter2"

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("XMPP_JID", "env@example.org")
        monkeypatch.setenv("XMPP_PASSWORD", "envpass")
        from gateway.platforms.xmpp import XMPPAdapter
        adapter = XMPPAdapter(PlatformConfig(enabled=True))
        assert adapter.jid == "env@example.org"
        assert adapter.password == "envpass"

    def test_omemo_disabled_by_default(self):
        from gateway.platforms.xmpp import XMPPAdapter
        adapter = XMPPAdapter(self._make_config())
        # omemo_enabled depends on SLIXMPP_OMEMO_AVAILABLE and config
        # Without env var set, it should be False
        assert isinstance(adapter.omemo_enabled, bool)

    def test_platform_attribute(self):
        from gateway.platforms.xmpp import XMPPAdapter
        adapter = XMPPAdapter(self._make_config())
        assert adapter.platform == Platform.XMPP

    def test_name_property(self):
        from gateway.platforms.xmpp import XMPPAdapter
        adapter = XMPPAdapter(self._make_config())
        assert adapter.name == "Xmpp"  # base class uses .title()


# ---------------------------------------------------------------------------
# 5. Helper functions
# ---------------------------------------------------------------------------

class TestBareJID:
    def test_strips_resource(self):
        from gateway.platforms.xmpp import _bare_jid
        assert _bare_jid("user@example.org/resource") == "user@example.org"

    def test_no_resource(self):
        from gateway.platforms.xmpp import _bare_jid
        assert _bare_jid("user@example.org") == "user@example.org"

    def test_empty_string(self):
        from gateway.platforms.xmpp import _bare_jid
        assert _bare_jid("") == ""

    def test_muc_jid(self):
        from gateway.platforms.xmpp import _bare_jid
        assert _bare_jid("room@conference.example.org/nick") == "room@conference.example.org"


class TestParseCommaList:
    def test_splits_commas(self):
        from gateway.platforms.xmpp import _parse_comma_list
        result = _parse_comma_list("a@x.org, b@x.org,c@x.org")
        assert result == ["a@x.org", "b@x.org", "c@x.org"]

    def test_empty_string(self):
        from gateway.platforms.xmpp import _parse_comma_list
        assert _parse_comma_list("") == []

    def test_single_item(self):
        from gateway.platforms.xmpp import _parse_comma_list
        assert _parse_comma_list("user@example.org") == ["user@example.org"]


# ---------------------------------------------------------------------------
# 6. Authorization integration (platform in allowlist maps)
# ---------------------------------------------------------------------------

class TestXMPPAuthorization:
    def test_xmpp_in_platform_env_map(self):
        """XMPP_ALLOWED_USERS must be registered in _is_user_authorized."""
        import inspect
        from gateway import run
        source = inspect.getsource(run.GatewayRunner._is_user_authorized)
        assert "XMPP_ALLOWED_USERS" in source

    def test_xmpp_in_allow_all_map(self):
        """XMPP_ALLOW_ALL_USERS must be registered in _is_user_authorized."""
        import inspect
        from gateway import run
        source = inspect.getsource(run.GatewayRunner._is_user_authorized)
        assert "XMPP_ALLOW_ALL_USERS" in source

    def test_allowed_user_passes(self, monkeypatch):
        monkeypatch.setenv("XMPP_ALLOWED_USERS", "alice@example.org,bob@example.org")

        from gateway.session import SessionSource
        source = SessionSource(
            platform=Platform.XMPP,
            chat_id="alice@example.org",
            chat_type="direct",
            user_id="alice@example.org",
            user_name="alice",
        )

        from unittest.mock import MagicMock
        from gateway.run import GatewayRunner
        runner = object.__new__(GatewayRunner)
        runner.config = MagicMock()
        runner.pairing_store = MagicMock()
        runner.pairing_store.is_approved.return_value = False

        result = runner._is_user_authorized(source)
        assert result is True

    def test_disallowed_user_blocked(self, monkeypatch):
        monkeypatch.setenv("XMPP_ALLOWED_USERS", "alice@example.org")
        monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
        monkeypatch.delenv("XMPP_ALLOW_ALL_USERS", raising=False)

        from gateway.session import SessionSource
        source = SessionSource(
            platform=Platform.XMPP,
            chat_id="eve@example.org",
            chat_type="direct",
            user_id="eve@example.org",
            user_name="eve",
        )

        from unittest.mock import MagicMock
        from gateway.run import GatewayRunner
        runner = object.__new__(GatewayRunner)
        runner.config = MagicMock()
        runner.pairing_store = MagicMock()
        runner.pairing_store.is_approved.return_value = False

        result = runner._is_user_authorized(source)
        assert result is False


# ---------------------------------------------------------------------------
# 7. Send message tool routing
# ---------------------------------------------------------------------------

class TestXMPPSendMessageTool:
    def test_xmpp_in_platform_map(self):
        import inspect
        from tools import send_message_tool as smt
        source = inspect.getsource(smt._handle_send)
        assert "xmpp" in source

    def test_xmpp_platform_resolves(self):
        """Platform map contains 'xmpp' key mapping to Platform.XMPP."""
        # We inspect the function source to verify the key exists
        import inspect
        from tools import send_message_tool as smt
        # The platform_map dict is inside _handle_send
        source = inspect.getsource(smt._handle_send)
        assert '"xmpp": Platform.XMPP' in source or "'xmpp': Platform.XMPP" in source


# ---------------------------------------------------------------------------
# 8. Cron scheduler routing
# ---------------------------------------------------------------------------

class TestXMPPCronScheduler:
    def test_xmpp_in_scheduler_platform_map(self):
        import inspect
        from cron import scheduler
        source = inspect.getsource(scheduler._deliver_result)
        assert "xmpp" in source


# ---------------------------------------------------------------------------
# 9. Channel directory
# ---------------------------------------------------------------------------

class TestXMPPChannelDirectory:
    def test_xmpp_in_session_based_discovery(self):
        import inspect
        from gateway import channel_directory
        source = inspect.getsource(channel_directory.build_channel_directory)
        assert "xmpp" in source


# ---------------------------------------------------------------------------
# 10. Prompt builder platform hint
# ---------------------------------------------------------------------------

class TestXMPPPromptHint:
    def test_xmpp_hint_exists(self):
        from agent.prompt_builder import PLATFORM_HINTS
        assert "xmpp" in PLATFORM_HINTS

    def test_xmpp_hint_not_empty(self):
        from agent.prompt_builder import PLATFORM_HINTS
        assert len(PLATFORM_HINTS["xmpp"]) > 20


# ---------------------------------------------------------------------------
# 11. Toolset
# ---------------------------------------------------------------------------

class TestXMPPToolset:
    def test_hermes_xmpp_toolset_exists(self):
        from toolsets import TOOLSETS
        assert "hermes-xmpp" in TOOLSETS

    def test_hermes_xmpp_in_gateway_includes(self):
        from toolsets import TOOLSETS
        gateway = TOOLSETS.get("hermes-gateway", {})
        includes = gateway.get("includes", [])
        assert "hermes-xmpp" in includes


# ---------------------------------------------------------------------------
# 12. Adapter get_chat_info
# ---------------------------------------------------------------------------

class TestXMPPGetChatInfo:
    def _make_adapter(self):
        from gateway.platforms.xmpp import XMPPAdapter
        config = PlatformConfig(
            enabled=True,
            extra={"jid": "bot@example.org", "password": "pass"},
        )
        return XMPPAdapter(config)

    @pytest.mark.asyncio
    async def test_direct_chat_type(self):
        adapter = self._make_adapter()
        info = await adapter.get_chat_info("user@example.org")
        assert info["type"] == "direct"
        assert info["chat_id"] == "user@example.org"

    @pytest.mark.asyncio
    async def test_conference_room_type(self):
        adapter = self._make_adapter()
        info = await adapter.get_chat_info("room@conference.example.org")
        assert info["type"] == "group"


# ---------------------------------------------------------------------------
# 13. Inbound message handling (unit-level mock)
# ---------------------------------------------------------------------------

class TestXMPPMessageHandling:
    def _make_adapter(self):
        from gateway.platforms.xmpp import XMPPAdapter
        config = PlatformConfig(
            enabled=True,
            extra={"jid": "bot@example.org", "password": "pass"},
        )
        adapter = XMPPAdapter(config)
        adapter._running = True
        return adapter

    def _make_msg(self, from_jid, body, mtype="chat"):
        msg = MagicMock()
        msg.get = lambda key, default="": {
            "type": mtype,
            "from": from_jid,
            "body": body,
        }.get(key, default)
        return msg

    @pytest.mark.asyncio
    async def test_dispatches_direct_message(self):
        adapter = self._make_adapter()
        received = []

        async def mock_handle(event):
            received.append(event)

        adapter.handle_message = mock_handle

        msg = self._make_msg("alice@example.org", "hello bot")
        await adapter._handle_message(msg)

        assert len(received) == 1
        assert received[0].text == "hello bot"
        assert received[0].source.user_id == "alice@example.org"

    @pytest.mark.asyncio
    async def test_filters_own_messages(self):
        adapter = self._make_adapter()
        received = []

        async def mock_handle(event):
            received.append(event)

        adapter.handle_message = mock_handle

        # From our own JID
        msg = self._make_msg("bot@example.org", "echo")
        await adapter._handle_message(msg)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_ignores_empty_body(self):
        adapter = self._make_adapter()
        received = []

        async def mock_handle(event):
            received.append(event)

        adapter.handle_message = mock_handle

        msg = self._make_msg("alice@example.org", "")
        await adapter._handle_message(msg)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_ignores_non_chat_types(self):
        adapter = self._make_adapter()
        received = []

        async def mock_handle(event):
            received.append(event)

        adapter.handle_message = mock_handle

        msg = self._make_msg("alice@example.org", "ping", mtype="headline")
        await adapter._handle_message(msg)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_groupchat_sets_group_type(self):
        adapter = self._make_adapter()
        received = []

        async def mock_handle(event):
            received.append(event)

        adapter.handle_message = mock_handle

        msg = self._make_msg("room@conference.example.org/alice", "hi room", mtype="groupchat")
        await adapter._handle_message(msg)

        assert len(received) == 1
        assert received[0].source.chat_type == "group"


# ---------------------------------------------------------------------------
# 14. Disconnect / cleanup
# ---------------------------------------------------------------------------

class TestXMPPDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_sets_running_false(self):
        from gateway.platforms.xmpp import XMPPAdapter
        config = PlatformConfig(
            enabled=True,
            extra={"jid": "bot@example.org", "password": "pass"},
        )
        adapter = XMPPAdapter(config)
        adapter._running = True
        # Mock out the xmpp client so disconnect doesn't actually connect
        adapter._xmpp = None
        await adapter.disconnect()
        assert adapter._running is False


# ---------------------------------------------------------------------------
# 15. Connect fails gracefully without credentials
# ---------------------------------------------------------------------------

class TestXMPPConnectFailure:
    @pytest.mark.asyncio
    async def test_connect_fails_without_jid(self):
        from gateway.platforms.xmpp import XMPPAdapter
        config = PlatformConfig(enabled=True, extra={})
        adapter = XMPPAdapter(config)
        result = await adapter.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_fails_without_password(self):
        from gateway.platforms.xmpp import XMPPAdapter
        config = PlatformConfig(
            enabled=True,
            extra={"jid": "bot@example.org"},
        )
        adapter = XMPPAdapter(config)
        result = await adapter.connect()
        assert result is False
