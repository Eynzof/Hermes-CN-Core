"""Web launch honors compiler receipts and never masks a failed build."""
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.main_web_build import _build_web_ui, _web_ui_build_needed
from tests.hermes_cli.test_source_build import (
    _events,
    copy_freshness_scripts,
    stamp_product,
    use_host_node_as_pm_node,
)
from tests.hermes_cli.test_source_build import source_checkout, source_products  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "_hermes_home"))


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _make_web_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Return the source and product paths used by the real dashboard builder."""
    copy_freshness_scripts(tmp_path)
    web_dir = tmp_path / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    return web_dir, tmp_path / "hermes_cli" / "web_dist"


@pytest.fixture
def web_product(tmp_path, monkeypatch):
    use_host_node_as_pm_node(monkeypatch)
    web, dist = _make_web_dir(tmp_path)
    (web / "src").mkdir()
    (web / "src/App.tsx").write_text("export const A = 1\n", encoding="utf-8")
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("built dashboard", encoding="utf-8")
    stamp_product(tmp_path, "web", dist)
    assert not _web_ui_build_needed(web)
    return web, dist


@pytest.mark.platforms("posix")
class TestWebUIProductFreshness:
    """Exercise the compiler's real receipt writer and the launcher's reader."""

    def test_mtime_only_change_is_not_stale(self, web_product):
        web, _ = web_product
        future = time.time() + 10_000
        for path in (web / "src/App.tsx", web / "package.json"):
            os.utime(path, (future, future))
        assert not _web_ui_build_needed(web)

    @pytest.mark.parametrize("relative", ["web/src/App.tsx", "apps/shared/src/shared.ts", "package-lock.json"])
    def test_source_or_dependency_change_requires_a_rebuild(self, web_product, relative):
        web, _ = web_product
        changed = web.parent / relative
        changed.parent.mkdir(parents=True, exist_ok=True)
        changed.write_text("changed input", encoding="utf-8")
        assert _web_ui_build_needed(web)

    def test_modified_output_requires_a_rebuild(self, web_product):
        web, dist = web_product
        (dist / "index.html").write_text("damaged output", encoding="utf-8")
        assert _web_ui_build_needed(web)

    @pytest.mark.parametrize("missing", ["index.html", "hermes-build.json"])
    def test_missing_output_or_receipt_requires_a_rebuild(self, web_product, missing):
        web, dist = web_product
        (dist / missing).unlink()
        assert _web_ui_build_needed(web)

    def test_receipt_is_repeatable_and_independent_of_profile(self, web_product, tmp_path, monkeypatch):
        web, dist = web_product
        receipt = dist / "hermes-build.json"
        before = receipt.read_bytes()
        stamp_product(web.parent, "web", dist)
        assert receipt.read_bytes() == before
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "another-profile"))
        assert not _web_ui_build_needed(web)


@pytest.mark.platforms("posix")
def test_web_build_prepares_once_and_skips_a_current_product(source_products):
    root, acquired = source_products
    assert _build_web_ui(root / "web", fatal=True)
    assert [event["step"] for event in _events(root)] == ["deps", "web"]
    assert "esbuild" not in _events(root)[0]
    assert (root / "node_modules/ui-tui").exists()
    assert (root / "node_modules/web").exists()
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
