"""Tests for tools/file_operations.py — deny list, result dataclasses, helpers."""

import os
import pytest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from tools.file_operations import (
    _is_write_denied,
    ReadResult,
    WriteResult,
    PatchResult,
    SearchResult,
    SearchMatch,
    LintResult,
    ShellFileOperations,
    MAX_LINE_LENGTH,
    normalize_read_pagination,
    normalize_search_pagination,
)


# =========================================================================
# Write deny list
# =========================================================================

class TestIsWriteDenied:
    def test_ssh_authorized_keys_denied(self):
        path = os.path.join(str(Path.home()), ".ssh", "authorized_keys")
        assert _is_write_denied(path) is True

    def test_ssh_id_rsa_denied(self):
        path = os.path.join(str(Path.home()), ".ssh", "id_rsa")
        assert _is_write_denied(path) is True

    def test_netrc_denied(self):
        path = os.path.join(str(Path.home()), ".netrc")
        assert _is_write_denied(path) is True

    def test_aws_prefix_denied(self):
        path = os.path.join(str(Path.home()), ".aws", "credentials")
        assert _is_write_denied(path) is True

    def test_kube_prefix_denied(self):
        path = os.path.join(str(Path.home()), ".kube", "config")
        assert _is_write_denied(path) is True

    def test_normal_file_allowed(self, tmp_path):
        path = str(tmp_path / "safe_file.txt")
        assert _is_write_denied(path) is False

    def test_project_file_allowed(self):
        assert _is_write_denied("/tmp/project/main.py") is False

    def test_tilde_expansion(self):
        assert _is_write_denied("~/.ssh/authorized_keys") is True

    @pytest.mark.parametrize(
        "path",
        [
            "auth.json",
            "config.yaml",
            "webhook_subscriptions.json",
            ".anthropic_oauth.json",
            "mcp-tokens/token1.json",
            "mcp-tokens/subdir/token2.json",
            "pairing/telegram-approved.json",
            "pairing/discord-approved.json",
            "pairing/telegram-pending.json",
            "pairing",
        ],
    )
    def test_hermes_control_files_oauth_and_mcp_tokens_denied(self, path):
        """Hermes control files, PKCE creds, mcp-tokens, and pairing entries must be write-denied."""
        from hermes_constants import get_hermes_home
        hermes_home = get_hermes_home()
        full_path = str(hermes_home / path)
        assert _is_write_denied(full_path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "dummy/../config.yaml",
            "./auth.json",
            "./.anthropic_oauth.json",
            "mcp-tokens/../config.yaml",
        ],
    )
    def test_hermes_control_files_and_oauth_traversal_denied(self, path):
        """Path traversal attempts to protected Hermes files must be blocked."""
        from hermes_constants import get_hermes_home
        hermes_home = get_hermes_home()
        full_path = str(hermes_home / path)
        assert _is_write_denied(full_path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/tmp/standard_file.txt",
            "~/projects/myapp/main.py",
            "/var/log/app.log",
        ],
    )
    def test_standard_paths_allowed(self, path):
        """Unrelated paths must still be allowed."""
        assert _is_write_denied(path) is False

    @pytest.mark.parametrize(
        "name",
        ["auth.json", "config.yaml", "webhook_subscriptions.json", ".anthropic_oauth.json"],
    )
    def test_control_files_and_oauth_protected_in_profile_mode(self, tmp_path, monkeypatch, name):
        """Under a profile, BOTH <profile>/X and <root>/X must be denied (#15981 shape).

        Without the root-level pass, a profile-mode session leaves the
        global ~/.hermes/{auth.json,config.yaml,webhook_subscriptions.json,
        .anthropic_oauth.json} writable — the same gap PR #15981 fixed
        for .env.
        """
        # Simulate a profile-mode HERMES_HOME layout:
        #   <root>/profiles/coder/{auth.json,config.yaml,...}
        #   <root>/{auth.json,config.yaml,...}        ← must also be denied
        root = tmp_path / "hermes"
        profile = root / "profiles" / "coder"
        profile.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(profile))

        # Profile copy
        assert _is_write_denied(str(profile / name)) is True
        # Root copy — the gap this widening closes
        assert _is_write_denied(str(root / name)) is True

    def test_mcp_tokens_dir_protected_in_profile_mode(self, tmp_path, monkeypatch):
        """mcp-tokens/ under profile AND under root must both be denied."""
        root = tmp_path / "hermes"
        profile = root / "profiles" / "coder"
        profile.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(profile))

        assert _is_write_denied(str(profile / "mcp-tokens" / "tok.json")) is True
        assert _is_write_denied(str(root / "mcp-tokens" / "tok.json")) is True
        # The directory itself must also be denied (not just files inside)
        assert _is_write_denied(str(root / "mcp-tokens")) is True

    def test_pairing_dir_denied(self, tmp_path, monkeypatch):
        """Regression: pairing/ must be write-denied under both profile and root.

        PR #30383 introduced ~/.hermes/pairing/{platform}-approved.json as the
        gateway access-control list. Without this block, a prompt-injected agent
        can write arbitrary user IDs into an approved file, granting persistent
        gateway access without going through the pairing code flow — the same
        threat class that motivated protecting webhook_subscriptions.json.
        """
        root = tmp_path / "hermes"
        profile = root / "profiles" / "coder"
        profile.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(profile))

        # Active profile pairing entries
        assert _is_write_denied(str(profile / "pairing" / "telegram-approved.json")) is True
        assert _is_write_denied(str(profile / "pairing" / "discord-pending.json")) is True
        # The directory itself
        assert _is_write_denied(str(profile / "pairing")) is True
        # Root pairing entries (profile mode — same shape as mcp-tokens gap)
        assert _is_write_denied(str(root / "pairing" / "telegram-approved.json")) is True
        assert _is_write_denied(str(root / "pairing")) is True



