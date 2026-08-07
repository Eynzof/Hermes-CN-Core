"""Tests for ``export `` prefix handling in the hand-rolled .env parsers.

Bash-compatible .env files commonly prefix lines with ``export `` (users
copy-paste from shell profiles, cloud provider docs, tutorials). The three
hand-rolled parsers — ``hermes_cli.config.load_env``,
``hermes_cli.main._has_any_provider_configured``, and
``tools.skills_tool.load_env`` — split on ``line.partition("=")`` and must
strip the ``export `` prefix first, otherwise ``export API_KEY=sk-...`` is
stored under the wrong key ``"export API_KEY"`` and the real key is lost
(setup wizard re-triggers, providers undetected, skill env passthrough drops
the var). See PR #6659.

These assert the behavior contract (prefix stripped → canonical key resolves),
not the literal parser source.
"""
from __future__ import annotations


import tempfile
from pathlib import Path
from unittest.mock import patch


def _write_env(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def test_config_load_env_strips_export_prefix(tmp_path):
    from hermes_cli.config import invalidate_env_cache, load_env

    env_path = tmp_path / ".env"
    _write_env(
        env_path,
        'export OPENAI_API_KEY=sk-export-123\n'
        'export OPENROUTER_API_KEY="sk-or-456"\n'
        'ANTHROPIC_API_KEY=sk-plain-789\n',
    )
    invalidate_env_cache()
    try:
        with patch("hermes_cli.config.get_env_path", return_value=env_path):
            env = load_env()
    finally:
        invalidate_env_cache()

    # Canonical keys resolve, export-prefixed wrong keys never appear.
    assert env["OPENAI_API_KEY"] == "sk-export-123"
    assert env["OPENROUTER_API_KEY"] == "sk-or-456"
    assert env["ANTHROPIC_API_KEY"] == "sk-plain-789"
    assert "export OPENAI_API_KEY" not in env


def test_skills_tool_load_env_strips_export_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "export SOME_SKILL_KEY=skillval\nPLAIN=plainval\n", encoding="utf-8"
    )

    # skills_tool.load_env reads get_hermes_home()/.env directly.
    # Evict + re-import instead of importlib.reload: reload mutates the
    # shared module __dict__, generation-splitting objects other test
    # modules imported at collection time.
    import sys

    saved = sys.modules.pop("tools.skills_tool", None)
    try:
        import tools.skills_tool as skills_tool

        with patch.object(skills_tool, "get_hermes_home", return_value=tmp_path):
            env = skills_tool.load_env()
    finally:
        sys.modules.pop("tools.skills_tool", None)
        if saved is not None:
            sys.modules["tools.skills_tool"] = saved
            # The re-import also re-bound the ``skills_tool`` attribute on the
            # parent ``tools`` package to the fresh copy; restore it so
            # ``import tools.skills_tool`` resolves to the original module
            # object again (cross-test module-identity pollution).
            tools_pkg = sys.modules.get("tools")
            if tools_pkg is not None:
                tools_pkg.skills_tool = saved

    assert env["SOME_SKILL_KEY"] == "skillval"
    assert env["PLAIN"] == "plainval"
    assert "export SOME_SKILL_KEY" not in env


def test_has_any_provider_configured_with_export_prefix(tmp_path):
    """An export-prefixed provider key in .env counts as configured.

    Exercises the .env-reading branch of _has_any_provider_configured by
    blanking provider creds from the process environment first, so detection
    depends solely on parsing the file.

    Runs in a subprocess: importing hermes_cli.main executes module-level
    startup code (dotenv load, plugin discovery, config caches) with global
    side effects.  Doing that in-process — even with sys.modules
    pop/restore around it — leaks those side effects into the shared
    interpreter and breaks later tests (cross-test pollution).
    """
    import os
    import subprocess
    import sys

    (tmp_path / ".env").write_text(
        "export OPENAI_API_KEY=sk-export-only-123\n", encoding="utf-8"
    )

    repo_root = Path(__file__).resolve().parents[2]
    probe = tmp_path / "_probe.py"
    probe.write_text(
        "import os\n"
        "# Blank provider-shaped creds so the os.environ short-circuit can't\n"
        "# mask the .env parse path.\n"
        "for key in list(os.environ):\n"
        "    if key.endswith(('_API_KEY', '_TOKEN')) and key != 'BWS_ACCESS_TOKEN':\n"
        "        del os.environ[key]\n"
        "import hermes_cli.main as hmain\n"
        "# load_hermes_dotenv populated os.environ from .env at import time;\n"
        "# re-clear so the function takes its .env-reading branch.\n"
        "for key in list(os.environ):\n"
        "    if key.endswith(('_API_KEY', '_TOKEN')) and key != 'BWS_ACCESS_TOKEN':\n"
        "        del os.environ[key]\n"
        "assert hmain._has_any_provider_configured() is True\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(probe)],
        env=env,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "subprocess _has_any_provider_configured check failed:\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
