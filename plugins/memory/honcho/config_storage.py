"""Honcho's provider-owned desktop fields, preserving its host blocks and OAuth lock."""
from typing import Any

from plugins.memory.honcho import client, oauth
from plugins.memory.honcho.config_schema import CONFIG_SCHEMA
from utils import atomic_json_write


class ConfigStorageMixin:
    """The provider API runs in the requested profile's bound scope."""

    def read_config_fields(self, hermes_home: str) -> dict[str, Any]:
        host = client.resolve_active_host()
        raw = oauth._read_config_strict(client.resolve_config_path())
        block = client._host_block(raw, host)
        values = {}
        for field in CONFIG_SCHEMA.fields:
            sources = (block, raw) if field.scope == "host" else (raw,)
            for source in sources:
                key = next((key for key in (field.key, *field.aliases) if key in source), None)
                if key is not None:
                    values[field.key] = source[key]
                    break
        return {"values": values, "placeholders": {"workspace": host, "aiPeer": host}}

    def write_config_fields(self, values: dict[str, Any], hermes_home: str) -> None:
        path, host = client.resolve_config_path(), client.resolve_active_host()
        with oauth._refresh_lock, oauth._config_refresh_lock(path):
            raw = oauth._read_config_strict(path)
            hosts = raw.get("hosts")
            raw["hosts"] = hosts = hosts if isinstance(hosts, dict) else {}
            existing = client._host_block(raw, host)
            host_key = next((key for key, block in hosts.items() if block is existing), host) if existing else host
            block = hosts.setdefault(host_key, existing)
            for field in CONFIG_SCHEMA.fields:
                if field.key not in values:
                    continue
                target = block if field.scope == "host" else raw
                value = values[field.key]
                if field.is_secret:
                    stored = target.get(field.key)
                    if isinstance(stored, str) and stored.startswith(oauth.ACCESS_TOKEN_PREFIX):
                        continue
                if value is None:
                    for key in (field.key, *field.aliases):
                        target.pop(key, None)
                else:
                    target[field.key] = value
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(path, raw, mode=0o600)