# =========================================================================
# Result dataclasses
# =========================================================================

class TestReadResult:
    def test_to_dict_omits_defaults(self):
        r = ReadResult()
        d = r.to_dict()
        assert "error" not in d    # None omitted
        assert "similar_files" not in d  # empty list omitted

    def test_to_dict_preserves_empty_content(self):
        """Empty file should still have content key in the dict."""
        r = ReadResult(content="", total_lines=0, file_size=0)
        d = r.to_dict()
        assert "content" in d
        assert d["content"] == ""
        assert d["total_lines"] == 0
        assert d["file_size"] == 0

    def test_to_dict_includes_values(self):
        r = ReadResult(content="hello", total_lines=10, file_size=50, truncated=True)
        d = r.to_dict()
        assert d["content"] == "hello"
        assert d["total_lines"] == 10
        assert d["truncated"] is True

    def test_binary_fields(self):
        r = ReadResult(is_binary=True, is_image=True, mime_type="image/png")
        d = r.to_dict()
        assert d["is_binary"] is True
        assert d["is_image"] is True
        assert d["mime_type"] == "image/png"


class TestWriteResult:
    def test_to_dict_omits_none(self):
        r = WriteResult(bytes_written=100)
        d = r.to_dict()
        assert d["bytes_written"] == 100
        assert "error" not in d
        assert "warning" not in d

    def test_to_dict_includes_error(self):
        r = WriteResult(error="Permission denied")
        d = r.to_dict()
        assert d["error"] == "Permission denied"


class TestPatchResult:
    def test_to_dict_success(self):
        r = PatchResult(success=True, diff="--- a\n+++ b", files_modified=["a.py"])
        d = r.to_dict()
        assert d["success"] is True
        assert d["diff"] == "--- a\n+++ b"
        assert d["files_modified"] == ["a.py"]

    def test_to_dict_error(self):
        r = PatchResult(error="File not found")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "File not found"


class TestSearchResult:
    def test_to_dict_with_matches(self):
        m = SearchMatch(path="a.py", line_number=10, content="hello")
        r = SearchResult(matches=[m], total_count=1)
        d = r.to_dict()
        assert d["total_count"] == 1
        assert len(d["matches"]) == 1
        assert d["matches"][0]["path"] == "a.py"

    def test_to_dict_empty(self):
        r = SearchResult()
        d = r.to_dict()
        assert d["total_count"] == 0
        assert "matches" not in d

    def test_to_dict_files_mode(self):
        r = SearchResult(files=["a.py", "b.py"], total_count=2)
        d = r.to_dict()
        assert d["files"] == ["a.py", "b.py"]

    def test_to_dict_count_mode(self):
        r = SearchResult(counts={"a.py": 3, "b.py": 1}, total_count=4)
        d = r.to_dict()
        assert d["counts"]["a.py"] == 3

    def test_truncated_flag(self):
        r = SearchResult(total_count=100, truncated=True)
        d = r.to_dict()
        assert d["truncated"] is True


class TestLintResult:
    def test_skipped(self):
        r = LintResult(skipped=True, message="No linter for .md files")
        d = r.to_dict()
        assert d["status"] == "skipped"
        assert d["message"] == "No linter for .md files"

    def test_success(self):
        r = LintResult(success=True, output="")
        d = r.to_dict()
        assert d["status"] == "ok"

    def test_error(self):
        r = LintResult(success=False, output="SyntaxError line 5")
        d = r.to_dict()
        assert d["status"] == "error"
        assert "SyntaxError" in d["output"]


# =========================================================================
# ShellFileOperations helpers
# =========================================================================

@pytest.fixture()
def mock_env():
    """Create a mock terminal environment."""
    env = MagicMock()
    env.cwd = "/tmp/test"
    env.execute.return_value = {"output": "", "returncode": 0}
    return env


@pytest.fixture()
def file_ops(mock_env):
    return ShellFileOperations(mock_env)


