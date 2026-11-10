"""GLM catalogs: discovered models merge with the presets, and the presets survive offline.

Regression for #193. The CN desktop's 「智谱 GLM · Coding Plan」 preset (``zai-coding-cn``, the
``open.bigmodel.cn/api/coding/paas/v4`` endpoint) listed 4 models capped at ``glm-5.2`` while the
endpoint served ``glm-5.3`` / ``glm-5.3-flash`` / ``glm-5.3-flashx``. The runtime feeds that
surface from two static sources, and BOTH had drifted below the curated row:

* the provider profile's ``fallback_models`` — what the picker serves when the live ``/models``
  probe cannot run (the routine case on a CN host, where models.dev is often unreachable), which
  still advertised a retired ``glm-4-9b`` and capped at ``glm-5.2``;
* the bundled models.dev snapshot's ``*-coding-plan`` entries, whose newest models.dev rows were
  older than the models the CLI's own Z.AI endpoint probe accepts as proof the plan serves them.

These tests pin the CONTRACTS between those sources — the offline floor never lags the curated
presets, discovery neither hides nor drops them, and the snapshot covers what the runtime can
actually run — never one release's model list.
"""

from __future__ import annotations

import re

import pytest

# Models the runtime can actually run (context-length metadata exists) that are plain GLM slugs,
# i.e. not an aggregator ``:variant`` SKU and not the bare ``glm`` family key.
_GLM_SLUG_RE = re.compile(r"^glm-\d[\w.\-]*$")

_ZAI_CODING_ENDPOINTS = (
    "https://api.z.ai/api/coding/paas/v4",
    "https://open.bigmodel.cn/api/coding/paas/v4",
)


def _offline_probe(*_args, **_kwargs):
    raise OSError("offline: no catalog egress in this test")


@pytest.fixture(autouse=True)
def _no_network_catalogs(monkeypatch):
    """Serve models.dev from the bundled snapshot and make every live probe fail."""
    from agent import models_dev

    snapshot = models_dev._load_bundled_snapshot()
    assert snapshot, "the bundled models.dev snapshot must ship and parse"
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda *_a, **_k: snapshot)
    monkeypatch.setattr("hermes_cli.models.fetch_api_models", _offline_probe)


@pytest.fixture
def zai_profile():
    """Resolve the registered Z.AI profile through the real discovery path."""
    import model_tools  # import triggers bundled provider-plugin discovery
    import providers

    profile = providers.get_provider_profile("zai")
    assert profile is not None, "zai provider profile must be registered"
    return profile


def test_offline_picker_row_leads_with_the_preset_catalog_head(zai_profile):
    """The offline picker list must not cap below the curated preset row.

    ``provider_model_ids`` serves the profile's ``fallback_models`` whenever the live probe
    yields nothing, so a stale fallback (``glm-5.2`` head, retired ``glm-4-9b``) silently
    downgraded every picker on an air-gapped/CN host even though the curated row already knew
    ``glm-5.3``.
    """
    from hermes_cli.models import provider_model_ids, static_catalog

    curated = static_catalog("zai")
    offline = provider_model_ids("zai")

    assert offline, "an offline zai picker must never be empty"
    assert offline[0] == curated[0], "the offline floor must lead with the preset row's flagship"


def test_offline_fallback_models_belong_to_the_curated_row(zai_profile):
    """The two static sources must agree: a fallback id absent from the curated row is drift.

    If the offline floor advertises an id the preset catalog never lists (a retired slug), the
    picker's contents depend on which source happened to answer that millisecond.
    """
    from hermes_cli.models import static_catalog

    curated = set(static_catalog("zai"))
    strays = [m for m in zai_profile.fallback_models if m not in curated]
    assert not strays, f"fallback_models not in the curated zai row: {strays}"


def test_discovered_models_merge_into_presets_instead_of_hiding_them(zai_profile):
    """``discover_models`` on: the live catalog is a UNION with the presets, both directions."""
    from hermes_cli.models import merge_profile_catalog, static_catalog

    curated = static_catalog("zai")
    live = ["glm-99-experimental", curated[0]]
    rows = merge_profile_catalog("zai", zai_profile, list(live))

    assert "glm-99-experimental" in rows, "a discovered model must never be hidden by the presets"
    missing = [m for m in curated if m not in rows]
    assert not missing, f"discovery dropped preset models: {missing}"


