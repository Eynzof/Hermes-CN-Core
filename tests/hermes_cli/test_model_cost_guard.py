from decimal import Decimal

from agent.models_dev import ModelInfo
from agent.usage_pricing import PricingEntry
from hermes_cli.model_cost_guard import expensive_model_warning


def test_no_warning_when_known_prices_are_at_threshold():
    info = ModelInfo(
        id="edge/model",
        name="edge/model",
        family="",
        provider_id="test",
        cost_input=20.0,
        cost_output=100.0,
    )

    assert expensive_model_warning("edge/model", provider="test", model_info=info) is None






def test_openai_gpt55_pro_warns_for_nous_portal_pricing(monkeypatch):
    monkeypatch.setattr("agent.models_dev.get_model_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "agent.usage_pricing.fetch_endpoint_model_metadata",
        lambda base_url, api_key="": {
            "openai/gpt-5.5-pro": {
                "pricing": {
                    "prompt": "0.000025",
                    "completion": "0.000125",
                }
            }
        },
    )

    warning = expensive_model_warning("openai/gpt-5.5-pro", provider="nous")

    assert warning is not None
    assert warning.input_cost_per_million == Decimal("25.000000")
    assert warning.output_cost_per_million == Decimal("125.000000")
    assert "did you mean to select openai/gpt-5.5?" in warning.message


# P-028: the model-switch hot path calls the guard with allow_network=False.


def test_allow_network_false_warns_from_model_info_without_probing(monkeypatch):
    """With supplied model_info, the guard warns and never falls through to the
    live get_pricing_entry probe (which would hit the network)."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("get_pricing_entry must not be called when offline")

    monkeypatch.setattr("agent.usage_pricing.get_pricing_entry", _boom)

    info = ModelInfo(
        id="expensive/input",
        name="expensive/input",
        family="",
        provider_id="test",
        cost_input=50.0,
        cost_output=1.0,
    )

    warning = expensive_model_warning(
        "expensive/input", provider="test", model_info=info, allow_network=False
    )

    assert warning is not None
    assert warning.input_cost_per_million == Decimal("50")


def test_allow_network_false_skips_pricing_probe_and_fails_open(monkeypatch):
    """No model_info and no cached pricing → the guard skips the live probe
    entirely and fails open (no warning) instead of blocking on the network."""
    monkeypatch.setattr("agent.models_dev.get_model_info", lambda *_a, **_k: None)

    def _boom(*_args, **_kwargs):
        raise AssertionError("get_pricing_entry must not be called when offline")

    monkeypatch.setattr("agent.usage_pricing.get_pricing_entry", _boom)

    warning = expensive_model_warning(
        "provider/unknown", provider="openrouter", allow_network=False
    )

    assert warning is None
