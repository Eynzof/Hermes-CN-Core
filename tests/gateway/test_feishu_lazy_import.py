"""Regression coverage for deferred Feishu SDK loading."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, patch


def _feishu_adapter_module():
    """Import the adapter with the Windows app-data root available in CI."""
    with patch.dict(os.environ, {"LOCALAPPDATA": tempfile.gettempdir()}):
        from plugins.platforms.feishu import adapter

    return adapter


def test_configured_feishu_dependency_check_does_not_load_sdk():
    """Gateway configuration can validate Feishu without importing its SDK."""
    feishu_adapter = _feishu_adapter_module()

    with (
        patch.object(feishu_adapter, "FEISHU_AVAILABLE", False),
        patch("tools.lazy_deps.ensure", autospec=True) as ensure,
    ):
        assert feishu_adapter.check_feishu_requirements() is True
        assert feishu_adapter.FEISHU_AVAILABLE is False

    ensure.assert_called_once_with("platform.feishu", prompt=False)


def test_preinstalled_feishu_sdk_is_still_bound_on_first_use():
    """Installed and imported are distinct states for the deferred SDK."""
    feishu_adapter = _feishu_adapter_module()
    fake_lark = object()

    def _ensure_and_bind(_feature, importer, target_globals, *, prompt):
        assert prompt is False
        target_globals.update(importer())
        return True

    with (
        patch.object(feishu_adapter, "FEISHU_AVAILABLE", True),
        patch.object(feishu_adapter, "lark", None),
        patch.object(
            feishu_adapter,
            "_lark_bindings",
            return_value={"lark": fake_lark, "FEISHU_AVAILABLE": True},
        ),
        patch("tools.lazy_deps.ensure_and_bind", side_effect=_ensure_and_bind) as ensure_and_bind,
    ):
        assert feishu_adapter._load_lark_oapi() is True
        assert feishu_adapter.lark is fake_lark

    ensure_and_bind.assert_called_once()


def test_request_builders_fall_back_when_sdk_is_not_bound():
    """Stable None placeholders must still select the test-safe fallback."""
    feishu_adapter = _feishu_adapter_module()

    with (
        patch.object(feishu_adapter, "CreateMessageRequestBody", None),
        patch.object(feishu_adapter, "CreateMessageRequest", None),
    ):
        body = feishu_adapter.FeishuAdapter._build_create_message_body(
            receive_id="oc_chat",
            msg_type="text",
            content='{"text":"hello"}',
            uuid_value="uuid-1",
        )
        request = feishu_adapter.FeishuAdapter._build_create_message_request("chat_id", body)

    assert body.receive_id == "oc_chat"
    assert body.uuid == "uuid-1"
    assert request.receive_id_type == "chat_id"
    assert request.request_body is body


def test_feishu_connect_loads_sdk_on_worker_thread():
    """The first SDK import is deferred until a configured adapter connects."""
    from gateway.config import PlatformConfig
    feishu_adapter = _feishu_adapter_module()

    adapter = feishu_adapter.FeishuAdapter(
        PlatformConfig(
            extra={
                "app_id": "cli_test",
                "app_secret": "secret_test",
                "connection_mode": "websocket",
            }
        )
    )

    with (
        patch.object(feishu_adapter, "FEISHU_AVAILABLE", False),
        patch.object(feishu_adapter, "_load_lark_oapi", return_value=True) as load_sdk,
        patch.object(feishu_adapter.asyncio, "to_thread", new_callable=AsyncMock, return_value=True) as to_thread,
        patch.object(adapter, "_connect_with_retry", new_callable=AsyncMock),
        patch.object(feishu_adapter, "acquire_scoped_lock", return_value=(True, {})),
    ):
        assert asyncio.run(adapter.connect()) is True

    to_thread.assert_awaited_once_with(load_sdk)
