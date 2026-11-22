"""Assign the picker and the runtime the same in-memory custom provider IDs."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from hermes_cli.route_identity import normalize_route_base_url
from hermes_cli.providers import custom_provider_aliases, custom_provider_slug


def display_prefix(name: str) -> str:
    for separator in ("—", " - "):
        if separator in name:
            return name.split(separator, 1)[0].strip() or name
    return name


def route_signature(entry: dict) -> tuple:
    from hermes_cli.config import _canonical_api_mode, normalize_extra_headers

    name = str(entry.get("name") or "").strip()
    url = str(entry.get("base_url") or entry.get("url") or entry.get("api") or "").strip().rstrip("/")
    return (
        normalize_route_base_url(url), display_prefix(name).lower(),
        str(entry.get("api_key") or "").strip(),
        str(entry.get("key_env") or entry.get("api_key_env") or "").strip(),
        _canonical_api_mode(str(entry.get("api_mode") or entry.get("transport") or "")),
        tuple(sorted(normalize_extra_headers(entry.get("extra_headers")).items())),
        str(entry.get("key_cmd") or "").strip(),
    )


@dataclass(eq=False)
class CustomRoute:
    slug: str
    display_name: str
    keyed: bool
    base_url: str
    entries: list[dict] = field(default_factory=list, repr=False)
    aliases: set[str] = field(default_factory=set)
    warning: str = ""

    @property
    def conflict(self) -> bool:
        return bool(self.warning)


def custom_routes(entries, mapped=None) -> list[CustomRoute]:
    """Group per-model declarations and assign suffixes in configuration order."""
    grouped = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        signature = route_signature(entry)
        if not name or not signature[0]:
            continue
        key = str(entry.get("provider_key") or "").strip()
        display = display_prefix(name)
        slug = custom_provider_slug(display, key)
        group_id = (signature, slug if key else "")
        route = grouped.get(group_id)
        if route is None:
            route = grouped[group_id] = CustomRoute(slug, display, bool(key), str(entry.get("base_url") or entry.get("url") or entry["api"]).strip().rstrip("/"))
        route.entries.append(entry)
        route.aliases.update(custom_provider_aliases(name, key))

    routes = list(grouped.values())
    reserved = {route.slug for route in routes}
    first_names = set()
    next_suffix = {}
    for route in routes:
        base = route.slug
        if route.keyed:
            continue
        if base in first_names:
            number = next_suffix.get(base, 2)
            while f"{base}-{number}" in reserved:
                number += 1
            route.slug = f"{base}-{number}"
            next_suffix[base] = number + 1
            reserved.add(route.slug)
        first_names.add(base)

    owners = defaultdict(list)
    for route in routes:
        owners[route.slug].append(route)
    # Before numbered routes, inference checked mapping names before the
    # legacy list. Keep that saved destination while blocking a new selection
    # if the old picker and inference disagree about the same name.
    old_mapped = {}
    indexed = {(r.slug, route_signature(r.entries[0])): r for r in routes if r.keyed}
    for key, entry in (mapped.items() if isinstance(mapped, dict) else ()):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or key)
        target = indexed.get((custom_provider_slug(str(key)), route_signature({**entry, "name": name})))
        if target:
            for alias in custom_provider_aliases(name, str(key)):
                old_mapped.setdefault(alias, target)
    old_list = {}
    for route in routes:
        for alias in route.aliases:
            old_list.setdefault(alias, route)
    for slug, claimed in owners.items():
        prior = [old_mapped.get(slug), old_list.get(slug)]
        affected = list(dict.fromkeys([*claimed, *(r for r in prior if r)]))
        if len(affected) > 1:
            warning = conflict_message(slug, affected)
            for route in claimed:
                route.warning = warning
    return routes


def find_custom_route(requested: str, routes: list[CustomRoute]) -> CustomRoute | None:
    name = requested.strip().lower()
    if not name:
        return None
    canonical = custom_provider_slug(name)
    for route in routes:
        if route.slug == canonical:
            return route
    return next((route for route in routes if name in route.aliases), None)


def conflict_message(slug: str, affected: list[CustomRoute]) -> str:
    from urllib.parse import urlsplit

    labels = []
    for route in affected:
        address = urlsplit(route.base_url).netloc.rsplit("@", 1)[-1]
        labels.append(f"{route.display_name} ({route.slug}, {address})")
    return f"Conflicting provider {slug}: {', '.join(labels)}. Set distinct provider_key values or rename one endpoint."


def saved_route_warning(provider: str) -> str:
    """Validate a deferred selection before recording it on the session."""
    if not provider:
        return ""
    from hermes_cli.config import get_compatible_custom_providers, load_config_readonly

    config = load_config_readonly()
    entries = get_compatible_custom_providers(config)
    route = find_custom_route(provider, custom_routes(entries, config.get("providers")))
    return route.warning if route else ""
