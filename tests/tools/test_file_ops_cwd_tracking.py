"""Regression tests for cwd-staleness in ShellFileOperations.

The bug: ShellFileOperations captured the terminal env's cwd at __init__
time and used that stale value for every subsequent _exec() call.  When
a user ran ``cd`` via the terminal tool, ``env.cwd`` updated but
``ops.cwd`` did not.  Relative paths passed to patch/read/write/search
then targeted the wrong directory — typically the session's start dir
instead of the current working directory.

Observed symptom: patch_replace() returned ``success=True`` with a
plausible diff, but the user's ``git diff`` showed no change (because
the patch landed in a different directory's copy of the same file).

Fix: _exec() now prefers the LIVE ``env.cwd`` over the init-time
``self.cwd``.  Explicit ``cwd`` arg to _exec still wins over both.
"""

from __future__ import annotations

import os
import sys

import pytest

from agent.re_compat import re
from tools.file_operations import ShellFileOperations


class _FakeEnv:
    """Minimal terminal env that tracks cwd across execute() calls.

    Matches the real ``BaseEnvironment`` contract: ``cwd`` attribute plus
    an ``execute(command, cwd=...)`` method whose return dict carries
    ``output`` and ``returncode``.  Commands are interpreted in-process so
    the tests run on Windows without a ``bash``/``cat`` toolchain.
    """

    def __init__(self, start_cwd: str):
        self.cwd = start_cwd
        self.calls: list[dict] = []

    def _resolve(self, path: str, cwd: str | None) -> str:
        base = cwd or self.cwd
        if os.path.isabs(path):
            return path
        return os.path.join(base, path)

    def execute(self, command: str, cwd: str = None, **kwargs) -> dict:
        self.calls.append({"command": command, "cwd": cwd})
        workdir = cwd or self.cwd

        # Simulate cd by updating self.cwd (the real env does the same
        # via _extract_cwd_from_output after a successful command)
        if command.strip().startswith("cd "):
            new = command.strip()[3:].strip()
            self.cwd = new
            return {"output": "", "returncode": 0}

        stdin_data = kwargs.get("stdin_data")
        if stdin_data is not None:
            # Atomic write script emitted by _atomic_write for remote backends.
            # Extract the target path from ``t='...';`` and write stdin_data there.
            match = re.search(r"t='([^']+)';", command)
            if match:
                target = match.group(1)
                abs_target = self._resolve(target, cwd)
                try:
                    os.makedirs(os.path.dirname(abs_target), exist_ok=True)
                    # Write bytes verbatim so LF/CRLF round-trips match the
                    # real atomic-write path (binary mode, no OS translation).
                    with open(abs_target, "wb") as fh:
                        fh.write(stdin_data.encode("utf-8"))
                    return {"output": "", "returncode": 0}
                except Exception as exc:
                    return {"output": str(exc), "returncode": 1}
            return {"output": "unhandled stdin command", "returncode": 1}

        stripped = command.strip()

        # cat <file> [2>/dev/null]
        cat_match = re.match(r"cat\s+(.+?)(?:\s+2>/dev/null)?$", stripped)
        if cat_match:
            path = cat_match.group(1).strip().strip("'\"")
            abs_path = self._resolve(path, cwd)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    return {"output": fh.read(), "returncode": 0}
            except Exception:
                return {"output": "", "returncode": 1}

        # mkdir -p <dir>
        mkdir_match = re.match(r"mkdir\s+-p\s+(.+)$", stripped)
        if mkdir_match:
            path = mkdir_match.group(1).strip().strip("'\"")
            abs_path = self._resolve(path, cwd)
            try:
                os.makedirs(abs_path, exist_ok=True)
                return {"output": "", "returncode": 0}
            except Exception as exc:
                return {"output": str(exc), "returncode": 1}

        # wc -c < <file> [2>/dev/null]
        wc_match = re.match(r"wc\s+-c\s+<\s+(.+?)(?:\s+2>/dev/null)?$", stripped)
        if wc_match:
            path = wc_match.group(1).strip().strip("'\"")
            abs_path = self._resolve(path, cwd)
            try:
                size = os.path.getsize(abs_path)
                return {"output": str(size), "returncode": 0}
            except Exception:
                return {"output": "", "returncode": 1}

        return {"output": f"unhandled command: {command}", "returncode": 1}


@pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: shell ops cwd tracking")
class TestShellFileOpsCwdTracking:
    """_exec() must use live env.cwd, not the init-time cached cwd."""

    def test_exec_follows_env_cwd_after_cd(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "target.txt").write_text("content-a\n")
        (dir_b / "target.txt").write_text("content-b\n")

        env = _FakeEnv(start_cwd=str(dir_a))
        ops = ShellFileOperations(env, cwd=str(dir_a))
        assert ops.cwd == str(dir_a)  # init-time

        # Simulate the user running `cd b` in terminal
        env.execute(f"cd {dir_b}")
        assert env.cwd == str(dir_b)
        assert ops.cwd == str(dir_a), "ops.cwd is still init-time (fallback only)"

        # Reading a relative path must now hit dir_b, not dir_a
        result = ops._exec("cat target.txt")
        assert result.exit_code == 0
        assert "content-b" in result.stdout, (
            f"Expected dir_b content, got {result.stdout!r}. "
            "Stale ops.cwd leaked through — _exec must prefer env.cwd."
        )

    def test_patch_replace_targets_live_cwd_not_init_cwd(self, tmp_path):
        """The exact bug reported: patch lands in wrong dir after cd."""
        dir_a = tmp_path / "main"
        dir_b = tmp_path / "worktree"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "t.txt").write_text("shared text\n")
        (dir_b / "t.txt").write_text("shared text\n")

        env = _FakeEnv(start_cwd=str(dir_a))
        ops = ShellFileOperations(env, cwd=str(dir_a))

        # Emulate user cd'ing into the worktree
        env.execute(f"cd {dir_b}")
        assert env.cwd == str(dir_b)

        # Patch with a RELATIVE path — must target the worktree, not main
        result = ops.patch_replace("t.txt", "shared text\n", "PATCHED\n")
        assert result.success is True

        assert (dir_b / "t.txt").read_text() == "PATCHED\n", (
            "patch must land in the live-cwd dir (worktree)"
        )
        assert (dir_a / "t.txt").read_text() == "shared text\n", (
            "patch must NOT land in the init-time dir (main)"
        )

    def test_explicit_cwd_arg_still_wins(self, tmp_path):
        """An explicit cwd= arg to _exec must override both env.cwd and self.cwd."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_c = tmp_path / "c"
        for d in (dir_a, dir_b, dir_c):
            d.mkdir()
        (dir_a / "target.txt").write_text("from-a\n")
        (dir_b / "target.txt").write_text("from-b\n")
        (dir_c / "target.txt").write_text("from-c\n")

        env = _FakeEnv(start_cwd=str(dir_a))
        ops = ShellFileOperations(env, cwd=str(dir_a))
        env.execute(f"cd {dir_b}")

        # Explicit cwd=dir_c should win over env.cwd (dir_b) and self.cwd (dir_a)
        result = ops._exec("cat target.txt", cwd=str(dir_c))
        assert "from-c" in result.stdout

    def test_env_without_cwd_attribute_falls_back_to_self_cwd(self, tmp_path):
        """Backends without a cwd attribute still work via init-time cwd."""
        import os
        dir_a = tmp_path / "fixed"
        dir_a.mkdir()
        (dir_a / "target.txt").write_text("fixed-content\n")

        class _NoCwdEnv:
            def execute(self, command, cwd=None, **kwargs):
                if not command.strip().startswith("cat "):
                    return {"output": "", "returncode": 1}
                path = command.strip()[4:].strip().strip("'\"")
                abs_path = os.path.join(cwd or ".", path) if not os.path.isabs(path) else path
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                        return {"output": fh.read(), "returncode": 0}
                except Exception:
                    return {"output": "", "returncode": 1}

        env = _NoCwdEnv()
        ops = ShellFileOperations(env, cwd=str(dir_a))
        result = ops._exec("cat target.txt")
        assert result.exit_code == 0
        assert "fixed-content" in result.stdout

    def test_patch_returns_success_only_when_file_actually_written(self, tmp_path):
        """Safety rail: patch_replace success must reflect the real file state.

        This test doesn't trigger the bug directly (it would require manual
        corruption of the write), but it pins the invariant: when
        patch_replace returns success=True, the file on disk matches the
        intended content.  If a future write_file change ever regresses,
        this test catches it.
        """
        target = tmp_path / "file.txt"
        target.write_text("old content\n")

        env = _FakeEnv(start_cwd=str(tmp_path))
        ops = ShellFileOperations(env, cwd=str(tmp_path))

        result = ops.patch_replace(str(target), "old content\n", "new content\n")
        assert result.success is True
        assert result.error is None
        assert target.read_text() == "new content\n", (
            "patch_replace claimed success but file wasn't written correctly"
        )
