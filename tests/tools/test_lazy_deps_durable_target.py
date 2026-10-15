"""Previously installed durable packages remain readable after the PM migration.

Bootstrap only activates existing targets, behind the core import paths. New
dependencies belong to PM; an old target never overrides its install policy.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from hermes_bootstrap import activate_durable_lazy_target


@pytest.mark.parametrize("configured", [False, True])
def test_absent_target_does_not_create_or_activate_a_directory(monkeypatch, tmp_path, configured):
    target = tmp_path / "missing-target"
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(target) if configured else "")
    before = list(sys.path)
    activate_durable_lazy_target()
    assert sys.path == before
    assert not target.exists()


def test_existing_target_and_pth_are_appended_and_importable(monkeypatch, tmp_path):
    target, extension = tmp_path / "lazy", tmp_path / "extension"
    target.mkdir()
    extension.mkdir()
    (target / "extension.pth").write_text(str(extension) + "\n", encoding="utf-8")
    (extension / "durable_fixture_sdk.py").write_text("VALUE = 42\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(target))
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    try:
        activate_durable_lazy_target()
        activate_durable_lazy_target()
        assert sys.path[:len(before)] == before
        assert sys.path[len(before):] == [str(target), str(extension)]
        assert importlib.import_module("durable_fixture_sdk").VALUE == 42
    finally:
        sys.modules.pop("durable_fixture_sdk", None)


def test_durable_package_cannot_shadow_the_core_module(monkeypatch, tmp_path):
    core, target = tmp_path / "core", tmp_path / "lazy"
    core.mkdir()
    target.mkdir()
    for directory, value in ((core, "core"), (target, "shadow")):
        (directory / "durable_fixture_collision.py").write_text(
            f"SOURCE = {value!r}\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(core))
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(target))
    try:
        activate_durable_lazy_target()
        assert importlib.import_module("durable_fixture_collision").SOURCE == "core"
    finally:
        sys.modules.pop("durable_fixture_collision", None)


@pytest.mark.parametrize("policy", ["environment", "config"])
def test_legacy_target_never_bypasses_pm_install_policy(monkeypatch, tmp_path, policy):
    import pm

    target = tmp_path / "lazy"
    target.mkdir()
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(target))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1" if policy == "environment" else "")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "security:\n  allow_lazy_installs: false\n" if policy == "config" else "{}\n",
        encoding="utf-8",
    )
    assert not pm.lazy_installs_allowed()
