"""AgentSwarm tool — parallel subagent execution via prompt template.

Schema and registration follow the same pattern as ``tools/delegate_tool.py``.
"""

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Schema ────────────────────────────────────────────────────────────────

AGENT_SWARM_SCHEMA = {
    "name": "agent_swarm",
    "description": (
        "Launch multiple subagents from one prompt template "
        "with {{item}} placeholder."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short description of the whole swarm task",
            },
            "subagent_type": {
                "type": "string",
                "description": (
                    "Subagent profile; defaults to the configured "
                    "subagent type"
                ),
            },
            "prompt_template": {
                "type": "string",
                "description": (
                    "Prompt template with {{item}} placeholder. "
                    "Each item replaces {{item}} to create one "
                    "subagent's goal."
                ),
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 128,
                "description": (
                    "Values for {{item}} placeholder. "
                    "Each item spawns one subagent."
                ),
            },
            "resume_agent_ids": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": (
                    "Map of existing subagent agent_id → prompt "
                    "(e.g. 'continue') to resume failed subagents."
                ),
            },
        },
        "anyOf": [
            {"required": ["items", "prompt_template"]},
            {"required": ["resume_agent_ids"]},
        ],
        "additionalProperties": False,
    },
}


# ── Spec dataclass ────────────────────────────────────────────────────────

@dataclass
class AgentSwarmSpec:
    """A single unit of work for the swarm scheduler."""

    kind: str  # "spawn" | "resume"
    index: int
    item: Optional[str]
    prompt: str
    agent_id: Optional[str] = None
    subagent_type: Optional[str] = None


# ── Spec construction ─────────────────────────────────────────────────────

def create_agent_swarm_specs(
    items: Optional[list[str]],
    prompt_template: Optional[str],
    resume_agent_ids: Optional[dict[str, str]],
    default_subagent_type: Optional[str] = None,
) -> list[AgentSwarmSpec]:
    """Build an ordered list of ``AgentSwarmSpec``\\ s.

    Resume specs come first (preserving caller order), then spawn specs
    derived from *items* interpolated into *prompt_template*.
    """
    specs: list[AgentSwarmSpec] = []
    seen_prompts: set[str] = set()

    # 1. Resume specs (ordered as provided)
    for agent_id, prompt in (resume_agent_ids or {}).items():
        prompt = prompt.strip()
        specs.append(
            AgentSwarmSpec(
                kind="resume",
                index=len(specs) + 1,
                item=None,
                prompt=prompt,
                agent_id=agent_id.strip(),
                subagent_type=default_subagent_type,
            )
        )
        seen_prompts.add(prompt)

    # 2. Spawn specs from items
    if items and prompt_template:
        for i, item in enumerate(items):
            item = item.strip()
            prompt = prompt_template.replace("{{item}}", item)
            if prompt in seen_prompts:
                raise ValueError(
                    f"Duplicate subagent prompt at index {i + 1}"
                )
            seen_prompts.add(prompt)
            specs.append(
                AgentSwarmSpec(
                    kind="spawn",
                    index=len(specs) + 1,
                    item=item,
                    prompt=prompt,
                    agent_id=None,
                    subagent_type=default_subagent_type,
                )
            )

    return specs


# ── Validation ────────────────────────────────────────────────────────────

def validate_swarm_args(
    items: Optional[list[str]],
    prompt_template: Optional[str],
    resume_agent_ids: Optional[dict[str, str]],
) -> Optional[str]:
    """Validate swarm arguments.

    Returns an error string if invalid, or ``None`` if everything is fine.
    """
    # At least one mode must be used
    has_spawn = bool(items) or bool(prompt_template)
    has_resume = bool(resume_agent_ids)

    if not has_spawn and not has_resume:
        return (
            "Provide either items+prompt_template to spawn new subagents, "
            "or resume_agent_ids to continue existing ones."
        )

    # spawn mode: both items and prompt_template required
    if has_spawn:
        if not items or not prompt_template:
            return (
                "Both 'items' and 'prompt_template' are required "
                "when spawning subagents."
            )
        if "{{item}}" not in prompt_template:
            return (
                "prompt_template must contain the {{item}} placeholder."
            )
        if len(items) > 128:
            return (
                f"Maximum of 128 items allowed, got {len(items)}."
            )

    # resume mode: at least one agent_id
    if has_resume:
        if not resume_agent_ids:
            return (
                "resume_agent_ids must contain at least one entry."
            )

    return None


# ── XML result rendering ─────────────────────────────────────────────────

