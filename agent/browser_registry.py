"""Browser provider registry: cloud browser backends registered by plugins via
:meth:`PluginContext.register_browser_provider`, consumed by ``tools.browser_tool_cloud._get_cloud_provider``.

Active-provider precedence (see :func:`_resolve`): ``browser.cloud_provider`` in config.yaml wins
regardless of ``is_available()`` (so the dispatcher surfaces a typed "X_API_KEY is not set" error
instead of silently switching); else the legacy auto-detect walk ``browser-use`` → ``browserbase``
filtered by availability; else ``None`` (local browser mode). There is no capability split here —
every provider implements the full :class:`agent.browser_provider.BrowserProvider` lifecycle.
"""

from __future__ import annotations

import logging
from typing import Optional

from agent.browser_provider import BrowserProvider
from agent.provider_registry import ProviderRegistry, is_available_safe

logger = logging.getLogger(__name__)


class _ScopedBrowserRegistry(ProviderRegistry[BrowserProvider]):
    """[CN-fork] Key every explicit *scope* with ``hermes_home_key()``.

    the shared :class:`ProviderRegistry` stores a scope verbatim while lookups
    default to ``hermes_home_key()``; on Windows a raw path scope (``scope=str(home)``,
    as plugin/tests pass) differs by drive-letter and component case from that lookup
    key, so a scoped registration would be invisible to ``get_provider()``/``merged()``.
    Normalize at every scope-taking entry point, then delegate upstream.
    """

    @staticmethod
    def _scope_key(scope: Optional[str]) -> Optional[str]:
        if scope is None:
            return None
        from hermes_constants import hermes_home_key
        return hermes_home_key(scope)

    def register(self, provider, *, scope=None):
        super().register(provider, scope=self._scope_key(scope))

    def merged(self, scope=None):
        return super().merged(self._scope_key(scope))

    def get_provider(self, name, *, scope=None):
        return super().get_provider(name, scope=self._scope_key(scope))

    def snapshot_registration(self, name, *, scope=None):
        return super().snapshot_registration(name, scope=self._scope_key(scope))

    def restore_registration(self, name, current, previous, *, scope=None):
        return super().restore_registration(name, current, previous, scope=self._scope_key(scope))

    def registry_generation(self, *, scope=None):
        return super().registry_generation(scope=self._scope_key(scope))


_registry: ProviderRegistry[BrowserProvider] = _ScopedBrowserRegistry(
    label="Browser", provider_cls=BrowserProvider, logger=logger,
)
_registry.export(globals())


# Auto-detect order when ``browser.cloud_provider`` is unset (historic order: Browser Use first because
# it covers both the managed Nous gateway and the direct API key path; Browserbase as the older
# direct-credentials fallback). Firecrawl is deliberately absent — see :func:`_resolve`.
_LEGACY_PREFERENCE = ("browser-use", "browserbase")


def _resolve(configured: Optional[str]) -> Optional[BrowserProvider]:
    """Resolve the active browser provider (rules in the module docstring).

    Intentionally NO "single-eligible shortcut" (unlike ``agent.web_search_registry._resolve``): only
    ``_LEGACY_PREFERENCE`` names are auto-eligible. Firecrawl shares its API key with the *web* extract
    plugin, so a user with ``FIRECRAWL_API_KEY`` must never be routed to a paid cloud browser without
    setting ``browser.cloud_provider``; the same gate applies to third-party browser-provider plugins.
    """
    snapshot = _registry.merged()
    if configured == "local":
        return None
    if configured:
        provider = snapshot.get(configured)
        if provider is not None:
            return provider
        logger.debug(
            "browser cloud_provider '%s' configured but not registered; falling back to auto-detect",
            configured,
        )
    for legacy in _LEGACY_PREFERENCE:
        provider = snapshot.get(legacy)
        if provider is not None and is_available_safe(
            provider, logger,
            "Browser provider %s.is_available() raised %s — treating as unavailable",
            level=logging.WARNING, exc_info=True,
        ):
            return provider
    return None


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
from typing import Dict  # noqa: F401,E402
from typing import List  # noqa: F401,E402
import threading  # noqa: F401,E402


_PLUGIN_COMPAT_LAZY = {
    'hermes_home_key': ('hermes_constants', 'hermes_home_key'),
}


def __getattr__(name):  # PEP 562 — lazy so no import cycles
    target = _PLUGIN_COMPAT_LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    from hermes_cli.plugin_compat import warn_once
    warn_once(__name__, name, *target)
    return getattr(importlib.import_module(target[0]), target[1])
# ---- END PLUGIN-COMPAT ----
