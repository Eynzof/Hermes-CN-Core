"""Bundled memory setup keeps optional dependencies under PM ownership."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "llm,embedder,vector,expected",
    [
        ("openai", "openai", "qdrant", ["mem0"]),
        ("ollama", "ollama", "qdrant", ["mem0", "mem0-ollama"]),
        ("openai", "openai", "pgvector", ["mem0", "mem0-pgvector"]),
    ],
)
def test_mem0_setup_prepares_only_selected_backend_extras(monkeypatch, llm, embedder, vector, expected):
    from plugins.memory.mem0._setup import _install_provider_deps

    calls = []
    monkeypatch.setattr("pm.sync_venv", lambda extras, **kwargs: calls.append((extras, kwargs)))
    monkeypatch.setattr("pm.available", lambda extra: True)
    monkeypatch.setattr("pm.environments_adopt.adopt_selected", lambda root: True)
    monkeypatch.setattr("tools.runtime_compat.is_frozen_runtime", lambda: False)

    assert _install_provider_deps(llm, embedder, vector)
    assert calls == [(expected, {"explicit": True})]


def test_hindsight_embedded_setup_reports_conflict_without_installing(monkeypatch, tmp_path, capsys):
    from plugins.memory.hindsight import setup

    choices = iter(["local_embedded", "openai"])
    monkeypatch.setattr(setup, "_select", lambda *args: next(choices))
    monkeypatch.setattr(setup, "_check_local_runtime", lambda: (False, "No module named 'hindsight'"))
    monkeypatch.setattr("pm.sync_venv", lambda *args, **kwargs: pytest.fail("must not install conflicting dependencies"))

    setup.run_setup(SimpleNamespace(_config={}), str(tmp_path), {"memory": {}})

    output = capsys.readouterr().out
    assert "requirements conflict" in output
    assert "Local External" in output
    assert not (tmp_path / "hindsight" / "config.json").exists()
