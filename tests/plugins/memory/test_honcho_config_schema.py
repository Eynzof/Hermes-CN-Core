"""Tests for Honcho's declared config surface."""

from plugins.memory.config_schema import (
    KIND_BOOL,
    KIND_JSON,
    KIND_NUMBER,
    KIND_SECRET,
    KIND_SELECT,
    STORAGE_PROVIDER,
    get_provider_config_schema,
)

# The curated set shown in the compact panel; everything else lives in the modal.
INLINE_KEYS = {
    "apiKey",
    "baseUrl",
    "environment",
    "workspace",
    "peerName",
    "aiPeer",
    "sessionStrategy",
}

def test_honcho_is_declared():
    provider = get_provider_config_schema("honcho")

    assert provider is not None
    assert provider.label == "Honcho"
    assert provider.storage == STORAGE_PROVIDER
    # Field keys are unique, and the curated inline set is present.
    keys = [field.key for field in provider.fields]
    assert len(keys) == len(set(keys))
    assert INLINE_KEYS <= set(keys)

def test_api_key_is_a_secret_bound_to_env():
    provider = get_provider_config_schema("honcho")
    assert provider is not None

    api_key = next(f for f in provider.fields if f.key == "apiKey")
    assert api_key.kind == KIND_SECRET
    assert api_key.is_secret is True
    assert api_key.env_key == "HONCHO_API_KEY"



def test_provider_storage_preserves_host_layout_and_oauth(monkeypatch, tmp_path):
    import json
    from plugins.memory.honcho import HonchoMemoryProvider
    from plugins.memory.honcho import client, oauth

    path = tmp_path / "honcho.json"
    token = oauth.ACCESS_TOKEN_PREFIX + "existing-token"
    original = {"baseUrl": "https://honcho.example", "timeout": 8,
                "hosts": {"hermes.work": {"apiKey": token, "workspace": "old", "saveMessages": False},
                          "other": {"workspace": "untouched"}}}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(client, "resolve_config_path", lambda: path)
    monkeypatch.setattr(client, "resolve_active_host", lambda: "hermes_work")
    provider = HonchoMemoryProvider()
    before = provider.read_config_fields(str(tmp_path))
    assert before["values"]["saveMessages"] is False
    assert before["values"]["baseUrl"] == "https://honcho.example"
    assert before["placeholders"]["aiPeer"] == "hermes_work"

    provider.write_config_fields({"workspace": "new", "baseUrl": None, "apiKey": "manual-key"}, str(tmp_path))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"timeout": 8, "hosts": {
        "hermes.work": {"apiKey": token, "workspace": "new", "saveMessages": False},
        "other": {"workspace": "untouched"}}}


def test_provider_storage_refuses_malformed_config(monkeypatch, tmp_path):
    import json
    import pytest
    from plugins.memory.honcho import HonchoMemoryProvider, client

    path = tmp_path / "honcho.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(client, "resolve_config_path", lambda: path)
    monkeypatch.setattr(client, "resolve_active_host", lambda: "hermes")
    with pytest.raises(json.JSONDecodeError):
        HonchoMemoryProvider().write_config_fields({"workspace": "new"}, str(tmp_path))
    assert path.read_text(encoding="utf-8") == "{broken"