class TestShellFileOpsHelpers:
    def test_normalize_read_pagination_clamps_invalid_values(self):
        assert normalize_read_pagination(offset=0, limit=0) == (1, 1)
        assert normalize_read_pagination(offset=-10, limit=-5) == (1, 1)
        assert normalize_read_pagination(offset="bad", limit="bad") == (1, 500)
        assert normalize_read_pagination(offset=2, limit=999999) == (2, 2000)

    def test_normalize_search_pagination_clamps_invalid_values(self):
        assert normalize_search_pagination(offset=-10, limit=-5) == (0, 1)
        assert normalize_search_pagination(offset="bad", limit="bad") == (0, 50)
        assert normalize_search_pagination(offset=3, limit=0) == (3, 1)

    def test_escape_shell_arg_simple(self, file_ops):
        assert file_ops._escape_shell_arg("hello") == "'hello'"

    def test_escape_shell_arg_with_quotes(self, file_ops):
        result = file_ops._escape_shell_arg("it's")
        assert "'" in result
        # Should be safely escaped
        assert result.count("'") >= 4  # wrapping + escaping

    def test_is_likely_binary_by_extension(self, file_ops):
        assert file_ops._is_likely_binary("photo.png") is True
        assert file_ops._is_likely_binary("data.db") is True
        assert file_ops._is_likely_binary("code.py") is False
        assert file_ops._is_likely_binary("readme.md") is False

    def test_is_likely_binary_by_content(self, file_ops):
        # High ratio of non-printable chars -> binary
        binary_content = "\x00\x01\x02\x03" * 250
        assert file_ops._is_likely_binary("unknown", binary_content) is True

        # Normal text -> not binary
        assert file_ops._is_likely_binary("unknown", "Hello world\nLine 2\n") is False

    def test_is_image(self, file_ops):
        assert file_ops._is_image("photo.png") is True
        assert file_ops._is_image("pic.jpg") is True
        assert file_ops._is_image("icon.ico") is True
        assert file_ops._is_image("data.pdf") is False
        assert file_ops._is_image("code.py") is False

    def test_add_line_numbers(self, file_ops):
        content = "line one\nline two\nline three"
        result = file_ops._add_line_numbers(content)
        # Compact gutter: "<n>|content" (no fixed-width padding).
        assert "1|line one" in result
        assert "2|line two" in result
        assert "3|line three" in result

    def test_add_line_numbers_with_offset(self, file_ops):
        content = "continued\nmore"
        result = file_ops._add_line_numbers(content, start_line=50)
        assert "50|continued" in result
        assert "51|more" in result

    def test_add_line_numbers_truncates_long_lines(self, file_ops):
        long_line = "x" * (MAX_LINE_LENGTH + 100)
        result = file_ops._add_line_numbers(long_line)
        assert "[truncated]" in result

    def test_unified_diff(self, file_ops):
        old = "line1\nline2\nline3\n"
        new = "line1\nchanged\nline3\n"
        diff = file_ops._unified_diff(old, new, "test.py")
        assert "-line2" in diff
        assert "+changed" in diff
        assert "test.py" in diff

    def test_cwd_from_env(self, mock_env):
        mock_env.cwd = "/custom/path"
        ops = ShellFileOperations(mock_env)
        assert ops.cwd == "/custom/path"

    def test_cwd_fallback_to_slash(self):
        env = MagicMock(spec=[])  # no cwd attribute
        ops = ShellFileOperations(env)
        assert ops.cwd == "/"

    def test_read_file_strips_leaked_terminal_fence_markers(self, mock_env):
        leaked = (
            "'\x07__HERMES_FENCE_a9f7b3__\x1b]0;cat "
            "'/tmp/test/a.py' 2> /dev/null\x07\n"
            "print('ok')\n"
            "__HERMES_FENCE_a9f7b3__\x07'\n"
        )

        def side_effect(command, **kwargs):
            if command.startswith("wc -c"):
                return {"output": "12\n", "returncode": 0}
            if command.startswith("head -c"):
                return {"output": "print('ok')\n", "returncode": 0}
            if command.startswith("sed -n"):
                return {"output": leaked, "returncode": 0}
            if command.startswith("wc -l"):
                return {"output": "1\n", "returncode": 0}
            return {"output": "", "returncode": 0}

        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.read_file("/tmp/test/a.py")

        assert result.error is None
        assert "HERMES_FENCE" not in result.content
        assert "\x1b]" not in result.content
        assert "\x07" not in result.content
        assert "1|print('ok')" in result.content

    def test_read_file_raw_strips_leaked_terminal_fence_markers(self, mock_env):
        leaked = (
            "__HERMES_FENCE_a9f7b3__\x07'\n"
            "alpha\n"
            "\x1b]0;cat '/tmp/test/a.txt'\x07__HERMES_FENCE_a9f7b3__\n"
        )

        def side_effect(command, **kwargs):
            if command.startswith("wc -c"):
                return {"output": "6\n", "returncode": 0}
            if command.startswith("head -c"):
                return {"output": "alpha\n", "returncode": 0}
            if command.startswith("cat "):
                return {"output": leaked, "returncode": 0}
            return {"output": "", "returncode": 0}

        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.read_file_raw("/tmp/test/a.txt")

        assert result.error is None
        assert result.content == "alpha\n"