def test_empty_probe_still_serves_the_presets_offline(zai_profile):
    """``discover_models`` on but the endpoint is unreachable: the presets remain, nothing invented."""
    from hermes_cli.models import merge_profile_catalog, static_catalog

    rows = merge_profile_catalog("zai", zai_profile, None)
    curated = set(static_catalog("zai"))

    assert rows, "a failed probe must degrade to the preset catalog, never an empty picker"
    assert set(rows) <= curated, "the offline floor must stay inside the preset catalog"


def test_bundled_snapshot_covers_the_models_the_coding_plan_probe_trusts():
    """models.dev's offline rows must cover what Z.AI endpoint detection accepts.

    ``auth_zai_kimi`` probes these slugs in order when it decides whether a key belongs to a
    Coding Plan account; a snapshot missing one means the offline picker hides a model the
    runtime itself asserts the plan serves (``glm-5.3``/``glm-5.3-flash`` were the #193 gap).
    """
    from agent.models_dev import fetch_models_dev
    from hermes_cli.auth_zai_kimi import _ZAI_CODING_PROBE_MODELS

    snapshot = fetch_models_dev(allow_network=False)
    gaps = {}
    for provider_id in ("zai-coding-plan", "zhipuai-coding-plan"):
        served = set((snapshot.get(provider_id) or {}).get("models") or {})
        assert served, f"{provider_id} must be in the bundled snapshot"
        missing = [m for m in _ZAI_CODING_PROBE_MODELS if m not in served]
        if missing:
            gaps[provider_id] = missing
    assert not gaps, f"bundled coding-plan catalogs lag the auth probe: {gaps}"


def test_cn_coding_plan_snapshot_contains_the_reported_flashx_slug():
    """The CN endpoint's exact ``flashx`` id must not be replaced by ``highspeed``."""
    from agent.models_dev import fetch_models_dev

    snapshot = fetch_models_dev(allow_network=False)
    models = snapshot["zhipuai-coding-plan"]["models"]

    assert "glm-5.3-flashx" in models
    assert models["glm-5.3-flashx"]["id"] == "glm-5.3-flashx"


def test_offline_catalog_covers_every_glm_the_runtime_can_run():
    """Every GLM slug with context metadata (the models a turn can actually use) is offered offline.

    Curated row plus bundled snapshot are what a machine without models.dev access sees; a slug
    the runtime prices/limits but the catalog omits is unrunnable from the UI.
    """
    from agent.model_metadata import DEFAULT_CONTEXT_LENGTHS
    from agent.models_dev import fetch_models_dev
    from hermes_cli.models import static_catalog

    offered = set(static_catalog("zai"))
    snapshot = fetch_models_dev(allow_network=False)
    offered |= set((snapshot.get("zai") or {}).get("models") or {})
    runnable = {mid for mid in DEFAULT_CONTEXT_LENGTHS if _GLM_SLUG_RE.match(mid)}

    assert runnable, "sanity: the runtime must know some GLM context windows"
    missing = sorted(runnable - offered)
    assert not missing, f"runnable GLM models absent from the offline catalog: {missing}"


def test_desktop_preset_provider_row_keeps_its_models_when_the_probe_fails():
    """A preset provider entry (``providers.zai-coding-cn``) degrades to its declared models.

    This is the config shape the CN desktop writes for 「GLM Coding Plan」: a declared ``models:``
    list plus ``discover_models: true``. Offline the row must still offer that list.
    """
    from hermes_cli.model_switch_providers import list_authenticated_providers

    presets = ["glm-5.2", "glm-5.1", "glm-4.7", "glm-4.6"]
    providers_rows = list_authenticated_providers(
        current_provider="zai-coding-cn",
        user_providers={
            "zai-coding-cn": {
                "name": "智谱 GLM · Coding Plan",
                "base_url": _ZAI_CODING_ENDPOINTS[1],
                "key_env": "GLM_CODING_API_KEY",
                "models": presets,
                "discover_models": True,
            }
        },
        custom_providers=[],
        max_models=50,
    )

    row = next(r for r in providers_rows if r["slug"] == "zai-coding-cn")
    assert set(row["models"]) == set(presets), "a failed probe must keep the declared presets"


