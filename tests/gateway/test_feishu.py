"""Tests for Feishu (Lark) platform adapter."""
import asyncio
import json
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig


# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------


class TestFeishuRequirements:

    def test_returns_false_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr("gateway.platforms.feishu.LARK_OAPI_AVAILABLE", False)
        monkeypatch.setattr("gateway.platforms.feishu.HTTPX_AVAILABLE", True)
        monkeypatch.setenv("FEISHU_APP_ID", "test-id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
        from gateway.platforms.feishu import check_feishu_requirements
        assert check_feishu_requirements() is False

    def test_returns_false_when_httpx_missing(self, monkeypatch):
        monkeypatch.setattr("gateway.platforms.feishu.LARK_OAPI_AVAILABLE", True)
        monkeypatch.setattr("gateway.platforms.feishu.HTTPX_AVAILABLE", False)
        monkeypatch.setenv("FEISHU_APP_ID", "test-id")
        monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
        from gateway.platforms.feishu import check_feishu_requirements
        assert check_feishu_requirements() is False

    def test_returns_false_when_env_vars_missing(self, monkeypatch):
        monkeypatch.setattr("gateway.platforms.feishu.LARK_OAPI_AVAILABLE", True)
        monkeypatch.setattr("gateway.platforms.feishu.HTTPX_AVAILABLE", True)
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        from gateway.platforms.feishu import check_feishu_requirements
        assert check_feishu_requirements() is False

    def test_returns_true_when_all_available(self, monkeypatch):
        monkeypatch.setattr("gateway.platforms.feishu.LARK_OAPI_AVAILABLE", True)
        monkeypatch.setattr("gateway.platforms.feishu.HTTPX_AVAILABLE", True)
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
        monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
        from gateway.platforms.feishu import check_feishu_requirements
        assert check_feishu_requirements() is True


# ---------------------------------------------------------------------------
# Adapter construction
# ---------------------------------------------------------------------------


class TestFeishuAdapterInit:

    def test_reads_config_from_extra(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(
            enabled=True,
            extra={"app_id": "cli_cfg", "app_secret": "cfg-secret"},
        )
        adapter = FeishuAdapter(config)
        assert adapter._app_id == "cli_cfg"
        assert adapter._app_secret == "cfg-secret"
        assert adapter.name == "Feishu"

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
        monkeypatch.setenv("FEISHU_APP_SECRET", "env-secret")
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True)
        adapter = FeishuAdapter(config)
        assert adapter._app_id == "cli_env"
        assert adapter._app_secret == "env-secret"

    def test_uses_feishu_api_base_by_default(self):
        from gateway.platforms.feishu import FeishuAdapter, _API_BASE
        config = PlatformConfig(enabled=True, extra={"app_id": "x", "app_secret": "y"})
        adapter = FeishuAdapter(config)
        assert adapter._api_base == _API_BASE

    def test_uses_lark_api_base_when_configured(self):
        from gateway.platforms.feishu import FeishuAdapter, _LARK_API_BASE
        config = PlatformConfig(enabled=True, extra={"app_id": "x", "app_secret": "y", "use_lark": True})
        adapter = FeishuAdapter(config)
        assert adapter._api_base == _LARK_API_BASE


# ---------------------------------------------------------------------------
# Platform enum
# ---------------------------------------------------------------------------


class TestFeishuPlatformEnum:

    def test_platform_enum_exists(self):
        assert Platform.FEISHU.value == "feishu"

    def test_platform_enum_in_config(self):
        from gateway.config import _apply_env_overrides, GatewayConfig
        config = GatewayConfig()
        assert Platform.FEISHU not in config.platforms


# ---------------------------------------------------------------------------
# Config loading from env vars
# ---------------------------------------------------------------------------


class TestFeishuConfigLoading:

    def test_env_vars_create_platform_config(self, monkeypatch):
        from gateway.config import _apply_env_overrides, GatewayConfig
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")
        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.FEISHU in config.platforms
        assert config.platforms[Platform.FEISHU].enabled is True
        assert config.platforms[Platform.FEISHU].extra["app_id"] == "cli_test"
        assert config.platforms[Platform.FEISHU].extra["app_secret"] == "secret_test"

    def test_missing_env_vars_dont_create_config(self, monkeypatch):
        from gateway.config import _apply_env_overrides, GatewayConfig
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.FEISHU not in config.platforms

    def test_home_channel_loaded_from_env(self, monkeypatch):
        from gateway.config import _apply_env_overrides, GatewayConfig
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")
        monkeypatch.setenv("FEISHU_HOME_CHANNEL", "oc_123456")
        config = GatewayConfig()
        _apply_env_overrides(config)
        home = config.platforms[Platform.FEISHU].home_channel
        assert home is not None
        assert home.chat_id == "oc_123456"
        assert home.platform == Platform.FEISHU


# ---------------------------------------------------------------------------
# Authorization map integration
# ---------------------------------------------------------------------------


class TestFeishuAuthorizationMaps:

    def test_feishu_in_allowed_users_allowlist(self):
        import gateway.run as run_module
        src = inspect.getsource(run_module)
        assert "FEISHU_ALLOWED_USERS" in src

    def test_feishu_in_allow_all_users_map(self):
        import gateway.run as run_module
        src = inspect.getsource(run_module)
        assert "FEISHU_ALLOW_ALL_USERS" in src


# ---------------------------------------------------------------------------
# Send message tool routing
# ---------------------------------------------------------------------------


class TestFeishuSendMessageRouting:

    def test_feishu_in_platform_map(self):
        import tools.send_message_tool as smt
        src = inspect.getsource(smt)
        assert '"feishu": Platform.FEISHU' in src

    def test_feishu_in_send_to_platform_routing(self):
        import tools.send_message_tool as smt
        src = inspect.getsource(smt)
        assert "_send_feishu" in src


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


class TestFeishuTextExtraction:

    def test_extracts_plain_text(self):
        from gateway.platforms.feishu import FeishuAdapter
        msg = MagicMock()
        msg.message_type = "text"
        msg.content = json.dumps({"text": "Hello Feishu"})
        assert FeishuAdapter._extract_text(msg) == "Hello Feishu"

    def test_extracts_post_rich_text(self):
        from gateway.platforms.feishu import FeishuAdapter
        post_content = {
            "zh_cn": {
                "title": "Test",
                "content": [
                    [{"tag": "text", "text": "Hello "}, {"tag": "text", "text": "World"}]
                ],
            }
        }
        msg = MagicMock()
        msg.message_type = "post"
        msg.content = json.dumps(post_content)
        result = FeishuAdapter._extract_text(msg)
        assert "Hello" in result
        assert "World" in result

    def test_returns_empty_for_invalid_json(self):
        from gateway.platforms.feishu import FeishuAdapter
        msg = MagicMock()
        msg.message_type = "text"
        msg.content = "not json"
        result = FeishuAdapter._extract_text(msg)
        assert result == "not json"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestFeishuDeduplication:

    def test_first_message_not_duplicate(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True, extra={"app_id": "x", "app_secret": "y"})
        adapter = FeishuAdapter(config)
        assert adapter._is_duplicate("event_001") is False

    def test_second_identical_message_is_duplicate(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True, extra={"app_id": "x", "app_secret": "y"})
        adapter = FeishuAdapter(config)
        adapter._is_duplicate("event_002")
        assert adapter._is_duplicate("event_002") is True

    def test_different_messages_not_duplicate(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True, extra={"app_id": "x", "app_secret": "y"})
        adapter = FeishuAdapter(config)
        adapter._is_duplicate("event_003")
        assert adapter._is_duplicate("event_004") is False


# ---------------------------------------------------------------------------
# Send method (mocked HTTP)
# ---------------------------------------------------------------------------


class TestFeishuSend:

    @pytest.mark.asyncio
    async def test_send_returns_success_on_ok_response(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True, extra={"app_id": "cli_x", "app_secret": "sec_y"})
        adapter = FeishuAdapter(config)

        # Mock access token fetch
        adapter._access_token = "mock_token"
        adapter._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"message_id": "om_test123"},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        adapter._http_client = mock_client

        result = await adapter.send("oc_chat123", "Hello")
        assert result.success is True
        assert result.message_id == "om_test123"

    @pytest.mark.asyncio
    async def test_send_returns_error_on_api_failure(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True, extra={"app_id": "cli_x", "app_secret": "sec_y"})
        adapter = FeishuAdapter(config)
        adapter._access_token = "mock_token"
        adapter._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 99991663, "msg": "app not exist"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        adapter._http_client = mock_client

        result = await adapter.send("oc_chat123", "Hello")
        assert result.success is False
        assert "99991663" in result.error or "app not exist" in result.error

    @pytest.mark.asyncio
    async def test_send_returns_error_without_http_client(self):
        from gateway.platforms.feishu import FeishuAdapter
        config = PlatformConfig(enabled=True, extra={"app_id": "cli_x", "app_secret": "sec_y"})
        adapter = FeishuAdapter(config)
        adapter._http_client = None

        result = await adapter.send("oc_chat123", "Hello")
        assert result.success is False