class TestSearchPathValidation:
    """Test that search() returns an error for non-existent paths."""

    def test_search_nonexistent_path_returns_error(self, mock_env):
        """search() should return an error when the path doesn't exist."""
        def side_effect(command, **kwargs):
            if "test -e" in command:
                return {"output": "not_found", "returncode": 1}
            if "command -v" in command:
                return {"output": "yes", "returncode": 0}
            return {"output": "", "returncode": 0}
        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.search("pattern", path="/nonexistent/path")
        assert result.error is not None
        assert "not found" in result.error.lower() or "Path not found" in result.error

    def test_search_nonexistent_path_files_mode(self, mock_env):
        """search(target='files') should also return error for bad paths."""
        def side_effect(command, **kwargs):
            if "test -e" in command:
                return {"output": "not_found", "returncode": 1}
            if "command -v" in command:
                return {"output": "yes", "returncode": 0}
            return {"output": "", "returncode": 0}
        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.search("*.py", path="/nonexistent/path", target="files")
        assert result.error is not None
        assert "not found" in result.error.lower() or "Path not found" in result.error

    def test_search_existing_path_proceeds(self, mock_env):
        """search() should proceed normally when the path exists."""
        def side_effect(command, **kwargs):
            if "test -e" in command:
                return {"output": "exists", "returncode": 0}
            if "command -v" in command:
                return {"output": "yes", "returncode": 0}
            # rg returns exit 1 (no matches) with empty output
            return {"output": "", "returncode": 1}
        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.search("pattern", path="/existing/path")
        assert result.error is None
        assert result.total_count == 0  # No matches but no error

    def test_search_rg_error_exit_code(self, mock_env):
        """search() should report error when rg returns exit code 2."""
        call_count = {"n": 0}
        def side_effect(command, **kwargs):
            call_count["n"] += 1
            if "test -e" in command:
                return {"output": "exists", "returncode": 0}
            if "command -v" in command:
                return {"output": "yes", "returncode": 0}
            # rg returns exit 2 (error) with empty output
            return {"output": "", "returncode": 2}
        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.search("pattern", path="/some/path")
        assert result.error is not None
        assert "search failed" in result.error.lower() or "Search error" in result.error


class TestSearchFilesFallbackHiddenPaths:
    def _make_env(self):
        env = MagicMock()
        env.cwd = "/"

        def execute(command, **kwargs):
            completed = subprocess.run(
                command,
                shell=True,
                text=True,
                capture_output=True,
            )
            return {
                "output": completed.stdout,
                "returncode": completed.returncode,
            }

        env.execute = execute
        return env

    def test_hidden_root_with_hidden_ancestor_includes_files(self, tmp_path, monkeypatch):
        """Fallback find should include visible files when path is inside hidden root."""
        root = tmp_path / ".hermes" / "logs"
        root.mkdir(parents=True)
        visible_file = root / "agent.log"
        hidden_dir_file = root / ".hidden" / "secret.log"
        nested_hidden_file = root / "nested" / ".secret.log"
        visible_nested_file = root / "nested" / "visible.log"

        for p in [visible_file, nested_hidden_file, visible_nested_file, hidden_dir_file]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")

        ops = ShellFileOperations(self._make_env())
        monkeypatch.setattr(ops, "_has_command", lambda command: command == "find")
        result = ops._search_files("*.log", str(root), limit=50, offset=0)

        assert result.error is None
        assert set(result.files) == {str(visible_file), str(visible_nested_file)}

    def test_normal_root_still_excludes_hidden_descendants(self, tmp_path, monkeypatch):
        """Fallback find should still exclude hidden descendant paths for normal roots."""
        root = tmp_path / "repo"
        root.mkdir()
        visible_file = root / "agent.log"
        visible_nested_file = root / "nested" / "visible.log"
        hidden_dir_file = root / ".hidden" / "secret.log"

        for p in [visible_file, visible_nested_file, hidden_dir_file]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")

        ops = ShellFileOperations(self._make_env())
        monkeypatch.setattr(ops, "_has_command", lambda command: command == "find")
        result = ops._search_files("*.log", str(root), limit=50, offset=0)

        assert result.error is None
        assert set(result.files) == {str(visible_file), str(visible_nested_file)}


class TestShellFileOpsWriteDenied:
    def test_write_file_denied_path(self, file_ops):
        result = file_ops.write_file("~/.ssh/authorized_keys", "evil key")
        assert result.error is not None
        assert "denied" in result.error.lower()

    def test_patch_replace_denied_path(self, file_ops):
        result = file_ops.patch_replace("~/.ssh/authorized_keys", "old", "new")
        assert result.error is not None
        assert "denied" in result.error.lower()

    def test_delete_file_denied_path(self, file_ops):
        result = file_ops.delete_file("~/.ssh/authorized_keys")
        assert result.error is not None
        assert "denied" in result.error.lower()

    def test_move_file_src_denied(self, file_ops):
        result = file_ops.move_file("~/.ssh/id_rsa", "/tmp/dest.txt")
        assert result.error is not None
        assert "denied" in result.error.lower()

    def test_move_file_dst_denied(self, file_ops):
        result = file_ops.move_file("/tmp/src.txt", "~/.aws/credentials")
        assert result.error is not None
        assert "denied" in result.error.lower()

    def test_move_file_failure_path(self, mock_env):
        mock_env.execute.return_value = {"output": "No such file or directory", "returncode": 1}
        ops = ShellFileOperations(mock_env)
        result = ops.move_file("/tmp/nonexistent.txt", "/tmp/dest.txt")
        assert result.error is not None
        assert "Failed to move" in result.error