def test_desktop_preset_provider_row_merges_discovered_with_presets(monkeypatch):
    """Same entry with a reachable ``/models``: discovered models lead, presets are never dropped.

    The exact #193 contract — a probed catalog must not hide the entry's declared models. A preset
    id the endpoint's own listing omits (partial rollout, a plan that hides "flashx"/"highspeed"
    SKUs) stays selectable, and the endpoint's order still leads.
    """
    from hermes_cli.model_switch_providers import list_authenticated_providers

    discovered = ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-5.2", "glm-5.1", "glm-4.7"]
    presets = ["glm-5.2", "glm-5.1", "glm-4.7", "glm-4.6"]
    monkeypatch.setattr(
        "hermes_cli.model_switch_providers._discover_endpoint_models",
        lambda *_a, **_k: (list(discovered), False),
    )

    providers_rows = list_authenticated_providers(
        current_provider="zai-coding-cn",
        user_providers={
            "zai-coding-cn": {
                "name": "智谱 GLM · Coding Plan",
                "base_url": _ZAI_CODING_ENDPOINTS[1],
                "key_env": "GLM_CODING_API_KEY",
                "models": presets,
                "discover_models": True,
            }
        },
        custom_providers=[],
        max_models=50,
    )

    row = next(r for r in providers_rows if r["slug"] == "zai-coding-cn")
    assert row["models"][: len(discovered)] == discovered, "the endpoint's own order must lead"
    assert "glm-4.6" in row["models"], "a discovered catalog must not hide a declared preset"
    assert set(presets) <= set(row["models"])

def test_custom_provider_persists_the_merged_catalog_after_a_successful_probe(monkeypatch):
    """The on-disk discovery cache must not reintroduce the offline subset bug.

    A reachable endpoint can omit a preset during a partial rollout. The picker keeps that
    preset, and the value persisted for later no-probe launches must be the same union.
    """
    from hermes_cli.model_switch_providers import list_authenticated_providers

    discovered = ["glm-5.3", "glm-5.2"]
    presets = ["glm-5.2", "glm-5.3-flashx"]
    saved = {}
    monkeypatch.setattr(
        "hermes_cli.model_switch_providers._discover_endpoint_models",
        lambda *_a, **_k: (list(discovered), False),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch_providers._save_discovered_models_to_config",
        lambda _api_url, model_ids, **_kwargs: saved.setdefault("models", list(model_ids)),
    )

    list_authenticated_providers(
        current_provider="glm-custom",
        user_providers={},
        custom_providers=[{
            "name": "glm-custom",
            "base_url": _ZAI_CODING_ENDPOINTS[1],
            "api_key": "test-key",
            "models": presets,
            "discover_models": True,
        }],
        max_models=50,
    )

    assert saved["models"] == ["glm-5.3", "glm-5.2", "glm-5.3-flashx"]


def test_failed_probe_does_not_mark_presets_as_a_discovered_cache(monkeypatch):
    """A failed request must not relabel an offline fallback as a successful discovery."""
    from hermes_cli.model_switch_providers import list_authenticated_providers

    saved = {}
    monkeypatch.setattr(
        "hermes_cli.model_switch_providers._discover_endpoint_models",
        lambda *_a, **_k: (None, False),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch_providers._save_discovered_models_to_config",
        lambda *_a, **_kwargs: saved.setdefault("called", True),
    )

    list_authenticated_providers(
        current_provider="glm-custom",
        user_providers={},
        custom_providers=[{
            "name": "glm-custom",
            "base_url": _ZAI_CODING_ENDPOINTS[1],
            "api_key": "test-key",
            "models": ["glm-5.3-flashx"],
            "discover_models": True,
        }],
        max_models=50,
    )

    assert not saved
