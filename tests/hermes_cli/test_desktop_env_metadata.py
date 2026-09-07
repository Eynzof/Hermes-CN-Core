"""The desktop reports effective credentials without crossing profile boundaries."""


def test_process_credential_visible_only_for_current_profile(tmp_path, monkeypatch):
    from hermes_cli import web_server
    from hermes_constants import get_hermes_home

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-process-credential")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _name: other)
    current = web_server._get_env_vars_sync()["DEEPSEEK_API_KEY"]
    assert current["is_set"] is True
    assert current["redacted_value"] != "test-process-credential"
    assert web_server._get_env_vars_sync("other")["DEEPSEEK_API_KEY"]["is_set"] is False
    assert not (get_hermes_home() / ".env").exists()