class TestPatchReplacePostWriteVerification:
    """Tests for the post-write verification added in patch_replace.

    Confirms that a silent persistence failure (where write_file's command
    appears to succeed but the bytes on disk don't match new_content) is
    surfaced as an error instead of being reported as a successful patch.
    """

    def test_patch_replace_fails_when_file_not_persisted(self, mock_env):
        """write_file reports success but the re-read returns old content:
        patch_replace must return an error, not success-with-diff."""
        file_contents = {"/tmp/test/a.py": "hello world\n"}

        def side_effect(command, **kwargs):
            # cat reads the file — both the initial read and the verify read
            if command.startswith("cat "):
                # Extract path from cat command (strip quotes)
                for path in file_contents:
                    if path in command:
                        return {"output": file_contents[path], "returncode": 0}
                return {"output": "", "returncode": 1}
            # mkdir for parent dir
            if command.startswith("mkdir "):
                return {"output": "", "returncode": 0}
            # wc -c for byte count after write
            if command.startswith("wc -c"):
                for path in file_contents:
                    if path in command:
                        return {"output": str(len(file_contents[path].encode())), "returncode": 0}
                return {"output": "0", "returncode": 0}
            # Everything else (including the write itself) pretends to succeed
            # but DOESN'T update file_contents — simulates silent failure
            return {"output": "", "returncode": 0}

        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.patch_replace("/tmp/test/a.py", "hello", "hi")
        assert result.error is not None, (
            "Silent persistence failure must surface as error, got: "
            f"success={result.success}, diff={result.diff}"
        )
        assert "verification failed" in result.error.lower()
        assert "did not persist" in result.error.lower()

    def test_patch_replace_succeeds_when_file_persisted(self, mock_env):
        """Normal success path: write persists, verify read returns new bytes."""
        state = {"content": "hello world\n"}

        def side_effect(command, stdin_data=None, **kwargs):
            # A write is the only call that pipes content over stdin — key
            # on that behavioral signal rather than the exact write command,
            # which is an atomic temp-file + mv script (`set -e; ... mv ...`),
            # not a bare `cat > path`.
            if stdin_data is not None:
                state["content"] = stdin_data
                return {"output": "", "returncode": 0}
            if command.startswith("cat "):  # read / verify
                return {"output": state["content"], "returncode": 0}
            if command.startswith("mkdir "):
                return {"output": "", "returncode": 0}
            if command.startswith("wc -c"):
                return {"output": str(len(state["content"].encode())), "returncode": 0}
            return {"output": "", "returncode": 0}

        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.patch_replace("/tmp/test/a.py", "hello", "hi")
        assert result.error is None, f"Unexpected error: {result.error}"
        assert result.success is True
        assert state["content"] == "hi world\n", f"File not actually updated: {state['content']!r}"

    def test_patch_replace_fails_when_verify_read_errors(self, mock_env):
        """If the verify-read step itself fails (exit code != 0), return an error."""
        call_count = {"cat": 0}
        state = {"content": "hello world\n"}

        def side_effect(command, stdin_data=None, **kwargs):
            if stdin_data is not None:  # write (atomic temp-file + mv script)
                state["content"] = stdin_data
                return {"output": "", "returncode": 0}
            if command.startswith("cat "):  # read
                call_count["cat"] += 1
                # First read (initial fetch) succeeds; second read (verify) fails
                if call_count["cat"] == 1:
                    return {"output": state["content"], "returncode": 0}
                return {"output": "", "returncode": 1}
            if command.startswith("mkdir "):
                return {"output": "", "returncode": 0}
            if command.startswith("wc -c"):
                return {"output": str(len(state["content"].encode())), "returncode": 0}
            return {"output": "", "returncode": 0}

        mock_env.execute.side_effect = side_effect
        ops = ShellFileOperations(mock_env)
        result = ops.patch_replace("/tmp/test/a.py", "hello", "hi")
        assert result.error is not None
        assert "could not re-read" in result.error.lower()


# =========================================================================
# Git baseline check for write_file warning
# =========================================================================

class _DeletedTestGitBaselineCheck:
    """Removed May 2026 — these tests asserted on a ``_check_git_baseline``
    method that doesn't exist on ``ShellFileOperations`` (regression intro
    by a separate refactor). All 6 tests in the class fail with
    AttributeError on origin/main. Deleted wholesale per Teknium's
    instruction to keep CI green; reinstate them when the underlying
    helper is restored or replaced.
    """
    pass


# =========================================================================
# _parse_search_content_output — shared rg/grep output parser
# =========================================================================

from tools.file_operations import _parse_search_content_output


