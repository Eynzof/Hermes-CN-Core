"""Multi-agent group chat orchestration — CN fork P-052.

Ports hermes-studio's group-chat *decision logic* into the Core agent runtime
so several profiles can converse in a single shared transcript:

- mention routing  — who speaks next is whoever the latest message @mentions
  (ported from studio ``mention-routing.ts``; no moderator, no round-robin).
- context projection — the shared multi-party transcript is projected into a
  single agent's first-person view (self -> ``assistant``; everyone else ->
  ``user`` with a ``[name]:`` attribution prefix) before it is fed to a model
  that only understands user/assistant turns (ported from
  studio ``context-projection.ts``).
- instruction building — the per-agent system prompt (identity + role + room
  roster + group rules), ported from studio ``context-engine/prompt.ts``.

This module holds the *pure* pieces (no I/O, no LLM). The turn orchestrator
that builds one ``AIAgent`` per mentioned member and drives
``run_conversation`` lives alongside these helpers and is wired into the
gateway via ``tui_gateway/server.py`` (``groupchat.*`` methods).

MVP scope (see plan): user @mentions route to named members which reply
*serially* into the shared transcript. Agent-to-agent relay, parallel fan-out,
context compression, interrupt/freshness guards and cross-restart persistence
are deliberately out of scope for the first cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Members ───────────────────────────────────────────────────────────────

ALL_AGENTS_MENTION = "all"


@dataclass
class GroupMember:
    """A group-chat participant backed by a Core profile.

    ``profile`` is the HERMES_HOME-scoped profile name that supplies the
    member's model/provider/persona; ``name`` is both the display name and the
    ``@mention`` handle (must not be the reserved word ``all``). ``agent_id``
    is a stable identity used for self-attribution during projection; it
    defaults to ``profile`` when unset.
    """

    profile: str
    name: str
    description: str = ""
    avatar: str = ""
    agent_id: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id:
            self.agent_id = self.profile


# ── Mention routing (ported from studio mention-routing.ts) ────────────────

# A mention only routes when it stands as its own token: the char before ``@``
# must be whitespace / an opening bracket / start-of-string, and the char after
# the name must be whitespace / punctuation / a closing bracket / end-of-string.
_BEFORE_BOUNDARY = set("([{<")
_AFTER_BOUNDARY = set(".,!?;:，。！？；：)]}>")

# Pseudo-mentions inside a quoted reply block must not route. We blank out the
# block's non-newline chars (preserving offsets) before scanning.
_QUOTED_MESSAGE_BLOCK_RE = re.compile(
    r"<quoted_message(?:\s[^>]*)?>[\s\S]*?</quoted_message>",
    re.IGNORECASE,
)


def is_reserved_mention_name(name: str) -> bool:
    return name.strip().lower() == ALL_AGENTS_MENTION


def _mask_quoted_message_blocks(content: str) -> str:
    return _QUOTED_MESSAGE_BLOCK_RE.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), content
    )


def _is_before_boundary(char: str | None) -> bool:
    return char is None or char.isspace() or char in _BEFORE_BOUNDARY


def _is_after_boundary(char: str | None) -> bool:
    return char is None or char.isspace() or char in _AFTER_BOUNDARY


def _char_at(text: str, index: int) -> str | None:
    if 0 <= index < len(text):
        return text[index]
    return None


def find_mention_ranges(content: str, mention_name: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans where ``@mention_name`` stands as a token."""
    if not content or not mention_name:
        return []

    routable = _mask_quoted_message_blocks(content)
    content_lower = routable.lower()
    needle = f"@{mention_name.lower()}"
    ranges: list[tuple[int, int]] = []
    from_index = 0

    while from_index < len(content):
        at_index = content_lower.find(needle, from_index)
        if at_index == -1:
            break
        start = at_index
        end = at_index + len(mention_name) + 1
        if _is_before_boundary(_char_at(routable, start - 1)) and _is_after_boundary(
            _char_at(routable, end)
        ):
            ranges.append((start, end))
        from_index = at_index + 1

    return ranges


def is_agent_mentioned(content: str, agent_name: str) -> bool:
    return len(find_mention_ranges(content, agent_name)) > 0


def is_all_agents_mentioned(content: str) -> bool:
    return is_agent_mentioned(content, ALL_AGENTS_MENTION)


def _is_sender(member: GroupMember, sender_id: str) -> bool:
    return bool(sender_id) and member.agent_id == sender_id


def resolve_mention_targets(
    members: list[GroupMember], content: str, sender_id: str
) -> list[GroupMember]:
    """Members the latest message routes to, excluding the sender itself.

    ``@all`` targets every other member; otherwise only members whose name is
    mentioned as a standalone token. Empty result => no agent should reply.
    """
    candidates = [m for m in members if not _is_sender(m, sender_id)]
    if is_all_agents_mentioned(content):
        return candidates
    return [m for m in candidates if is_agent_mentioned(content, m.name)]


