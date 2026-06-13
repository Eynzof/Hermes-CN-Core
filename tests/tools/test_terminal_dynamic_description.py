"""Tests for the runtime-adaptive terminal-tool description (powershell vs bash).

Covers the two helpers for Windows PowerShell support in
``tools/terminal_tool.py``:

* ``_detect_shell_for_description`` — picks ``"powershell"`` or ``"bash"`` based on
  platform + ``HERMES_SHELL_TYPE`` + PATH lookup for ``powershell``/``powershell.exe``.
  ``@lru_cache``-d, so the suite clears the cache around every case.
* ``_build_dynamic_terminal_description`` — rewrites the LLM-facing tool
  description so its platform sentence and forbidden-command references match
  the shell actually in use.

All cases mock ``platform.system`` and ``shutil.which`` so they run
identically on Linux CI (where the real platform is never Windows).
"""

import os
from unittest import mock

import pytest

from tools.registry import registry
from tools.terminal_tool import (
    TERMINAL_TOOL_DESCRIPTION,
    _build_dynamic_terminal_description,
    _detect_shell_for_description,
)

DETECT = "tools.terminal_tool._detect_shell_for_description"
SHUTIL_WHICH = "shutil.which"
SYSTEM = "platform.system"


@pytest.fixture(autouse=True)
def _clear_detect_cache():
    """``_detect_shell_for_description`` is lru_cached — reset around each test."""
    _detect_shell_for_description.cache_clear()
    yield
    _detect_shell_for_description.cache_clear()


def _shell_type_env(value):
    """Patch HERMES_SHELL_TYPE; ``None`` removes it (exercise the default)."""
    env = dict(os.environ)
    env.pop("HERMES_SHELL_TYPE", None)
    if value is not None:
        env["HERMES_SHELL_TYPE"] = value
    return mock.patch.dict(os.environ, env, clear=True)


# shutil.which matchers ------------------------------------------------
def _which_matcher(wanted: str | None):
    """Return a ``shutil.which`` side-effect that matches only *wanted*."""
    def _which(exe):
        if exe == wanted or exe == f"{wanted}.exe":
            return fr"C:\Windows\System32\WindowsPowerShell\v1.0\{wanted}.exe"
        return None
    return _which


# --------------------------------------------------------------------------- #
# _detect_shell_for_description
# --------------------------------------------------------------------------- #


def test_detect_non_windows_is_bash():
    with mock.patch(SYSTEM, return_value="Linux"), mock.patch(SHUTIL_WHICH) as fp:
        assert _detect_shell_for_description() == "bash"
        fp.assert_not_called()  # never probe off Windows


def test_detect_macos_is_bash():
    with mock.patch(SYSTEM, return_value="Darwin"):
        assert _detect_shell_for_description() == "bash"


def test_detect_windows_explicit_bash_returns_powershell():
    # bash on Windows is no longer supported; _detect_shell_for_description()
    # returns "powershell" so _resolve_shell() can raise RuntimeError.
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("bash"), mock.patch(SHUTIL_WHICH) as fp:
        assert _detect_shell_for_description() == "powershell"
        fp.assert_not_called()


def test_detect_windows_auto_with_powershell_available():
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("auto"), mock.patch(
        SHUTIL_WHICH, side_effect=_which_matcher("powershell")
    ):
        assert _detect_shell_for_description() == "powershell"


def test_detect_windows_auto_always_returns_powershell():
    # Windows always uses PowerShell; PATH probing is not needed since
    # _resolve_shell() uses powershell.exe which ships with the OS.
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("auto"), mock.patch(
        SHUTIL_WHICH, return_value=None
    ):
        assert _detect_shell_for_description() == "powershell"


def test_detect_windows_default_unset_behaves_as_auto():
    # HERMES_SHELL_TYPE unset → defaults to "auto" → powershell when available.
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env(None), mock.patch(
        SHUTIL_WHICH, side_effect=_which_matcher("powershell")
    ):
        assert _detect_shell_for_description() == "powershell"