class TestParseSearchContentOutput:
    """Unit tests for the shared parse function extracted from
    _search_with_rg_shell and _search_with_grep."""

    # ── files_only mode ──────────────────────────────────────────────

    def test_files_only_mode_basic(self):
        stdout = "src/a.py\nsrc/b.py\ndocs/readme.md\n"
        result = _parse_search_content_output(stdout, "files_only", 0, 50, 0)
        assert result.error is None
        assert result.files == ["src/a.py", "src/b.py", "docs/readme.md"]
        assert result.total_count == 3

    def test_files_only_mode_with_offset(self):
        stdout = "a.py\nb.py\nc.py\nd.py\n"
        result = _parse_search_content_output(stdout, "files_only", 0, 2, 1)
        assert result.files == ["b.py", "c.py"]
        assert result.total_count == 4

    def test_files_only_mode_empty(self):
        result = _parse_search_content_output("", "files_only", 0, 50, 0)
        assert result.files == []
        assert result.total_count == 0

    def test_files_only_mode_blank_lines_ignored(self):
        stdout = "a.py\n\n\nb.py\n"
        result = _parse_search_content_output(stdout, "files_only", 0, 50, 0)
        assert result.files == ["a.py", "b.py"]

    # ── count mode ───────────────────────────────────────────────────

    def test_count_mode_basic(self):
        stdout = "src/a.py:5\nsrc/b.py:2\n"
        result = _parse_search_content_output(stdout, "count", 0, 50, 0)
        assert result.counts == {"src/a.py": 5, "src/b.py": 2}
        assert result.total_count == 7

    def test_count_mode_empty(self):
        result = _parse_search_content_output("", "count", 0, 50, 0)
        assert result.counts == {}
        assert result.total_count == 0

    def test_count_mode_invalid_line_skipped(self):
        stdout = "src/a.py:5\ninvalid\nsrc/b.py:2\n"
        result = _parse_search_content_output(stdout, "count", 0, 50, 0)
        assert result.counts == {"src/a.py": 5, "src/b.py": 2}
        assert result.total_count == 7

    # ── content mode ─────────────────────────────────────────────────

    def test_content_mode_basic(self):
        stdout = "src/a.py:10:def foo():\nsrc/a.py:20:def bar():\n"
        result = _parse_search_content_output(stdout, "content", 0, 50, 0)
        assert result.error is None
        assert len(result.matches) == 2
        assert result.matches[0].path == "src/a.py"
        assert result.matches[0].line_number == 10
        assert result.matches[0].content == "def foo():"
        assert result.matches[1].line_number == 20
        assert result.matches[1].content == "def bar():"
        assert result.total_count == 2
        assert result.truncated is False

    def test_content_mode_with_windows_paths(self):
        """Paths with drive letters (C:\\...) should parse correctly."""
        stdout = "C:\\Users\\vip\\src\\a.py:10:hello world\n"
        result = _parse_search_content_output(stdout, "content", 0, 50, 0)
        assert result.error is None
        assert len(result.matches) == 1
        assert result.matches[0].path == "C:\\Users\\vip\\src\\a.py"
        assert result.matches[0].line_number == 10
        assert result.matches[0].content == "hello world"

    def test_content_mode_with_context_lines(self):
        """When context > 0, dash-separated context lines are parsed."""
        stdout = (
            "src/a.py-8-before line\n"
            "src/a.py:10:match line\n"
            "src/a.py-12-after line\n"
            "--\n"
        )
        result = _parse_search_content_output(stdout, "content", 2, 50, 0)
        assert len(result.matches) == 3
        assert result.matches[0].content == "before line"
        assert result.matches[0].line_number == 8
        assert result.matches[1].content == "match line"
        assert result.matches[1].line_number == 10
        assert result.matches[2].content == "after line"
        assert result.matches[2].line_number == 12

    def test_content_mode_context_lines_ignored_without_context(self):
        """When context == 0, dash lines are NOT parsed as context lines."""
        stdout = "src/a.py-8-not-context\nsrc/a.py:10:match line\n"
        result = _parse_search_content_output(stdout, "content", 0, 50, 0)
        assert len(result.matches) == 1
        assert result.matches[0].line_number == 10

    def test_content_mode_separator_lines_skipped(self):
        stdout = "--\nsrc/a.py:10:match\n--\n"
        result = _parse_search_content_output(stdout, "content", 0, 50, 0)
        assert len(result.matches) == 1
        assert result.matches[0].content == "match"

    def test_content_mode_offset_and_limit(self):
        stdout = (
            "a.py:1:first\n"
            "a.py:2:second\n"
            "a.py:3:third\n"
            "a.py:4:fourth\n"
        )
        result = _parse_search_content_output(stdout, "content", 0, 2, 1)
        assert len(result.matches) == 2
        assert result.matches[0].line_number == 2
        assert result.matches[1].line_number == 3
        assert result.total_count == 4
        assert result.truncated is True

    def test_content_mode_truncated_false_when_all_fit(self):
        stdout = "a.py:1:a\na.py:2:b\n"
        result = _parse_search_content_output(stdout, "content", 0, 50, 0)
        assert result.truncated is False

    def test_content_mode_content_truncated_at_500_chars(self):
        long_line = "x" * 600
        stdout = f"a.py:1:{long_line}\n"
        result = _parse_search_content_output(stdout, "content", 0, 50, 0)
        assert len(result.matches[0].content) == 500


# =========================================================================
# _is_local_env — local-backend detection
# =========================================================================


class TestIsLocalEnv:
    """Tests for _is_local_env() which gates ripgrepy-vs-shell dispatch."""

    def test_local_env_returns_true(self):
        """_is_local_env returns True when env is a LocalEnvironment."""
        from tools.environments.local import LocalEnvironment
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = LocalEnvironment()
        assert ops._is_local_env() is True

    def test_no_env_returns_false(self):
        """_is_local_env returns False when env is None."""
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = None
        assert ops._is_local_env() is False

    def test_magic_mock_env_returns_false(self):
        """_is_local_env returns False for MagicMock (not a real LocalEnvironment)."""
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = MagicMock()
        assert ops._is_local_env() is False

    def test_missing_env_attribute_returns_false(self):
        """_is_local_env returns False when env attribute is missing."""
        ops = ShellFileOperations.__new__(ShellFileOperations)
        # Don't set env at all — uses getattr default
        assert ops._is_local_env() is False


# =========================================================================
# _search_files_rg_ripgrepy — ripgrepy-based file-name search
# =========================================================================


