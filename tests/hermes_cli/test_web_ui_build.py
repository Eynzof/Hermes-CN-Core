"""Web launch keeps freshness/serialization but never masks a failed build."""
import os
import time
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.main_web_build import _build_web_ui, _web_ui_build_needed
from tests.hermes_cli.test_source_build import stamp_product, copy_freshness_scripts, use_host_node_as_pm_node
from tests.hermes_cli.test_source_build import source_checkout, source_products, _events
# POSIX-only: /usr/bin/npm path assertions and fcntl flock serialization.
# On Windows npm resolves from the real PATH (find_node_executable_on_path scans
# PATH directly, bypassing the shutil.which mock) and fcntl does not exist.
win32_posix_web_build = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only: /usr/bin/npm path assertion + fcntl flock",
)

from hermes_cli.main_web_build import _build_web_ui, _run_npm_install_deterministic
from hermes_cli.main_web_build import _web_ui_build_needed, _compute_web_ui_content_hash, _missing_web_build_tool, _web_ui_stamp_path, _write_web_ui_build_stamp
from hermes_cli.update_cmd import _web_build_toolchain_ready, _web_toolchain_roots


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path, monkeypatch):
    """Keep web-build-stamp writes inside the test's tmp dir, never the real home."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "_hermes_home"))


def _touch(path: Path, offset: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    if offset:
        t = time.time() + offset
        os.utime(path, (t, t))


def _make_web_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Return (web_dir, dist_dir) matching real repo layout."""
    copy_freshness_scripts(tmp_path)
    web_dir = tmp_path / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "package.json").touch()
    dist_dir = tmp_path / "hermes_cli" / "web_dist"
    return web_dir, dist_dir


class TestWebUIBuildNeeded:
    """Content-hash staleness — replaces the old mtime comparison.

    The dashboard build hashes the web source tree (like the desktop build)
    instead of comparing mtimes, so git operations that rewrite mtimes
    without changing content no longer fool the freshness check.
    """

    @staticmethod
    def _root(web_dir: Path) -> Path:
        return web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent

    def _stamp_current(self, web_dir: Path) -> None:
        """Record a stamp matching web_dir's current source content."""
        _write_web_ui_build_stamp(self._root(web_dir), web_dir)





    def test_mtime_only_change_is_not_stale(self, tmp_path):
        """The whole point: bumping mtimes without changing bytes (what
        ``git pull`` / ``hermes update`` do) must NOT report stale."""
        web_dir, dist_dir = _make_web_dir(tmp_path)
        src = web_dir / "src" / "App.tsx"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("export const A = 1\n")
        (dist_dir / ".vite").mkdir(parents=True, exist_ok=True)
        (dist_dir / ".vite" / "manifest.json").write_text("{}")
        self._stamp_current(web_dir)
        assert _web_ui_build_needed(web_dir) is False
        future = time.time() + 10_000
        os.utime(src, (future, future))
        os.utime(web_dir / "package.json", (future, future))
        assert _web_ui_build_needed(web_dir) is False



    def test_content_hash_is_deterministic(self, tmp_path):
        web_dir, _ = _make_web_dir(tmp_path)
        (web_dir / "src").mkdir(parents=True, exist_ok=True)
        (web_dir / "src" / "App.tsx").write_text("export const A = 1\n")
        root = self._root(web_dir)
        h1 = _compute_web_ui_content_hash(root, web_dir)
        h2 = _compute_web_ui_content_hash(root, web_dir)
        assert h1 == h2
        assert len(h1) == 64

    def test_write_stamp_creates_file_with_hash(self, tmp_path):
        import json as _json
        web_dir, _ = _make_web_dir(tmp_path)
        (web_dir / "src").mkdir(parents=True, exist_ok=True)
        (web_dir / "src" / "App.tsx").write_text("export const A = 1\n")
        self._stamp_current(web_dir)
        stamp = _web_ui_stamp_path()
        assert stamp.is_file()
        data = _json.loads(stamp.read_text())
        assert data["contentHash"] == _compute_web_ui_content_hash(self._root(web_dir), web_dir)



