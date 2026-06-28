"""Portable in-process search fallback (GitHub #334).

On the Windows/PowerShell local backend, `search_files` hard-errored even with
ripgrep on PATH because the POSIX `command -v` probe and the `find`/`grep`
shell pipelines can't run there. `ShellFileOperations` now falls back to an
os.walk-based Python search on the local backend when rg is unavailable. These
tests exercise that fallback with no shell and no rg/grep/find dependency.
"""

import os

import pytest

import tools.file_operations as fo
from tools.file_operations import ShellFileOperations


@pytest.fixture()
def ops():
    """A ShellFileOperations whose env is irrelevant — the Python fallback
    methods never touch self.env."""
    return ShellFileOperations(None)


def _touch(path, content="", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# --------------------------------------------------------------------------- #
# File-name search
# --------------------------------------------------------------------------- #

def test_files_python_matches_by_name(ops, tmp_path):
    _touch(tmp_path / "alpha.py")
    _touch(tmp_path / "beta.py")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "sub" / "gamma.py")

    res = ops._search_files_python("*.py", str(tmp_path), limit=50, offset=0,
                                   has_hidden_path_ancestor=False)
    assert res.error is None
    names = sorted(os.path.basename(f) for f in res.files)
    assert names == ["alpha.py", "beta.py", "gamma.py"]
    assert res.total_count == 3


def test_files_python_bare_name_matches_at_any_depth(ops, tmp_path):
    # A bare token is wrapped as ``*token`` (mirrors _search_files_rg).
    _touch(tmp_path / "app_config")
    _touch(tmp_path / "deep" / "nested" / "myconfig")
    _touch(tmp_path / "unrelated.txt")

    res = ops._search_files_python("config", str(tmp_path), limit=50, offset=0,
                                   has_hidden_path_ancestor=False)
    names = sorted(os.path.basename(f) for f in res.files)
    assert names == ["app_config", "myconfig"]


def test_files_python_excludes_hidden_and_vendored_dirs(ops, tmp_path):
    _touch(tmp_path / "keep.py")
    _touch(tmp_path / ".git" / "hidden.py")
    _touch(tmp_path / "node_modules" / "dep.py")
    _touch(tmp_path / ".secret.py")  # hidden file

    res = ops._search_files_python("*.py", str(tmp_path), limit=50, offset=0,
                                   has_hidden_path_ancestor=False)
    names = [os.path.basename(f) for f in res.files]
    assert names == ["keep.py"]


def test_files_python_includes_hidden_when_root_is_hidden(ops, tmp_path):
    hidden_root = tmp_path / ".config"
    _touch(hidden_root / "settings.py")
    res = ops._search_files_python("*.py", str(hidden_root), limit=50, offset=0,
                                   has_hidden_path_ancestor=True)
    assert [os.path.basename(f) for f in res.files] == ["settings.py"]


def test_files_python_sorts_newest_first_and_paginates(ops, tmp_path):
    _touch(tmp_path / "old.py", mtime=1_000)
    _touch(tmp_path / "mid.py", mtime=2_000)
    _touch(tmp_path / "new.py", mtime=3_000)

    res = ops._search_files_python("*.py", str(tmp_path), limit=2, offset=0,
                                   has_hidden_path_ancestor=False)
    assert [os.path.basename(f) for f in res.files] == ["new.py", "mid.py"]
    assert res.total_count == 3
    assert res.truncated is True

    res2 = ops._search_files_python("*.py", str(tmp_path), limit=2, offset=2,
                                    has_hidden_path_ancestor=False)
    assert [os.path.basename(f) for f in res2.files] == ["old.py"]


def test_files_python_unicode_filename(ops, tmp_path):
    _touch(tmp_path / "配置文件.py")
    res = ops._search_files_python("*.py", str(tmp_path), limit=50, offset=0,
                                   has_hidden_path_ancestor=False)
    assert [os.path.basename(f) for f in res.files] == ["配置文件.py"]


# --------------------------------------------------------------------------- #
# Content search
# --------------------------------------------------------------------------- #

def test_content_python_matches_lines(ops, tmp_path):
    _touch(tmp_path / "a.py", "import os\nTODO: fix this\nprint(1)\n")
    _touch(tmp_path / "b.py", "clean file\n")

    res = ops._search_content_python("TODO", str(tmp_path), file_glob=None,
                                     limit=50, offset=0, output_mode="content",
                                     context=0)
    assert res.error is None
    assert len(res.matches) == 1
    m = res.matches[0]
    assert os.path.basename(m.path) == "a.py"
    assert m.line_number == 2
    assert "TODO" in m.content


