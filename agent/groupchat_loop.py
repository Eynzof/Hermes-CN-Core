"""Multi-agent group chat decision logic — CN fork P-052.

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
  roster + bounded hand-off rules).
- relay routing — only an explicit leading ``@member`` in a completed agent
  reply may schedule the next wave. Incidental mentions in prose never do.

This module holds the *pure* pieces (no I/O, no LLM). The bounded wave
orchestrator lives in ``agent/groupchat_orchestrator.py`` and is wired into the
gateway via ``tui_gateway/server.py`` (``groupchat.*`` methods).

The current CN implementation remains deterministic and serial. Initial user
targets share one pre-turn snapshot; a completed wave is visible to the next
relay wave. Parallel fan-out, context compression and cross-restart persistence
remain separate follow-ups.
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


@dataclass(frozen=True)
class GroupChatRelayConfig:
    """Room-pinned automatic hand-off policy.

    Pinning this at room creation keeps every member's system prompt byte-stable
    for the room lifetime even if ``config.yaml`` changes mid-conversation.
    """

    enabled: bool = True
    require_leading_mention: bool = True
    allow_agent_all: bool = False
    max_depth: int = 4
    max_turns: int = 8
    max_chain_seconds: float = 300.0


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


_LEADING_MENTION_SEPARATORS = set(",，:：;；")


def _leading_mention_names(
    content: str,
    members: list[GroupMember],
    sender_id: str = "",
) -> list[str]:
    """Return consecutive known mention names at the start of ``content``.

    Leading whitespace is allowed. After the first mention, whitespace and
    common list/address punctuation may separate more mentions. An unknown
    leading handle ends parsing, so ``@ghost @Bob`` cannot accidentally route
    to Bob. Some models echo the projection label despite being instructed not
    to (for example ``[Alice]: @Bob ...``); the exact current sender's label is
    tolerated, while a foreign or unknown label is not.
    """

    text = str(content or "")
    sender = next(
        (member for member in members if _is_sender(member, sender_id)),
        None,
    )
    if sender is not None:
        own_prefix = re.compile(
            rf"^\s*\[\s*{re.escape(sender.name)}\s*\]\s*[:：]\s*",
            re.IGNORECASE,
        )
        text = own_prefix.sub("", text, count=1)
    names = [m.name for m in members]
    candidates = sorted(
        [*names, ALL_AGENTS_MENTION],
        key=len,
        reverse=True,
    )
    lowered = text.lower()
    cursor = 0
    found: list[str] = []

    while cursor < len(text) and text[cursor].isspace():
        cursor += 1

    while cursor < len(text) and text[cursor] == "@":
        matched: str | None = None
        end = cursor
        for name in candidates:
            candidate_end = cursor + len(name) + 1
            if lowered.startswith(f"@{name.lower()}", cursor) and _is_after_boundary(
                _char_at(text, candidate_end)
            ):
                matched = name
                end = candidate_end
                break
        if matched is None:
            break
        found.append(matched)
        cursor = end
        while cursor < len(text) and (
            text[cursor].isspace() or text[cursor] in _LEADING_MENTION_SEPARATORS
        ):
            cursor += 1

    return found


def resolve_agent_relay_targets(
    members: list[GroupMember],
    content: str,
    sender_id: str,
    config: GroupChatRelayConfig,
) -> list[GroupMember]:
    """Resolve targets from a completed agent reply under the room policy."""

    if not config.enabled:
        return []

    candidates = [m for m in members if not _is_sender(m, sender_id)]
    if config.require_leading_mention:
        mentioned_names = _leading_mention_names(content, members, sender_id)
        mentioned_lower = {name.lower() for name in mentioned_names}
        if (
            config.allow_agent_all
            and ALL_AGENTS_MENTION in mentioned_lower
        ):
            return candidates
        return [m for m in candidates if m.name.lower() in mentioned_lower]

    if config.allow_agent_all and is_all_agents_mentioned(content):
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
    relay_config: GroupChatRelayConfig | None = None,
) -> str:
    """Build the per-agent group-chat system prompt (Chinese)."""
    relay = relay_config or GroupChatRelayConfig()
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

    base = f"""你是"{agent_name}"，群聊房间"{room_name}"中的 AI 助手。

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
- 如果只是回答提问，直接回答即可。"""

    if relay.enabled:
        base += """
- 群聊支持 Agent 之间自动接力。只有把 @成员 写在回复开头，系统才会把本条消息交给对方；正文中偶然提到成员不会触发接力。
- 如果用户明确要求你叫、让、请某个成员执行任务，不要自己代办，也不要声称无法联系对方；请在回复开头直接写 @名字，并清楚说明要对方执行什么。
- 不要使用 @all 发起 Agent 接力；如需协作，请明确点名真正需要行动的成员。
- 不要为了活跃气氛、征求补充或礼貌而 @ 其他成员。只有确实需要对方执行动作、提供信息或确认决策时才接力。
- 问题已经解决、达成共识或对方只是陈述时，不要再 @任何人，直接结束回复，避免无意义循环。"""
    else:
        base += """
- 自行判断对话是否已经结束——如果问题已解决、达成共识、或对方只是陈述不需要回复，则直接结束回复，避免产生无意义的循环对话。"""

    return base


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
    relay_config: GroupChatRelayConfig = field(default_factory=GroupChatRelayConfig)

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
    content: str,
    sender_name: str = "用户",
    timestamp: float = 0.0,
    *,
    message_id: str = "",
    chain_id: str = "",
) -> dict[str, Any]:
    message = {
        "role": "user",
        "sender_id": USER_SENDER_ID,
        "sender_name": sender_name,
        "content": content,
        "timestamp": timestamp,
        "status": "complete",
        "mention_depth": 0,
    }
    if message_id:
        message["id"] = message_id
    if chain_id:
        message["chain_id"] = chain_id
        message["root_message_id"] = message_id
    return message


def make_agent_message(
    member: GroupMember,
    content: str,
    timestamp: float = 0.0,
    *,
    message_id: str = "",
    chain_id: str = "",
    root_message_id: str = "",
    parent_message_id: str = "",
    mention_depth: int = 1,
    status: str = "complete",
    route_kind: str = "user",
) -> dict[str, Any]:
    message = {
        "role": "assistant",
        "sender_id": member.agent_id,
        "sender_name": member.name,
        "avatar": member.avatar,
        "content": content,
        "timestamp": timestamp,
        "status": status,
        "mention_depth": mention_depth,
        "route_kind": route_kind,
    }
    if message_id:
        message["id"] = message_id
    if chain_id:
        message["chain_id"] = chain_id
    if root_message_id:
        message["root_message_id"] = root_message_id
    if parent_message_id:
        message["parent_message_id"] = parent_message_id
    return message


def prepare_member_turn(
    target: GroupMember,
    prior_messages: list[dict[str, Any]],
    trigger_content: str,
    room_name: str,
    members: list[GroupMember],
    *,
    trigger_sender_id: str = USER_SENDER_ID,
    trigger_sender_name: str = "用户",
    relay_config: GroupChatRelayConfig | None = None,
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
    if trigger_sender_id and trigger_sender_id != USER_SENDER_ID:
        current = f"[{trigger_sender_name or 'unknown'}]: {current}"
    system_message = build_agent_instructions(
        target.name,
        room_name,
        target.description,
        members,
        relay_config=relay_config,
    )
    return history, current, system_message