def escape_xml(text: str) -> str:
    """Escape special XML characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_swarm_results(results: list[dict]) -> str:
    """Render swarm execution results as structured XML.

    The XML format includes per-subagent metadata so the model can
    inspect individual outcomes and optionally resume failed ones.
    """
    completed = sum(1 for r in results if r.get("status") == "completed")
    failed = sum(
        1
        for r in results
        if r.get("status") in ("failed", "error")
    )
    aborted = sum(1 for r in results if r.get("status") == "aborted")
    total = len(results)

    has_failures = failed + aborted > 0
    has_agent_ids = any(r.get("agent_id") for r in results)
    show_resume_hint = has_failures and has_agent_ids

    lines: list[str] = ["<agent_swarm_result>"]
    lines.append(
        f"<summary>total: {total}, completed: {completed}, "
        f"failed: {failed}, aborted: {aborted}</summary>"
    )

    if show_resume_hint:
        lines.append(
            "<resume_hint>Call agent_swarm with resume_agent_ids "
            "using the agent_id values below to continue unfinished "
            "work.</resume_hint>"
        )

    for r in results:
        attrs = ""
        if r.get("agent_id"):
            attrs += f' agent_id="{escape_xml(r["agent_id"])}"'
        if r.get("item"):
            attrs += f' item="{escape_xml(r["item"])}"'
        if r.get("kind") == "resume":
            attrs += ' mode="resume"'
        if r.get("state") and r["state"] != "started":
            attrs += f' state="{r["state"]}"'

        status = r.get("status", "unknown")
        body = r.get("summary") or r.get("error") or "unknown"
        lines.append(
            f'<subagent{attrs} outcome="{status}">'
            f"{escape_xml(body)}</subagent>"
        )

    lines.append("</agent_swarm_result>")
    return "\n".join(lines)


# ── Handler ───────────────────────────────────────────────────────────────

def agent_swarm_handler(
    description: Optional[str] = None,
    prompt_template: Optional[str] = None,
    items: Optional[list[str]] = None,
    resume_agent_ids: Optional[dict[str, str]] = None,
    subagent_type: Optional[str] = None,
    parent_agent: Any = None,
) -> str:
    """Handle an ``agent_swarm`` tool call.

    Validates arguments, builds specs, and dispatches to
    ``SwarmBatchScheduler`` for rate-limit-aware parallel execution.
    """
    # 1. Validate
    error = validate_swarm_args(items, prompt_template, resume_agent_ids)
    if error:
        return json.dumps({"status": "error", "error": error})

    # 2. Build specs
    try:
        specs = create_agent_swarm_specs(
            items=items,
            prompt_template=prompt_template,
            resume_agent_ids=resume_agent_ids,
            default_subagent_type=subagent_type,
        )
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)})

    if not specs:
        return json.dumps(
            {"status": "error", "error": "No subagent specs to execute."}
        )

    # 3. Execute via SwarmBatchScheduler
    from tools.swarm_scheduler import SwarmBatchScheduler
    from tools.subagent_runner import build_and_run_subagent, resume_subagent

    def _spawn_runner(goal, item=None, subagent_type=None):
        return build_and_run_subagent(
            goal=goal,
            parent_agent=parent_agent,
            swarm_item=item,
        )

    def _resume_runner(agent_id, prompt):
        return resume_subagent(
            session_id=agent_id,
            prompt=prompt,
            parent_agent=parent_agent,
        )

    max_concurrency = _get_swarm_max_concurrency()
    scheduler = SwarmBatchScheduler(
        specs=specs,
        spawn_runner=_spawn_runner,
        resume_runner=_resume_runner,
        max_concurrency=max_concurrency,
    )
    results = scheduler.run()

    # 4. Render XML
    xml_output = render_swarm_results(results)

    # 5. Also return structured JSON for programmatic consumers
    summary = {
        "status": "completed",
        "total": len(results),
        "completed": sum(1 for r in results if r.get("status") == "completed"),
        "failed": sum(
            1
            for r in results
            if r.get("status") in ("failed", "error")
        ),
        "aborted": sum(1 for r in results if r.get("status") == "aborted"),
        "results": results,
        "xml": xml_output,
    }
    return json.dumps(summary)


def _get_swarm_max_concurrency() -> int:
    """Read swarm max concurrency from config, with sensible defaults."""
    try:
        from hermes_cli.config import cfg_get
        return int(cfg_get("swarm.max_concurrency", 3))
    except (ImportError, ValueError, TypeError):
        return 3
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


# ── Requirements check ───────────────────────────────────────────────────

def check_swarm_requirements() -> bool:
    """Swarm has no external requirements — always available."""
    return True


# ── Dynamic schema overrides ─────────────────────────────────────────────

def _build_swarm_schema_overrides() -> dict:
    """Return per-call schema overrides reflecting current swarm config.

    Updates the tool description with actual runtime limits
    (max_subagents, max_concurrency) so the model sees real ceilings.
    """
    # Deep-copy so we don't mutate the static AGENT_SWARM_SCHEMA dict
    overrides_params: dict[str, Any] = {
        k: _deep_copy(v) if isinstance(v, dict) else v
        for k, v in AGENT_SWARM_SCHEMA["parameters"].items()
    }
    overrides_params["properties"] = {
        k: _deep_copy(v) if isinstance(v, dict) else v
        for k, v in AGENT_SWARM_SCHEMA["parameters"]["properties"].items()
    }

    # Read config values (with sensible defaults)
    max_subagents = 128
    max_concurrency = 3

    try:
        from hermes_cli.config import cfg_get

        max_subagents = int(cfg_get("swarm.max_subagents", 128))
        max_concurrency = int(cfg_get("swarm.max_concurrency", 3))
    except (ImportError, ValueError, TypeError):
        pass

    overrides_params["properties"]["items"]["maxItems"] = max_subagents

    return {
        "description": (
            f"Launch multiple subagents from one prompt template. "
            f"Supports up to {max_subagents} subagents per call "
            f"(concurrent: {max_concurrency}). "
            f"Use {{item}} placeholder in prompt_template."
        ),
        "parameters": overrides_params,
    }


def _deep_copy(d: dict) -> dict:
    """Simple deep-copy for schema dicts (JSON-safe values only)."""
    return {k: (_deep_copy(v) if isinstance(v, dict) else v) for k, v in d.items()}


# ── Registry registration ────────────────────────────────────────────────

from tools.registry import registry  # noqa: E402

registry.register(
    name="agent_swarm",
    toolset="swarm",
    schema=AGENT_SWARM_SCHEMA,
    handler=lambda args, **kw: agent_swarm_handler(
        description=args.get("description"),
        prompt_template=args.get("prompt_template"),
        items=args.get("items"),
        resume_agent_ids=args.get("resume_agent_ids"),
        subagent_type=args.get("subagent_type"),
        parent_agent=kw.get("parent_agent"),
    ),
    check_fn=check_swarm_requirements,
    emoji="🐝",
    dynamic_schema_overrides=_build_swarm_schema_overrides,
)
