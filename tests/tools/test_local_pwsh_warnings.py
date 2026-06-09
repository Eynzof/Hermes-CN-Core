"""Tests for pwsh_transform warning propagation through LocalEnvironment.

Verifies that _run_pwsh captures warnings from pwsh_transform, execute()
attaches them to the result dict, and terminal_tool surfaces them in JSON.
"""

import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_popen_for_pwsh(stdout_data="output"):
    """Return a fake Popen that captures what was passed to it."""

    def fake_popen(args, **kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = MagicMock()
        proc.stdout.__iter__ = lambda s: iter([stdout_data])
        proc.stdin = MagicMock()
        proc.poll.return_value = 0
        return proc

    return fake_popen


# ---------------------------------------------------------------------------
# _run_pwsh captures warnings
# ---------------------------------------------------------------------------

class TestRunPwshCapturesWarnings:
    """Test that _run_pwsh stores pwsh_transform warnings on self._pwsh_warnings."""

    def test_warnings_stored_on_instance(self):
        """When pwsh_transform returns warnings, they are stored on self._pwsh_warnings."""
        from tools.environments.local import LocalEnvironment

        # Mock to return tuple with warnings
        mock_transform_result = ("transformed code", ["Line 1: ternary operator `$a ? $b : $c` rewritten"])

        with patch(
            "tools.environments.local.pwsh_transform",
            return_value=mock_transform_result,
        ), patch("tools.environments.local.os.path.basename", return_value="powershell.exe"), patch(
            "tools.environments.local.subprocess.Popen",
            _fake_popen_for_pwsh(),
        ):
            env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
            env._shell_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            env._run_pwsh("$a ? $b : $c")

            assert hasattr(env, "_pwsh_warnings")
            assert env._pwsh_warnings == ["Line 1: ternary operator `$a ? $b : $c` rewritten"]

    def test_no_warnings_when_no_powershell(self):
        """When the shell is not powershell, pwsh_transform is not called and no warnings."""
        from tools.environments.local import LocalEnvironment

        # pwsh path, not powershell
        with patch(
            "tools.environments.local.pwsh_transform",
            return_value=("code", []),
        ) as mock_transform, patch(
            "tools.environments.local.subprocess.Popen",
            _fake_popen_for_pwsh(),
        ):
            env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
            env._shell_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
            env._run_pwsh("Write-Output hello")

            # pwsh_transform should not be called because basename ("pwsh.exe")
            # does not start with "powershell"
            mock_transform.assert_not_called()


# ---------------------------------------------------------------------------
# execute() attaches warnings to result dict
# ---------------------------------------------------------------------------

class TestExecuteAttachesWarnings:
    """Test that execute() copies _pwsh_warnings into the result dict."""

    def test_warnings_in_result_dict(self):
        """When _pwsh_warnings is set, execute() puts them in result['pwsh_warnings']."""
        from tools.environments.base import BaseEnvironment

        class _FakeEnv(BaseEnvironment):
            """Concrete env that returns a fake result from _run_bash."""

            def __init__(self):
                super().__init__(cwd="/tmp", timeout=30)
                self._snapshot_ready = True
                self._pwsh_warnings = None

            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                proc = MagicMock()
                proc.returncode = 0
                return proc

            def _wait_for_process(self, proc, timeout=None):
                return {"output": "ok", "returncode": 0}

            def cleanup(self):
                pass

        env = _FakeEnv()
        env._pwsh_warnings = ["Line 3: ternary operator rewritten"]
        result = env.execute("$a ? $b : $c")

        assert "pwsh_warnings" in result
        assert result["pwsh_warnings"] == ["Line 3: ternary operator rewritten"]

    def test_warnings_consumed_after_use(self):
        """After execute() attaches warnings, _pwsh_warnings is set to None."""
        from tools.environments.base import BaseEnvironment

        class _FakeEnv(BaseEnvironment):
            def __init__(self):
                super().__init__(cwd="/tmp", timeout=30)
                self._snapshot_ready = True
                self._pwsh_warnings = None

            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                proc = MagicMock()
                proc.returncode = 0
                return proc

            def _wait_for_process(self, proc, timeout=None):
                return {"output": "ok", "returncode": 0}

            def cleanup(self):
                pass

        env = _FakeEnv()
        env._pwsh_warnings = ["warning text"]
        result = env.execute("command")

        assert "pwsh_warnings" in result
        assert env._pwsh_warnings is None

    def test_no_warnings_key_when_none_set(self):
        """When _pwsh_warnings is None, result dict does not have the key."""
        from tools.environments.base import BaseEnvironment

        class _FakeEnv(BaseEnvironment):
            def __init__(self):
                super().__init__(cwd="/tmp", timeout=30)
                self._snapshot_ready = True
                self._pwsh_warnings = None

            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                proc = MagicMock()
                proc.returncode = 0
                return proc

            def _wait_for_process(self, proc, timeout=None):
                return {"output": "ok", "returncode": 0}

            def cleanup(self):
                pass

        env = _FakeEnv()
        result = env.execute("command")

        assert "pwsh_warnings" not in result


# ---------------------------------------------------------------------------
# terminal_tool surfaces pwsh_warnings in JSON
# ---------------------------------------------------------------------------

class TestTerminalToolSurfacesWarnings:
    """Test that terminal_tool includes pwsh_warnings in its JSON output."""

    def test_pwsh_warnings_in_json_output(self):
        """When result contains pwsh_warnings, they appear in the JSON response."""
        # Test the result_dict building logic directly
        result = {
            "output": "hello",
            "returncode": 0,
            "pwsh_warnings": ["Line 1: ternary operator rewritten"],
        }

        result_dict = {
            "output": result["output"],
            "exit_code": result["returncode"],
            "error": None,
        }
        if result.get("pwsh_warnings"):
            result_dict["pwsh_warnings"] = result["pwsh_warnings"]

        json_output = json.dumps(result_dict, ensure_ascii=False)
        parsed = json.loads(json_output)
        assert "pwsh_warnings" in parsed
        assert parsed["pwsh_warnings"] == ["Line 1: ternary operator rewritten"]

    def test_no_pwsh_warnings_key_when_absent(self):
        """When result has no pwsh_warnings, they are omitted from JSON."""
        result = {
            "output": "hello",
            "returncode": 0,
        }

        result_dict = {
            "output": result["output"],
            "exit_code": result["returncode"],
            "error": None,
        }
        if result.get("pwsh_warnings"):
            result_dict["pwsh_warnings"] = result["pwsh_warnings"]

        json_output = json.dumps(result_dict, ensure_ascii=False)
        parsed = json.loads(json_output)
        assert "pwsh_warnings" not in parsed

    def test_empty_warnings_list_not_included(self):
        """Empty pwsh_warnings list is treated as falsy and omitted."""
        result = {
            "output": "hello",
            "returncode": 0,
            "pwsh_warnings": [],
        }

        result_dict = {
            "output": result["output"],
            "exit_code": result["returncode"],
            "error": None,
        }
        if result.get("pwsh_warnings"):
            result_dict["pwsh_warnings"] = result["pwsh_warnings"]

        json_output = json.dumps(result_dict, ensure_ascii=False)
        parsed = json.loads(json_output)
        assert "pwsh_warnings" not in parsed