def test_content_python_file_glob_filter(ops, tmp_path):
    _touch(tmp_path / "a.py", "needle here\n")
    _touch(tmp_path / "a.txt", "needle here\n")

    res = ops._search_content_python("needle", str(tmp_path), file_glob="*.py",
                                     limit=50, offset=0, output_mode="content",
                                     context=0)
    assert len(res.matches) == 1
    assert os.path.basename(res.matches[0].path) == "a.py"


def test_content_python_count_and_files_only_modes(ops, tmp_path):
    _touch(tmp_path / "a.py", "x\nx\ny\n")
    _touch(tmp_path / "b.py", "x\n")

    count = ops._search_content_python("x", str(tmp_path), file_glob=None,
                                       limit=50, offset=0, output_mode="count",
                                       context=0)
    assert count.total_count == 3
    assert {os.path.basename(k): v for k, v in count.counts.items()} == {"a.py": 2, "b.py": 1}

    files_only = ops._search_content_python("x", str(tmp_path), file_glob=None,
                                            limit=50, offset=0,
                                            output_mode="files_only", context=0)
    assert sorted(os.path.basename(f) for f in files_only.files) == ["a.py", "b.py"]


def test_content_python_context_lines(ops, tmp_path):
    _touch(tmp_path / "a.py", "line1\nline2\nMATCH\nline4\nline5\n")
    res = ops._search_content_python("MATCH", str(tmp_path), file_glob=None,
                                     limit=50, offset=0, output_mode="content",
                                     context=1)
    nums = sorted(m.line_number for m in res.matches)
    assert nums == [2, 3, 4]  # match plus one line each side


def test_content_python_skips_binary(ops, tmp_path):
    _touch(tmp_path / "data.bin", b"\x00\x01needle\x00")
    _touch(tmp_path / "code.py", "needle\n")
    res = ops._search_content_python("needle", str(tmp_path), file_glob=None,
                                     limit=50, offset=0, output_mode="content",
                                     context=0)
    assert [os.path.basename(m.path) for m in res.matches] == ["code.py"]


def test_content_python_unicode_content(ops, tmp_path):
    _touch(tmp_path / "zh.py", "# 你好世界\nvalue = 1\n")
    res = ops._search_content_python("你好", str(tmp_path), file_glob=None,
                                     limit=50, offset=0, output_mode="content",
                                     context=0)
    assert len(res.matches) == 1
    assert "你好世界" in res.matches[0].content


def test_content_python_newline_pattern_is_line_oriented(ops, tmp_path):
    # rg/grep (and this fallback) are line-oriented: a \n-regex matches nothing,
    # so the alternative `needle` must NOT match either — 0 results, and the
    # caller attaches the line-oriented warning.
    _touch(tmp_path / "a.py", "needle here\nother\n")
    res = ops._search_content_python(r"needle|absent\npattern", str(tmp_path),
                                     file_glob=None, limit=50, offset=0,
                                     output_mode="content", context=0)
    assert res.error is None
    assert res.total_count == 0


def test_content_python_invalid_regex_returns_error(ops, tmp_path):
    _touch(tmp_path / "a.py", "hello\n")
    res = ops._search_content_python("(unclosed", str(tmp_path), file_glob=None,
                                     limit=50, offset=0, output_mode="content",
                                     context=0)
    assert res.error is not None
    assert "Invalid search pattern" in res.error


# --------------------------------------------------------------------------- #
# Routing: local backend + no rg => fallback, no shell, no "requires rg" error
# --------------------------------------------------------------------------- #

@pytest.fixture()
def local_ops_no_rg(monkeypatch, tmp_path):
    ops = ShellFileOperations(None)
    monkeypatch.setattr(ops, "_is_local_env", lambda: True)
    monkeypatch.setattr(fo.shutil, "which", lambda name: None)
    # _has_command must never be consulted on the local path; make it explode.
    def _boom(_cmd):
        raise AssertionError("_has_command must not run on the local backend")
    monkeypatch.setattr(ops, "_has_command", _boom)
    return ops


def test_search_files_routes_to_python_without_rg(local_ops_no_rg, tmp_path):
    _touch(tmp_path / "found.py")
    res = local_ops_no_rg.search("*.py", path=str(tmp_path), target="files")
    assert res.error is None
    assert [os.path.basename(f) for f in res.files] == ["found.py"]


def test_search_content_routes_to_python_without_rg(local_ops_no_rg, tmp_path):
    _touch(tmp_path / "x.py", "needle\n")
    res = local_ops_no_rg.search("needle", path=str(tmp_path), target="content")
    assert res.error is None
    assert len(res.matches) == 1


def test_search_local_path_not_found_without_shell(local_ops_no_rg, tmp_path):
    missing = str(tmp_path / "does_not_exist")
    res = local_ops_no_rg.search("*.py", path=missing, target="files")
    assert res.error is not None
    assert "Path not found" in res.error
