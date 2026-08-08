"""Tests for Tencent TokenHub provider support (Hy3 Preview)."""

import orjson
import os

import pytest

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    resolve_provider,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
)

# Other provider env vars to clear during auto-detection tests
_OTHER_PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
    "XAI_API_KEY", "KIMI_API_KEY", "KIMI_CN_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY", "HF_TOKEN", "GLM_API_KEY", "ZAI_API_KEY",
    "XIAOMI_API_KEY", "OPENROUTER_API_KEY", "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN", "GITHUB_TOKEN", "ARCEEAI_API_KEY",
)

# =============================================================================
# Provider Registry
# =============================================================================

class TestTencentTokenhubProviderRegistry:
    """Verify tencent-tokenhub is registered correctly in the PROVIDER_REGISTRY."""

    def test_registered(self):
        assert "tencent-tokenhub" in PROVIDER_REGISTRY

# =============================================================================
# Aliases
# =============================================================================

class TestTencentTokenhubAliases:
    """All aliases should resolve to 'tencent-tokenhub'."""

    @pytest.mark.parametrize("alias", [
        "tencent-tokenhub", "tencent", "tokenhub", "tencent-cloud", "tencentmaas",
    ])
    def test_alias_resolves(self, alias, monkeypatch):
        for key in _OTHER_PROVIDER_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("TOKENHUB_API_KEY", "sk-test-key-12345678")
        assert resolve_provider(alias) == "tencent-tokenhub"

    def test_normalize_provider_models_py(self):
        from hermes_cli.models import normalize_provider
        assert normalize_provider("tencent") == "tencent-tokenhub"
        assert normalize_provider("tokenhub") == "tencent-tokenhub"
        assert normalize_provider("tencent-cloud") == "tencent-tokenhub"
        assert normalize_provider("tencentmaas") == "tencent-tokenhub"

    def test_normalize_provider_providers_py(self):
        from hermes_cli.providers import normalize_provider
        assert normalize_provider("tencent") == "tencent-tokenhub"
        assert normalize_provider("tokenhub") == "tencent-tokenhub"
        assert normalize_provider("tencent-cloud") == "tencent-tokenhub"
        assert normalize_provider("tencentmaas") == "tencent-tokenhub"

# =============================================================================
# Auto-detection
# =============================================================================

class TestTencentTokenhubAutoDetection:
    """Setting TOKENHUB_API_KEY should auto-detect the provider."""

    def test_auto_detect(self, monkeypatch):
        for var in _OTHER_PROVIDER_KEYS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("TOKENHUB_API_KEY", "sk-tokenhub-test-12345678")
        provider = resolve_provider("auto")
        assert provider == "tencent-tokenhub"

# =============================================================================
# Credentials
# =============================================================================