def strip_mention_routing_tokens(content: str, own_agent_name: str) -> str:
    """Drop the ``@all`` / ``@self`` tokens that selected this agent.

    Prevents the model from re-reading ``@all`` as an instruction to fan out
    again when the message is handed to the mentioned member.
    """
    ranges: dict[tuple[int, int], tuple[int, int]] = {}
    for rng in [
        *find_mention_ranges(content, ALL_AGENTS_MENTION),
        *find_mention_ranges(content, own_agent_name),
    ]:
        ranges[rng] = rng

    result = content
    for start, end in sorted(ranges.values(), key=lambda r: r[0], reverse=True):
        result = result[:start] + result[end:]

    result = re.sub(r"^[\s,，:：;；.!?。！？]+", "", result)
    result = re.sub(r"[\s,，:：;；]+$", "", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()


# ── Context projection (ported from studio context-projection.ts) ──────────

_TOOL_ARGS_BUDGET = 4000


def _strip_mentions_for_projection(content: str) -> str:
    content = re.sub(r"@([^\s@]+)", "", str(content or ""))
    content = re.sub(r"[ \t]{2,}", " ", content)
    content = re.sub(r"^\s+", "", content)
    return content


def _attribution_prefix(sender_name: str) -> str:
    return f"[{sender_name}]: "


def _attributed_content(sender_name: str, content: str) -> str:
    return f"{_attribution_prefix(sender_name)}{_strip_mentions_for_projection(content)}"


def is_workspace_diff_tool_message(message: dict[str, Any]) -> bool:
    return (
        str(message.get("role") or "") == "tool"
        and str(message.get("tool_name") or "") == "workspace_diff"
    )


def project_group_message(
    message: dict[str, Any], own: GroupMember
) -> dict[str, str]:
    """Project one shared-transcript message into ``own``'s first-person view.

    ``message`` is a plain dict with ``sender_id`` / ``sender_name`` /
    ``content`` / ``role`` (and optionally ``tool_calls`` / ``tool_name``).
    Returns an OpenAI-format ``{"role": "user"|"assistant", "content": str}``.
    """
    sender_name = str(message.get("sender_name") or "unknown")
    sender_id = str(message.get("sender_id") or "").strip()
    role = str(message.get("role") or "user")
    is_own = bool(
        (sender_id and own.agent_id and sender_id == own.agent_id)
        or (not sender_id and sender_name == own.name)
    )

    if role == "tool":
        tool_name = message.get("tool_name")
        label = f"Tool result: {tool_name}" if tool_name else "Tool result"
        return {
            "role": "user",
            "content": f"[{sender_name}] [{label}]\n{str(message.get('content') or '')}",
        }

    tool_calls = message.get("tool_calls")
    if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
        rendered_calls = []
        for call in tool_calls:
            fn = (call or {}).get("function") or {}
            name = fn.get("name") or "unknown"
            args = str(fn.get("arguments") or "{}")
            if len(args) > _TOOL_ARGS_BUDGET:
                args = f"{args[:_TOOL_ARGS_BUDGET]}..."
            rendered_calls.append(f"[Calling tool: {name} with arguments: {args}]")
        tools_info = "\n".join(rendered_calls)
        content = str(message.get("content") or "").strip()
        role_out = "assistant" if is_own else "user"
        if content:
            body = f"{_attributed_content(sender_name, content)}\n{_attribution_prefix(sender_name)}{tools_info}"
        else:
            body = f"{_attribution_prefix(sender_name)}{tools_info}"
        return {"role": role_out, "content": body}

    return {
        "role": "assistant" if is_own else "user",
        "content": _attributed_content(sender_name, str(message.get("content") or "")),
    }


def build_projected_history(
    messages: list[dict[str, Any]],
    own: GroupMember,
    summary: str = "",
) -> list[dict[str, str]]:
    """Project the shared transcript into ``own``'s user/assistant history.

    An optional leading ``summary`` (MVP: always empty) is injected as a
    user/assistant priming pair, matching studio's projection.
    """
    history: list[dict[str, str]] = []
    if summary:
        history.append(
            {"role": "user", "content": f"[Previous conversation summary]\n{summary}"}
        )
        history.append(
            {
                "role": "assistant",
                "content": "I have reviewed the conversation history and understand the context.",
            }
        )
    history.extend(
        project_group_message(m, own)
        for m in messages
        if not is_workspace_diff_tool_message(m)
    )
    return history


# ── Instruction building (ported from studio context-engine/prompt.ts) ─────

_DEFAULT_ROLE_DESCRIPTION = "专业的 AI 助手，随时准备协助解决问题。"


def build_agent_instructions(
    agent_name: str,
    room_name: str,
    agent_description: str,
    members: list[GroupMember],
) -> str:
    """Build the per-agent group-chat system prompt (Chinese).

    MVP note: the studio prompt's agent-to-agent relay rules are omitted here
    because the MVP does not route agent replies onward; keeping them would
    invite the model to ``@`` teammates that never get triggered.
    """
    seen: dict[str, GroupMember] = {}
    for m in members:
        existing = seen.get(m.name)
        if existing is None or (m.description and not existing.description):
            seen[m.name] = m
    unique = list(seen.values())

    if unique:
        member_section = "\n".join(
            f"- {m.name}: {m.description}" if m.description else f"- {m.name}"
            for m in unique
        )
    else:
        member_section = "- 未知"

    role_description = agent_description.strip() or _DEFAULT_ROLE_DESCRIPTION

    return f"""你是"{agent_name}"，群聊房间"{room_name}"中的 AI 助手。

你的角色：{role_description}

当前房间成员：
{member_section}

规则：
- 当你收到群聊任务时，说明系统已经判断你需要回复；请直接回应当前消息，不要因为消息里同时提及其他成员而拒绝回复或输出空回复。
- 重点回应提及你的人。
- 回答简洁、对群聊有帮助。
- 不要假装是人类，需要时明确表明自己是 AI。
- 对话历史中包含多个人的消息，每条消息前标有发送者名字。
- 历史消息里的"[发送者]: ..."只是系统添加的归属标记，用来帮助你理解谁说了这句话；不要在你的回复中复述或模仿这种方括号前缀。
- 回复时使用自然语言即可；如果需要点名某人，只使用 @名字，不要输出"[{agent_name}]:"这类格式。
- 回复最新一条提及你的消息。
- 如果只是回答提问，直接回答即可。
- 自行判断对话是否已经结束——如果问题已解决、达成共识、或对方只是陈述不需要回复，则直接结束回复，避免产生无意义的循环对话。"""


# ── Room state + turn preparation ──────────────────────────────────────────

# Sender id used for human/user messages in the shared transcript. Kept
# distinct from any member's agent_id so a user @mention never routes back to
# the user and members never self-attribute a user message.
USER_SENDER_ID = "user"


@dataclass
class GroupRoom:
    """In-memory group-chat room (MVP: not persisted across restarts).

    ``transcript`` is the single shared multi-party message stream. Each entry
    is a plain dict: ``{role, sender_id, sender_name, content}`` (+ optional
    ``tool_calls`` / ``tool_name``). Projection turns it into each member's
    first-person history on demand.
    """

    room_id: str
    name: str
    members: list[GroupMember] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def member_by_name(self, name: str) -> GroupMember | None:
        lowered = name.strip().lower()
        for member in self.members:
            if member.name.lower() == lowered:
                return member
        return None

    def append(self, message: dict[str, Any]) -> dict[str, Any]:
        self.transcript.append(message)
        return message


def make_user_message(
    content: str, sender_name: str = "用户", timestamp: float = 0.0
) -> dict[str, Any]:
    return {
        "role": "user",
        "sender_id": USER_SENDER_ID,
        "sender_name": sender_name,
        "content": content,
        "timestamp": timestamp,
    }


def make_agent_message(
    member: GroupMember, content: str, timestamp: float = 0.0
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "sender_id": member.agent_id,
        "sender_name": member.name,
        "content": content,
        "timestamp": timestamp,
    }


def prepare_member_turn(
    target: GroupMember,
    prior_messages: list[dict[str, Any]],
    trigger_content: str,
    room_name: str,
    members: list[GroupMember],
) -> tuple[list[dict[str, str]], str, str]:
    """Build the inputs for one member's turn.

    Returns ``(conversation_history, current_message, system_message)`` where:
    - ``conversation_history`` is ``prior_messages`` projected into ``target``'s
      first-person view (everything before the triggering message);
    - ``current_message`` is the triggering message with the mention tokens
      that selected ``target`` stripped, ready to pass as ``run_conversation``'s
      ``user_message``;
    - ``system_message`` is the group-chat instructions to layer on top of the
      member's SOUL.md persona.

    MVP uses an "independent view": each mentioned member replies to the same
    trigger without seeing this turn's other members' replies. Their replies
    still land in the shared transcript and are broadcast to the room.
    """
    history = build_projected_history(prior_messages, target)
    current = strip_mention_routing_tokens(str(trigger_content or ""), target.name)
    system_message = build_agent_instructions(
        target.name, room_name, target.description, members
    )
    return history, current, system_message