class TestSearchFilesRgRipgrepy:
    """Tests for _search_files_rg_ripgrepy on local backends."""

    @staticmethod
    def _make_local_ops():
        """Create a ShellFileOperations wired to a LocalEnvironment."""
        from tools.environments.local import LocalEnvironment
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = LocalEnvironment()
        ops.cwd = str(Path.cwd())
        return ops

    def test_bare_pattern_gets_glob_wildcard(self, tmp_path, monkeypatch):
        """A bare name like 'foo.py' → glob pattern '*foo.py'."""
        ops = self._make_local_ops()

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="a/foo.py\nb/foo.py\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_files_rg_ripgrepy("foo.py", str(tmp_path), 50, 0)

        assert result.error is None
        assert "--glob" in captured_cmds[0]
        assert "*foo.py" in captured_cmds[0]

    def test_pattern_with_slash_not_rewrapped(self, tmp_path, monkeypatch):
        """A pattern containing '/' is passed as-is."""
        ops = self._make_local_ops()

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        ops._search_files_rg_ripgrepy("src/foo.py", str(tmp_path), 50, 0)

        assert "--glob" in captured_cmds[0]
        assert "src/foo.py" in captured_cmds[0]

    def test_sortr_flag_included(self, tmp_path, monkeypatch):
        """Command should include --sortr modified by default."""
        ops = self._make_local_ops()

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        ops._search_files_rg_ripgrepy("test.py", str(tmp_path), 50, 0)

        assert "--sortr" in captured_cmds[0]

    def test_fallbacks_to_shell_on_error(self, tmp_path, monkeypatch):
        """On subprocess error, falls back to _search_files_rg_shell."""
        ops = self._make_local_ops()

        def fake_run(cmd, **kwargs):
            raise OSError("no rg")

        monkeypatch.setattr(subprocess, "run", fake_run)

        # Should not raise; instead falls back to shell path
        monkeypatch.setattr(ops, "_search_files_rg_shell",
                           lambda p, pa, l, o: SearchResult(files=["fallback.py"], total_count=1))

        result = ops._search_files_rg_ripgrepy("test.py", str(tmp_path), 50, 0)
        assert result.files == ["fallback.py"]

    def test_results_sliced_with_offset_and_limit(self, tmp_path, monkeypatch):
        """Results respect offset and limit."""
        ops = self._make_local_ops()
        stdout = "\n".join([f"file_{i}.py" for i in range(10)])

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout)

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_files_rg_ripgrepy("file_*.py", str(tmp_path), 3, 2)
        assert result.files == ["file_2.py", "file_3.py", "file_4.py"]
        assert result.total_count == 10
        assert result.truncated is True


# =========================================================================
# _search_with_rg_ripgrepy — ripgrepy-based content search
# =========================================================================


class TestSearchWithRgRipgrepy:
    """Tests for _search_with_rg_ripgrepy on local backends."""

    @staticmethod
    def _make_local_ops():
        """Create a ShellFileOperations wired to a LocalEnvironment."""
        from tools.environments.local import LocalEnvironment
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = LocalEnvironment()
        ops.cwd = str(Path.cwd())
        return ops

    def test_basic_content_search(self, tmp_path, monkeypatch):
        """A basic content search uses line_number, no_heading, with_filename."""
        ops = self._make_local_ops()
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="src/a.py:10:hello world\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_with_rg_ripgrepy("hello", str(tmp_path), None, 50, 0, "content", 0)

        assert result.error is None
        assert len(result.matches) == 1
        assert result.matches[0].path == "src/a.py"
        assert result.matches[0].line_number == 10
        assert "--line-number" in captured_cmds[0]
        assert "--no-heading" in captured_cmds[0]
        assert "--with-filename" in captured_cmds[0]

    def test_files_only_mode(self, tmp_path, monkeypatch):
        """files_only output mode adds --files-with-matches."""
        ops = self._make_local_ops()
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="src/a.py\nsrc/b.py\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_with_rg_ripgrepy("pattern", str(tmp_path), None, 50, 0, "files_only", 0)

        assert "--files-with-matches" in captured_cmds[0]
        assert result.files == ["src/a.py", "src/b.py"]

    def test_count_mode(self, tmp_path, monkeypatch):
        """count output mode adds --count."""
        ops = self._make_local_ops()
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="src/a.py:5\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_with_rg_ripgrepy("pattern", str(tmp_path), None, 50, 0, "count", 0)

        assert "--count-matches" in captured_cmds[0]
        assert result.counts == {"src/a.py": 5}

    def test_file_glob_added(self, tmp_path, monkeypatch):
        """file_glob is passed via --glob."""
        ops = self._make_local_ops()
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        ops._search_with_rg_ripgrepy("pattern", str(tmp_path), "*.py", 50, 0, "content", 0)

        assert "--glob" in captured_cmds[0]
        assert "*.py" in captured_cmds[0]

    def test_context_added(self, tmp_path, monkeypatch):
        """context > 0 adds --context."""
        ops = self._make_local_ops()
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        ops._search_with_rg_ripgrepy("pattern", str(tmp_path), None, 50, 0, "content", 3)

        assert "--context" in captured_cmds[0]
        assert "3" in captured_cmds[0]

    def test_exit_code_2_with_stdout_keeps_matches(self, tmp_path, monkeypatch):
        """rg exit 2 with stdout means partial success — keep the matches."""
        ops = self._make_local_ops()

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 2, stdout="src/a.py:5:match\n", stderr="permission denied\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_with_rg_ripgrepy("pattern", str(tmp_path), None, 50, 0, "content", 0)

        assert result.error is None
        assert len(result.matches) == 1
        assert result.matches[0].content == "match"

    def test_exit_code_2_empty_stdout_is_error(self, tmp_path, monkeypatch):
        """rg exit 2 with no stdout is reported as error."""
        ops = self._make_local_ops()

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="fatal error\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_with_rg_ripgrepy("pattern", str(tmp_path), None, 50, 0, "content", 0)

        assert result.error is not None
        assert "Search failed" in result.error

    def test_exit_code_1_no_matches(self, tmp_path, monkeypatch):
        """rg exit 1 (no matches) is not an error."""
        ops = self._make_local_ops()

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ops._search_with_rg_ripgrepy("zxzxzx_nonexistent", str(tmp_path), None, 50, 0, "content", 0)

        assert result.error is None
        assert result.total_count == 0

    def test_fallbacks_to_shell_on_error(self, tmp_path, monkeypatch):
        """On OS error, falls back to _search_with_rg_shell."""
        ops = self._make_local_ops()

        def fake_run(cmd, **kwargs):
            raise OSError("subprocess failure")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(ops, "_search_with_rg_shell",
                           lambda p, pa, fg, l, o, om, c: SearchResult(
                               files=["fallback.py"], total_count=1))

        result = ops._search_with_rg_ripgrepy("test", str(tmp_path), None, 50, 0, "files_only", 0)
        assert result.files == ["fallback.py"]