class TestTencentTokenhubCredentials:
    """Test credential resolution for the tencent-tokenhub provider."""

    def test_status_configured(self, monkeypatch):
        monkeypatch.setenv("TOKENHUB_API_KEY", "sk-test-12345678")
        status = get_api_key_provider_status("tencent-tokenhub")
        assert status["configured"]

    def test_status_not_configured(self, monkeypatch):
        monkeypatch.delenv("TOKENHUB_API_KEY", raising=False)
        status = get_api_key_provider_status("tencent-tokenhub")
        assert not status["configured"]

    def test_resolve_credentials(self, monkeypatch):
        monkeypatch.setenv("TOKENHUB_API_KEY", "sk-test-12345678")
        monkeypatch.delenv("TOKENHUB_BASE_URL", raising=False)
        creds = resolve_api_key_provider_credentials("tencent-tokenhub")
        assert creds["api_key"] == "sk-test-12345678"
        assert creds["base_url"] == "https://tokenhub.tencentmaas.com/v1"

    def test_openrouter_key_does_not_make_tokenhub_configured(self, monkeypatch):
        """OpenRouter users should NOT see tencent-tokenhub as configured."""
        monkeypatch.delenv("TOKENHUB_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        status = get_api_key_provider_status("tencent-tokenhub")
        assert not status["configured"]

    def test_custom_base_url_override(self, monkeypatch):
        monkeypatch.setenv("TOKENHUB_API_KEY", "sk-test-12345678")
        monkeypatch.setenv("TOKENHUB_BASE_URL", "https://custom.tokenhub.example/v1")
        creds = resolve_api_key_provider_credentials("tencent-tokenhub")
        assert creds["base_url"] == "https://custom.tokenhub.example/v1"

# =============================================================================
# Model catalog
# =============================================================================

class TestTencentTokenhubModelCatalog:
    """Tencent TokenHub static model list."""

    def test_static_model_list_exists(self):
        from hermes_cli.models import _PROVIDER_MODELS
        assert "tencent-tokenhub" in _PROVIDER_MODELS
        assert len(_PROVIDER_MODELS["tencent-tokenhub"]) >= 1

    def test_default_model(self):
        from hermes_cli.models import get_default_model_for_provider
        assert get_default_model_for_provider("tencent-tokenhub") == "hy3-preview"

# =============================================================================
# CANONICAL_PROVIDERS (hermes model picker)
# =============================================================================

class TestTencentTokenhubCanonicalProvider:
    """Tencent TokenHub appears in the interactive model picker."""

    def test_in_canonical_providers(self):
        from hermes_cli.models import CANONICAL_PROVIDERS
        slugs = [p.slug for p in CANONICAL_PROVIDERS]
        assert "tencent-tokenhub" in slugs

# =============================================================================
# OpenRouter / Nous Portal curated lists
# =============================================================================

# =============================================================================
# Model normalization
# =============================================================================

class TestTencentTokenhubNormalization:
    """Model name normalization — Tencent TokenHub is a direct provider
    not in _MATCHING_PREFIX_STRIP_PROVIDERS, so names pass through as-is.
    """

    def test_bare_name_passthrough(self):
        """hy3-preview should remain unchanged when targeting tencent-tokenhub."""
        from hermes_cli.model_normalize import normalize_model_for_provider
        result = normalize_model_for_provider("hy3-preview", "tencent-tokenhub")
        assert result == "hy3-preview"

    def test_vendor_prefixed_passthrough(self):
        """tencent/hy3-preview is not stripped since tencent-tokenhub is not in
        _MATCHING_PREFIX_STRIP_PROVIDERS — the slash survives."""
        from hermes_cli.model_normalize import normalize_model_for_provider
        result = normalize_model_for_provider("tencent/hy3-preview", "tencent-tokenhub")
        # Direct providers not in any special set → passthrough
        assert result == "tencent/hy3-preview"

    @pytest.mark.parametrize("empty_input", ["", None, "   "])
    def test_normalize_empty_and_none(self, empty_input):
        """None, empty, and whitespace-only inputs return empty string."""
        from hermes_cli.model_normalize import normalize_model_for_provider
        result = normalize_model_for_provider(empty_input, "tencent-tokenhub")
        assert result == "" or result.strip() == ""

# =============================================================================
# Provider label
# =============================================================================

# =============================================================================
# URL mapping
# =============================================================================

class TestTencentTokenhubURLMapping:
    """Test URL → provider inference for Tencent TokenHub endpoints."""

    def test_provider_prefixes(self):
        from agent.model_metadata import _PROVIDER_PREFIXES
        assert "tencent-tokenhub" in _PROVIDER_PREFIXES
        assert "tencent" in _PROVIDER_PREFIXES
        assert "tokenhub" in _PROVIDER_PREFIXES

    def test_infer_from_url(self):
        from agent.model_metadata import _infer_provider_from_url
        assert _infer_provider_from_url("https://tokenhub.tencentmaas.com/v1") == "tencent-tokenhub"

# =============================================================================
# Context length
# =============================================================================

class TestTencentTokenhubContextLength:
    """hy3-preview has a context-length entry registered.

    Asserting the relationship (registered + ≥ 4096) instead of a
    specific value, per AGENTS.md "Don't write change-detector tests".
    The previous version of this class pinned an exact integer that
    broke whenever Tencent / OpenRouter bumped the published context
    window (#22268).
    """

    def test_hy3_preview_has_registered_context_length(self):
        from agent.model_metadata import get_model_context_length
        ctx = get_model_context_length("hy3-preview")
        assert isinstance(ctx, int)
        assert ctx >= 4096, f"hy3-preview context length looks unset/wrong: {ctx}"

# =============================================================================
# providers.py (unified provider module)
# =============================================================================

class TestTencentTokenhubProvidersModule:
    """Test Tencent TokenHub in the unified providers module."""

    def test_alias_resolves(self):
        from hermes_cli.providers import normalize_provider
        assert normalize_provider("tencent") == "tencent-tokenhub"
        assert normalize_provider("tokenhub") == "tencent-tokenhub"

# =============================================================================
# Auxiliary client
# =============================================================================

# =============================================================================
# Doctor
# =============================================================================

class TestTencentTokenhubDoctor:
    """Verify hermes doctor recognizes Tencent TokenHub env vars."""

    def test_provider_env_hints(self):
        from hermes_cli.doctor import _PROVIDER_ENV_HINTS
        assert "TOKENHUB_API_KEY" in _PROVIDER_ENV_HINTS

# =============================================================================
# Agent init (no SyntaxError, correct api_mode)
# =============================================================================

class TestTencentTokenhubAgentInit:
    """Verify the agent can be constructed with tencent-tokenhub provider without errors."""

    def test_no_syntax_errors(self):
        """Importing run_agent with tencent-tokenhub should not raise."""
        import importlib
        importlib.import_module("run_agent")

    def test_api_mode_is_chat_completions(self):
        from hermes_cli.providers import HERMES_OVERLAYS, TRANSPORT_TO_API_MODE
        overlay = HERMES_OVERLAYS["tencent-tokenhub"]
        api_mode = TRANSPORT_TO_API_MODE[overlay.transport]
        assert api_mode == "chat_completions"

# =============================================================================
# CLI model flow dispatch (main.py)
# =============================================================================

# =============================================================================
# Remote model catalog (model-catalog.json)
# =============================================================================

# =============================================================================
# determine_api_mode (providers.py)
# =============================================================================

class TestTencentTokenhubApiMode:
    """Verify determine_api_mode routes tencent-tokenhub correctly."""

    def test_determine_api_mode_direct(self):
        from hermes_cli.providers import determine_api_mode
        mode = determine_api_mode("tencent-tokenhub")
        assert mode == "chat_completions"

    def test_determine_api_mode_with_base_url(self):
        from hermes_cli.providers import determine_api_mode
        mode = determine_api_mode("tencent-tokenhub", "https://tokenhub.tencentmaas.com/v1")
        assert mode == "chat_completions"

    def test_determine_api_mode_via_alias(self):
        from hermes_cli.providers import determine_api_mode
        mode = determine_api_mode("tencent")
        assert mode == "chat_completions"

# =============================================================================
# _KNOWN_PROVIDER_NAMES (models.py)
# =============================================================================

class TestTencentTokenhubKnownProviderNames:
    """Verify tencent-tokenhub and its aliases are recognized as valid
    provider names for the ``provider:model`` syntax.
    """

    def test_canonical_id_known(self):
        from hermes_cli.models import _KNOWN_PROVIDER_NAMES
        assert "tencent-tokenhub" in _KNOWN_PROVIDER_NAMES

    @pytest.mark.parametrize("alias", [
        "tencent", "tokenhub", "tencent-cloud", "tencentmaas",
    ])
    def test_alias_known(self, alias):
        from hermes_cli.models import _KNOWN_PROVIDER_NAMES
        assert alias in _KNOWN_PROVIDER_NAMES