class TestBuildWebUISkipsWhenFresh:







    @win32_posix_web_build
    def test_web_install_omits_workspace_and_scrubs_esbuild_override(
        self, tmp_path, monkeypatch
    ):
        """web/ with its own lockfile => _workspace_root returns web_dir, so
        --workspace web would fail (npm can't find that workspace from inside
        web/). The flag must be dropped and the install run plainly from web_dir.
        Symmetric to the TUI fix in test_tui_npm_install.py. See #42973.

        With web's own lockfile present at cwd, _run_npm_install_deterministic
        uses ``npm ci`` (not ``npm install``). The shared installer must also
        remove an inherited esbuild binary override so package/binary versions
        cannot diverge (#87405).
        """
        web_dir, _ = _make_web_dir(tmp_path)
        (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")
        (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setenv("PREFIX", "/usr")
        monkeypatch.setenv("ESBUILD_BINARY_PATH", "/opt/esbuild-0.28.2")

        install_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        build_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        with patch("hermes_cli.main.shutil.which", return_value="/usr/bin/npm"), \
             patch("hermes_cli.main.subprocess.run", return_value=install_cp) as mock_run, \
             patch("hermes_cli.main_web_build._run_with_idle_timeout", return_value=build_cp) as mock_build:
            result = _build_web_ui(web_dir)

        assert result is True
        args, kwargs = mock_run.call_args
        assert "--workspace" not in args[0]
        assert Path(args[0][0]).name in {"npm", "npm.cmd"}
        assert args[0][1:] == ["ci", "--include=dev", "--silent", "--prefer-offline"]
        assert kwargs["cwd"] == web_dir
        assert "ESBUILD_BINARY_PATH" not in kwargs["env"]
        assert "ESBUILD_BINARY_PATH" not in mock_build.call_args.kwargs["env"]

    def test_workspace_root_install_names_update_closure(self, tmp_path, monkeypatch):
        """From the workspace root, _build_web_ui must install the SAME
        closure as `hermes update` (ui-tui + web + --include-workspace-root).

        The install helper prefers `npm ci`, which deletes node_modules before
        reifying the requested tree — a narrower `--workspace web`-only pass
        right after the update step silently pruned root devDependencies and
        the ui-tui workspace while exiting 0. See #43564/#64354.
        """
        web_dir, _ = _make_web_dir(tmp_path)
        # Root lockfile only => _workspace_root(web_dir) == tmp_path.
        (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
        (tmp_path / "ui-tui").mkdir()
        (tmp_path / "ui-tui" / "package.json").write_text("{}", encoding="utf-8")
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setenv("PREFIX", "/usr")

        install_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        build_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        with patch("hermes_cli.main.shutil.which", return_value="/usr/bin/npm"), \
             patch("hermes_cli.main.subprocess.run", return_value=install_cp) as mock_run, \
             patch("hermes_cli.main_web_build._run_with_idle_timeout", return_value=build_cp):
            result = _build_web_ui(web_dir)

        assert result is True
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert "--include-workspace-root" in cmd
        assert cmd.count("--workspace") == 2
        assert "ui-tui" in cmd and "web" in cmd
        assert kwargs["cwd"] == tmp_path

    def test_workspace_root_install_skips_missing_ui_tui(self, tmp_path, monkeypatch):
        """A checkout without the ui-tui workspace must not name it — npm
        fails hard on a --workspace that doesn't exist."""
        web_dir, _ = _make_web_dir(tmp_path)
        (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setenv("PREFIX", "/usr")

        install_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        build_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        with patch("hermes_cli.main.shutil.which", return_value="/usr/bin/npm"), \
             patch("hermes_cli.main.subprocess.run", return_value=install_cp) as mock_run, \
             patch("hermes_cli.main_web_build._run_with_idle_timeout", return_value=build_cp):
            result = _build_web_ui(web_dir)

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "ui-tui" not in cmd
        assert "--include-workspace-root" in cmd
        assert "web" in cmd

    @win32_posix_web_build
    def test_web_build_uses_idle_timeout_helper(self, tmp_path):
        """npm run build now goes through _run_with_idle_timeout (issue #33788).

        The install step keeps its capture_output behavior (the existing
        retry-on-EPERM contract depends on it); only the long-running build
        step is streamed + idle-killed.
        """
        web_dir, _ = _make_web_dir(tmp_path)

        install_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        build_cp = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        with patch("hermes_cli.main.shutil.which", return_value="/usr/bin/npm"), \
             patch("hermes_cli.main.subprocess.run", return_value=install_cp), \
             patch("hermes_cli.main_web_build._run_with_idle_timeout", return_value=build_cp) as mock_idle:
            result = _build_web_ui(web_dir)

        assert result is True
        # Build was invoked through the idle-timeout helper, not subprocess.run.
        mock_idle.assert_called_once()
        args, kwargs = mock_idle.call_args
        # Positional: [npm, "run", "build"]; cwd passed as kwarg.
        assert args[0] == ["/usr/bin/npm", "run", "build"]
        assert kwargs["cwd"] == web_dir


class TestBuildWebUIRetryAndStaleFallback:
    """Coverage for the retry + stale-dist fallback added in #23824 / issue #23817."""

    def test_retries_build_once_on_failure(self, tmp_path):
        web_dir, _ = _make_web_dir(tmp_path)
        Subprocess = __import__("subprocess")
        install_ok = Subprocess.CompletedProcess([], 0, stdout="", stderr="")
        # build attempt 1: fail; build attempt 2: success.
        build_fail = Subprocess.CompletedProcess([], 1, stdout="EPERM", stderr="")
        build_ok = Subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("hermes_cli.main.shutil.which", return_value="/usr/bin/npm"), \
             patch("hermes_cli.main_web_build._time.sleep") as mock_sleep, \
             patch("hermes_cli.main.subprocess.run", return_value=install_ok), \
             patch("hermes_cli.main_web_build._run_with_idle_timeout",
                   side_effect=[build_fail, build_ok]) as mock_idle:
            result = _build_web_ui(web_dir)

@pytest.mark.platforms("posix")
def test_web_build_prepares_once_and_skips_a_current_product(source_products):
    root, acquired = source_products
    assert _build_web_ui(root / "web", fatal=True)
    assert [event["step"] for event in _events(root)] == ["deps", "web"]
    assert acquired == ["npm"]
    assert not _web_ui_build_needed(root / "web")
    assert _build_web_ui(root / "web", fatal=True)
    assert acquired == ["npm"]
    assert len(_events(root)) == 2


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("fatal", [False, True])
def test_web_failure_is_not_success_even_with_an_old_dist(source_products, fatal):
    root, acquired = source_products
    dist = root / "hermes_cli/web_dist/index.html"
    dist.parent.mkdir(parents=True)
    dist.write_text("old product")
    (root / "fail-web").touch()
    assert not _build_web_ui(root / "web", fatal=fatal)
    assert acquired == ["npm"]
    assert [event["step"] for event in _events(root)] == ["deps", "web"]
    assert dist.read_text() == "old product"
    assert not (root / "hermes_cli/web_dist/hermes-build.json").exists()


@pytest.mark.platforms("posix")
def test_failed_preparation_never_runs_web_compilation(source_products):
    root, acquired = source_products
    (root / "package-lock.json").write_text("not json")
    assert not _build_web_ui(root / "web", fatal=True)
    assert acquired == ["npm"]
    assert _events(root) == []
    assert not (root / "hermes_cli/web_dist/hermes-build.json").exists()


@pytest.mark.platforms("linux")
def test_web_rebuild_reuses_the_existing_desktop_union(source_products):
    from hermes_cli.source_build import build_update_products

    root, acquired = source_products
    build_update_products(root, desktop=True)
    before = _events(root)
    (root / "web/changed.ts").write_text("changed web source")
    assert _build_web_ui(root / "web", fatal=True)
    assert _events(root) == [*before, {"step": "web"}]
    assert acquired == ["npm", "npm"]
    assert (root / "node_modules/apps-desktop").exists()


@pytest.mark.platforms("linux")
@pytest.mark.parametrize('existing', [False, True])
def test_contended_build_waits_and_rechecks_winner(tmp_path, monkeypatch, existing):
    import fcntl
    import threading

    use_host_node_as_pm_node(monkeypatch)
    web, dist = _make_web_dir(tmp_path)
    if existing:
        _touch(dist / 'index.html')
    holder = open(tmp_path / '.web_ui_build.lock', 'a', encoding='utf-8')
    fcntl.flock(holder, fcntl.LOCK_EX)
    real_flock = fcntl.flock
    contended = threading.Event()
    def flock(fd, operation):
        contended.set()
        return real_flock(fd, operation)
    monkeypatch.setattr(fcntl, 'flock', flock)
    def finish():
        try:
            assert contended.wait(10), 'waiter never attempted the lock'
            _touch(dist / 'index.html')
            (dist / 'index.html').write_text('winner', encoding='utf-8')
            stamp_product(tmp_path, 'web', dist)
        finally:
            holder.close()
    worker = threading.Thread(target=finish)
    worker.start()
    try:
        with patch('hermes_cli.source_build.source_build_env', side_effect=AssertionError('duplicate preparation')):
            assert _build_web_ui(web, fatal=True)
        assert (dist / 'index.html').read_text(encoding='utf-8') == 'winner'
    finally:
        worker.join(timeout=15)
        holder.close()
    assert not worker.is_alive()


@pytest.mark.platforms("posix")
def test_lock_open_failure_does_not_start_an_unprotected_build(source_products):
    root, acquired = source_products
    (root / ".web_ui_build.lock").mkdir()
    assert not _build_web_ui(root / "web", fatal=True)
    assert acquired == []
    assert _events(root) == []