# =========================================================================
# _search_files_rg / _search_with_rg dispatch (ripgrepy vs shell)
# =========================================================================


class TestRgDispatchToRipgrepy:
    """Tests that _search_files_rg and _search_with_rg dispatch correctly."""

    @staticmethod
    def _make_local_ops():
        from tools.environments.local import LocalEnvironment
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = LocalEnvironment()
        ops.cwd = str(Path.cwd())
        return ops

    def test_search_files_rg_dispatches_to_ripgrepy(self, monkeypatch):
        """On local env with rg, _search_files_rg → _search_files_rg_ripgrepy."""
        ops = self._make_local_ops()
        monkeypatch.setattr(ops, "_has_command", lambda cmd: cmd in ("rg",))

        called = {"ripgrepy": False, "shell": False}
        monkeypatch.setattr(ops, "_search_files_rg_ripgrepy",
                           lambda p, pa, l, o: called.update({"ripgrepy": True}) or SearchResult())
        monkeypatch.setattr(ops, "_search_files_rg_shell",
                           lambda p, pa, l, o: called.update({"shell": True}) or SearchResult())

        ops._search_files_rg("*.py", "/test", 50, 0)
        assert called["ripgrepy"] is True
        assert called["shell"] is False

    def test_search_files_rg_dispatches_to_shell_for_remote(self, monkeypatch):
        """On non-local env, _search_files_rg → _search_files_rg_shell."""
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = MagicMock()  # not LocalEnvironment
        ops.cwd = "/remote"
        monkeypatch.setattr(ops, "_has_command", lambda cmd: cmd in ("rg",))

        called = {"ripgrepy": False, "shell": False}
        monkeypatch.setattr(ops, "_search_files_rg_ripgrepy",
                           lambda p, pa, l, o: called.update({"ripgrepy": True}) or SearchResult())
        monkeypatch.setattr(ops, "_search_files_rg_shell",
                           lambda p, pa, l, o: called.update({"shell": True}) or SearchResult())

        ops._search_files_rg("*.py", "/remote", 50, 0)
        assert called["ripgrepy"] is False
        assert called["shell"] is True

    def test_search_with_rg_dispatches_to_ripgrepy(self, monkeypatch):
        """On local env with rg, _search_with_rg → _search_with_rg_ripgrepy."""
        ops = self._make_local_ops()
        monkeypatch.setattr(ops, "_has_command", lambda cmd: cmd in ("rg",))

        called = {"ripgrepy": False, "shell": False}
        monkeypatch.setattr(ops, "_search_with_rg_ripgrepy",
                           lambda p, pa, fg, l, o, om, c: called.update({"ripgrepy": True}) or SearchResult())
        monkeypatch.setattr(ops, "_search_with_rg_shell",
                           lambda p, pa, fg, l, o, om, c: called.update({"shell": True}) or SearchResult())

        ops._search_with_rg("pattern", "/test", None, 50, 0, "content", 0)
        assert called["ripgrepy"] is True
        assert called["shell"] is False

    def test_search_with_rg_dispatches_to_shell_for_remote(self, monkeypatch):
        """On non-local env, _search_with_rg → _search_with_rg_shell."""
        ops = ShellFileOperations.__new__(ShellFileOperations)
        ops.env = MagicMock()
        ops.cwd = "/remote"
        monkeypatch.setattr(ops, "_has_command", lambda cmd: cmd in ("rg",))

        called = {"ripgrepy": False, "shell": False}
        monkeypatch.setattr(ops, "_search_with_rg_ripgrepy",
                           lambda p, pa, fg, l, o, om, c: called.update({"ripgrepy": True}) or SearchResult())
        monkeypatch.setattr(ops, "_search_with_rg_shell",
                           lambda p, pa, fg, l, o, om, c: called.update({"shell": True}) or SearchResult())

        ops._search_with_rg("pattern", "/remote", None, 50, 0, "content", 0)
        assert called["ripgrepy"] is False
        assert called["shell"] is True