def test_detect_windows_explicit_pwsh_reports_powershell_even_if_missing():
    # Explicit pwsh/powershell request reports "powershell" so _resolve_shell()
    # can raise a clear error downstream, rather than silently degrading to bash.
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("pwsh"), mock.patch(
        SHUTIL_WHICH, return_value=None
    ):
        assert _detect_shell_for_description() == "powershell"


def test_detect_windows_explicit_powershell_reports_powershell_even_if_missing():
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("powershell"), mock.patch(
        SHUTIL_WHICH, return_value=None
    ):
        assert _detect_shell_for_description() == "powershell"


def test_detect_windows_powershell_alias_available():
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("powershell"), mock.patch(
        SHUTIL_WHICH, side_effect=_which_matcher("powershell")
    ):
        assert _detect_shell_for_description() == "powershell"


def test_detect_windows_unknown_shell_type_is_powershell():
    # Any non-bash shell type on Windows → powershell (bash raises downstream).
    with mock.patch(SYSTEM, return_value="Windows"), _shell_type_env("fish"), mock.patch(SHUTIL_WHICH) as fp:
        assert _detect_shell_for_description() == "powershell"
        fp.assert_not_called()


def test_detect_is_cached_until_cleared():
    # _detect_shell_for_description is @lru_cache-d and decides purely from
    # platform.system() + HERMES_SHELL_TYPE (no PATH probe), so observe caching
    # via the number of platform.system() calls.
    with mock.patch(SYSTEM, return_value="Windows") as sysm, _shell_type_env("auto"):
        assert _detect_shell_for_description() == "powershell"
        assert _detect_shell_for_description() == "powershell"
        assert sysm.call_count == 1  # second call served from lru_cache
        _detect_shell_for_description.cache_clear()
        assert _detect_shell_for_description() == "powershell"
        assert sysm.call_count == 2  # re-evaluated after cache_clear


# --------------------------------------------------------------------------- #
# _build_dynamic_terminal_description
# --------------------------------------------------------------------------- #


def test_build_returns_description_dict():
    with mock.patch(DETECT, return_value="bash"), mock.patch(SYSTEM, return_value="Linux"):
        out = _build_dynamic_terminal_description()
    assert isinstance(out, dict)
    assert set(out) == {"description"}
    assert isinstance(out["description"], str) and out["description"]


def test_build_powershell_uses_powershell_sentence_and_cmdlet_refs():
    with mock.patch(DETECT, return_value="powershell"), mock.patch(SYSTEM, return_value="Windows"):
        desc = _build_dynamic_terminal_description()["description"]
    assert "Windows PowerShell environment" in desc
    # powershell-adapted forbidden-command references applied …
    assert "Do NOT use Get-Content/cat/type to read files" in desc
    assert "Do NOT use Select-String/findstr to search" in desc
    assert "Out-Host -Paging" in desc
    # … and the Linux/bash-only phrasings are gone.
    assert "Do NOT use cat/head/tail to read files" not in desc
    assert "Execute shell commands on a Linux environment." not in desc


def test_build_non_windows_leaves_linux_references_intact():
    with mock.patch(DETECT, return_value="bash"), mock.patch(SYSTEM, return_value="Linux"):
        desc = _build_dynamic_terminal_description()["description"]
    assert "Linux environment" in desc
    assert "Do NOT use cat/head/tail to read files" in desc
    # no powershell cmdlet substitutions on the bash/Linux path
    assert "Get-Content/cat/type" not in desc


def test_static_description_contains_phrases_the_powershell_path_rewrites():
    # Guard against the static description drifting out from under the
    # str.replace() calls in _build_dynamic_terminal_description (a silent
    # no-op rewrite would otherwise pass the powershell test above only by luck).
    for phrase in (
        "Execute shell commands on a Linux environment.",
        "Do NOT use cat/head/tail to read files",
        "Do NOT use grep/rg/find to search",
        "Pipe git output to cat if it might page.",
    ):
        assert phrase in TERMINAL_TOOL_DESCRIPTION


# --------------------------------------------------------------------------- #
# registry integration
# --------------------------------------------------------------------------- #


def test_terminal_registered_with_dynamic_override():
    entry = registry.get_entry("terminal")
    assert entry is not None
    assert entry.dynamic_schema_overrides is _build_dynamic_terminal_description
