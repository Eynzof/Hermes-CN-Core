"""Update prompts preserve --yes and unreadable-input behavior after PM migration.

Configuration now runs in the fresh completion process. These tests exercise
its real migration boundary; real Git/autostash transport has its own suite.
"""
from unittest.mock import patch

import pytest

from hermes_cli import update_cmd_config


@pytest.fixture
def migration(monkeypatch):
    from hermes_cli import config, update_cmd

    monkeypatch.setattr(config, "get_missing_config_fields", lambda: [])
    monkeypatch.setattr(config, "get_missing_env_vars", lambda **kw: ["NEW_KEY"])
    monkeypatch.setattr(config, "check_config_version", lambda **kw: (1, 2))
    monkeypatch.setattr(update_cmd, "_migrate_sibling_profile_configs", lambda: [])
    monkeypatch.setattr(update_cmd_config, "_restore_snapshot_safety_nets", lambda _: None)
    calls = []

    def migrate(**kwargs):
        calls.append(kwargs)
        return {"env_added": [], "config_added": []}

    monkeypatch.setattr(config, "migrate_config", migrate)
    return calls


class TestUpdateYesConfigMigration:
    def test_yes_auto_migrates_without_input(self, migration, capsys):
        with patch("builtins.input") as stdin_input:
            update_cmd_config._check_and_apply_config_migration(assume_yes=True)
        stdin_input.assert_not_called()
        assert migration == [{"interactive": False, "quiet": False}]
        assert "--yes: auto-applying config migration" in capsys.readouterr().out

    def test_no_yes_flag_still_prompts_in_tty(self, migration):
        with (
            patch("builtins.input", return_value="n") as stdin_input,
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            update_cmd_config._check_and_apply_config_migration(assume_yes=False)
        stdin_input.assert_called_once()
        assert "configure them now" in stdin_input.call_args.args[0]
        assert migration == []


class TestUnicodeDecodeErrorInUpdatePrompts:
    def test_unicode_decode_error_in_tty_skips_and_prints_hint(self, migration, capsys):
        with (
            patch("builtins.input", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            update_cmd_config._check_and_apply_config_migration(assume_yes=False)
        assert migration == []
        assert "hermes config migrate" in capsys.readouterr().out

    @pytest.mark.parametrize("error", [EOFError(), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")])
    def test_unreadable_stash_prompt_keeps_local_changes(self, tmp_path, capsys, error):
        from hermes_cli.update_cmd import _restore_stashed_changes

        with patch("builtins.input", side_effect=error):
            assert not _restore_stashed_changes(["git"], tmp_path, "stash@{0}", prompt_user=True)
        out = capsys.readouterr().out
        assert "Skipped restoring local changes" in out
        assert "git stash apply stash@{0}" in out

    def test_upstream_remote_prompt_unicode_decode_error_falls_through_to_skip(self, tmp_path):
        from hermes_cli.update_cmd import _sync_with_upstream_if_needed

        with (
            patch("hermes_cli.update_cmd._has_upstream_remote", return_value=False),
            patch("hermes_cli.update_cmd._should_skip_upstream_prompt", return_value=False),
            patch("hermes_cli.update_cmd._add_upstream_remote") as add,
            patch("builtins.input", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")) as stdin_input,
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            assert not _sync_with_upstream_if_needed(["git"], tmp_path)
        stdin_input.assert_called_once()
        add.assert_not_called()
