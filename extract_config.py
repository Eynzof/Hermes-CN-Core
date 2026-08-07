#!/usr/bin/env python3
"""Extract the current Hermes LLM configuration as JSON.

Everything is resolved through the project's own code paths — no hard-coded
paths, no ad-hoc parsing:

  * config          -> hermes_cli.config.load_config() / get_config_path() /
                       get_env_path()   (honours HERMES_HOME and the
                       platform-native default home)
  * live runtime    -> hermes_cli.runtime_provider.resolve_runtime_provider()
                       (provider, base_url, api_key, api_mode — the same
                       resolver run_agent uses)
  * context window  -> agent.model_metadata.get_model_context_length()
  * max output      -> config ``model.max_tokens`` first, else the models.dev
                       catalog (agent.models_dev) — ``limit.output``
  * api type        -> api_mode from the runtime resolution
                       (chat_completions | codex_responses | anthropic_messages)
  * thinking effort -> hermes_constants.resolve_reasoning_config()

Usage:
    python extract_config.py                 # JSON to stdout (keys included)
    python extract_config.py --redact        # mask the api key value
    python extract_config.py -o out.json     # write to a file
    python extract_config.py --network       # allow network metadata refresh
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the script location the import root so it works from any CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import hermes_constants
    import agent.model_metadata
    import agent.models_dev
    from hermes_cli import config as hconfig
    from hermes_cli.runtime_provider import resolve_runtime_provider
except Exception as exc:  # pragma: no cover - import guard
    print(
        json.dumps(
            {
                "error": "failed to import project modules",
                "detail": f"{type(exc).__name__}: {exc}",
                "hint": "run with the project interpreter, e.g. "
                f"`{os.path.join(_HERE, '.venv', 'Scripts', 'python.exe')} "
                f"{os.path.abspath(__file__)}`",
            },
            indent=2,
        )
    )
    sys.exit(1)


API_TYPE_LABELS = {
    "chat_completions": "openai chat completion",
    "codex_responses": "openai responses",
    "anthropic_messages": "anthropic messages",
    "": "auto",
}


def _model_section(cfg: dict) -> dict:
    """Normalise the ``model`` section (string form or dict form)."""
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        return {"default": model_cfg.strip(), "provider": "", "base_url": ""}
    if isinstance(model_cfg, dict):
        return model_cfg
    return {}


def _max_tokens_from_config(model_cfg: dict):
    """Config ``model.max_tokens`` — what agent_init actually applies."""
    raw = model_cfg.get("max_tokens")
    try:
        val = int(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def _catalog_max_output(model: str, runtime: dict, allow_network: bool):
    """models.dev catalog ``limit.output`` for the model (offline-first).

    Tries the runtime provider first, then scans every provider in the
    catalog for a matching model id (preferring a provider whose base URL
    host matches the runtime base URL).
    """
    try:
        data = agent.models_dev.fetch_models_dev(allow_network=allow_network)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    model_l = model.strip().lower()
    runtime_host = ""
    base_url = (runtime or {}).get("base_url") or ""
    if base_url:
        try:
            from urllib.parse import urlparse

            runtime_host = urlparse(base_url).netloc.lower()
        except Exception:
            runtime_host = ""

    def _entry_output(entry) -> int | None:
        if not isinstance(entry, dict):
            return None
        limit = entry.get("limit")
        if not isinstance(limit, dict):
            return None
        try:
            out = int(limit.get("output") or 0)
        except (TypeError, ValueError):
            out = 0
        return out if out > 0 else None

    best: tuple[int, int | None] | None = None  # (host_priority, output)
    for provider_id, provider_data in data.items():
        if not isinstance(provider_data, dict):
            continue
        models = provider_data.get("models")
        if not isinstance(models, dict):
            continue
        for mid, entry in models.items():
            if str(mid).strip().lower() != model_l:
                continue
            out = _entry_output(entry)
            if out is None:
                continue
            host = ""
            api = provider_data.get("api")
            if isinstance(api, str) and api:
                try:
                    from urllib.parse import urlparse

                    host = urlparse(api).netloc.lower()
                except Exception:
                    host = ""
            priority = 1 if (runtime_host and host == runtime_host) else 0
            if best is None or priority > best[0]:
                best = (priority, out)
    return best[1] if best else None


def _api_key_env_names(runtime: dict) -> list[str]:
    """Env var names that could hold the credential for this provider."""
    names: list[str] = []
    provider = str((runtime or {}).get("provider") or "").strip()
    if provider:
        try:
            pi = agent.models_dev.get_provider_info(provider)
            if pi and pi.env:
                names.extend(pi.env)
        except Exception:
            pass
        try:
            from hermes_cli import providers as pmod

            overlay = pmod.HERMES_OVERLAYS.get(provider)
            if overlay:
                names.extend(overlay.extra_env_vars)
        except Exception:
            pass
    if runtime and runtime.get("source"):
        src = str(runtime["source"])
        if src.startswith("env:"):
            names.append(src[4:])
    # de-dup, keep order
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract(redact: bool = False, allow_network: bool = False) -> dict:
    """Resolve and return the effective LLM configuration."""
    cfg = hconfig.load_config()
    model_cfg = _model_section(cfg)
    model = str(model_cfg.get("default") or "").strip()
    provider = str(model_cfg.get("provider") or "").strip()

    # 1) Live runtime (same resolver run_agent uses).
    runtime: dict = {}
    runtime_error: str = ""
    try:
        runtime = resolve_runtime_provider(
            requested=provider or None, target_model=model or None
        ) or {}
    except Exception as exc:
        runtime_error = f"{type(exc).__name__}: {exc}"

    rt_provider = str(runtime.get("provider") or provider or "").strip()
    rt_base_url = str(runtime.get("base_url") or "").strip()
    rt_api_key = runtime.get("api_key")
    if not isinstance(rt_api_key, str):
        rt_api_key = ""
    api_key = rt_api_key.strip()
    api_mode = str(runtime.get("api_mode") or "").strip()

    # 2) Context window — canonical metadata resolution, offline.
    context_length: int | None = None
    try:
        ctx_cfg = model_cfg.get("context_length")
        context_length = agent.model_metadata.get_model_context_length(
            model,
            base_url=rt_base_url,
            api_key=api_key,
            config_context_length=int(ctx_cfg) if ctx_cfg is not None else None,
            provider=rt_provider,
            allow_network=allow_network,
        )
    except Exception:
        context_length = None

    # 3) Max output tokens — config override, else models.dev catalog.
    max_output_config = _max_tokens_from_config(model_cfg)
    max_output_catalog = _catalog_max_output(model, runtime, allow_network)
    max_output = max_output_config if max_output_config is not None else max_output_catalog

    # 4) Thinking effort.
    reasoning: dict | None = None
    try:
        reasoning = hermes_constants.resolve_reasoning_config(cfg, model)
    except Exception:
        reasoning = None
    agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    raw_reasoning_effort = agent_cfg.get("reasoning_effort", "")
    reasoning_overrides = agent_cfg.get("reasoning_overrides") or {}

    # 5) Catalog facts for cross-check / provenance.  ``openai-api`` and
    # similar Hermes-side provider slugs have no models.dev entry, so fall
    # back to scanning every provider in the catalog for the model id and
    # prefer the provider whose base-URL host matches the runtime one.
    def _info_to_dict(info) -> dict:
        return {
            "provider_id": info.provider_id,
            "name": info.name,
            "family": info.family,
            "context_window": info.context_window,
            "max_output": info.max_output,
            "reasoning": info.reasoning,
            "tool_call": info.tool_call,
            "interleaved": info.interleaved,
            "knowledge_cutoff": info.knowledge_cutoff,
        }

    catalog: dict | None = None
    try:
        info = agent.models_dev.get_model_info(rt_provider, model, allow_network=allow_network)
        if info:
            catalog = _info_to_dict(info)
        else:
            scan = agent.models_dev.fetch_models_dev(allow_network=allow_network)
            model_l = model.strip().lower()
            match = None
            match_host_priority = -1
            for pid, pdata in scan.items():
                if not isinstance(pdata, dict):
                    continue
                ms = pdata.get("models")
                if not isinstance(ms, dict):
                    continue
                for mid, entry in ms.items():
                    if str(mid).strip().lower() != model_l:
                        continue
                    if isinstance(entry, dict) and isinstance(entry.get("limit"), dict):
                        try:
                            ctx = int(entry["limit"].get("context") or 0)
                        except (TypeError, ValueError):
                            ctx = 0
                        if ctx <= 0:
                            continue
                        api = pdata.get("api")
                        host_priority = 0
                        if isinstance(api, str) and rt_base_url:
                            try:
                                from urllib.parse import urlparse

                                if (
                                    urlparse(api).netloc.lower()
                                    == urlparse(rt_base_url).netloc.lower()
                                ):
                                    host_priority = 1
                            except Exception:
                                host_priority = 0
                        if host_priority > match_host_priority:
                            match = (pid, pdata, entry)
                            match_host_priority = host_priority
                if match_host_priority == 1:
                    break
            if match:
                pid, pdata, entry = match
                catalog = {
                    "provider_id": pid,
                    "name": entry.get("name") or model,
                    "family": entry.get("family") or "",
                    "context_window": int(entry["limit"].get("context") or 0),
                    "max_output": int(entry["limit"].get("output") or 0) or None,
                    "reasoning": bool(entry.get("reasoning")),
                    "tool_call": bool(entry.get("tool_call")),
                    "interleaved": entry.get("interleaved"),
                    "knowledge_cutoff": entry.get("knowledge") or "",
                    "catalog_api": pdata.get("api") or "",
                }
    except Exception:
        catalog = None

    provider_info: dict | None = None
    try:
        pi = agent.models_dev.get_provider_info(rt_provider)
        if pi:
            provider_info = {"name": pi.name, "env": list(pi.env), "api": pi.api}
        else:
            # Fall back to the Hermes overlay (generic transport/auth for the
            # provider slug) plus a models.dev provider whose base URL host
            # matches the runtime.  NOTE: the overlay ``transport`` is only a
            # generic default — the effective wire format is the runtime
            # ``api_mode`` above.
            from hermes_cli import providers as pmod

            overlay = pmod.HERMES_OVERLAYS.get(rt_provider)
            entry: dict = {"name": rt_provider}
            if overlay:
                entry["overlay_transport"] = overlay.transport
                entry["auth_type"] = overlay.auth_type
                if overlay.extra_env_vars:
                    entry["env"] = list(overlay.extra_env_vars)
            for pid, pdata in agent.models_dev.fetch_models_dev(
                allow_network=allow_network
            ).items():
                if not isinstance(pdata, dict) or not isinstance(pdata.get("api"), str):
                    continue
                if rt_base_url and pdata["api"].rstrip("/") == rt_base_url.rstrip("/"):
                    entry["catalog_provider"] = pid
                    entry["name"] = pdata.get("name") or entry["name"]
                    if pdata.get("env"):
                        entry["env"] = sorted(
                            set(entry.get("env") or []) | set(pdata["env"])
                        )
                    break
            provider_info = entry
    except Exception:
        provider_info = None

    return {
        "model_name": model,
        "provider": rt_provider,
        "base_url": rt_base_url,
        "api_key": (("<redacted>" if redact else api_key) or None) or None,
        "api_key_present": bool(api_key),
        "api_key_source": str(runtime.get("source") or "") if runtime else "",
        "api_key_env_names": _api_key_env_names(runtime),
        "max_context_size": context_length,
        "max_output_token": max_output,
        "max_output_source": (
            "config:model.max_tokens"
            if max_output_config is not None
            else "models.dev catalog"
            if max_output_catalog is not None
            else None
        ),
        "api_type": API_TYPE_LABELS.get(api_mode, api_mode or None),
        "api_mode": api_mode or None,
        "thinking_effort": (
            reasoning.get("effort") if isinstance(reasoning, dict) else None
        ),
        "thinking_enabled": (
            reasoning.get("enabled") if isinstance(reasoning, dict) else None
        ),
        "reasoning_config": reasoning,
        "config": {
            "hermes_home": str(hermes_constants.get_hermes_home()),
            "config_path": str(hconfig.get_config_path()),
            "env_path": str(hconfig.get_env_path()),
            "config_version": cfg.get("_config_version"),
            "model_section": {
                k: v for k, v in model_cfg.items() if k not in ("api_key",)
            },
            "agent_reasoning_effort_raw": raw_reasoning_effort,
            "agent_reasoning_overrides": reasoning_overrides,
        },
        "runtime_resolution": {
            "ok": bool(runtime),
            "error": runtime_error or None,
            "source": str(runtime.get("source") or "") if runtime else "",
        },
        "catalog": catalog,
        "provider_info": provider_info,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redact",
        action="store_true",
        help="mask the api key value in the output",
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="allow network metadata refresh (default: offline)",
    )
    parser.add_argument(
        "-o", "--output", default=None, help="write JSON to this file instead of stdout"
    )
    args = parser.parse_args()

    payload = extract(redact=args.redact, allow_network=args.network)
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
