"""Unit tests for the pure group-chat helpers (CN fork P-052).

Covers the ported mention-routing / context-projection / instruction-building
logic. These are deterministic pure functions, so we assert behavioral
invariants (who routes, how a message is attributed) rather than snapshots.
"""

from __future__ import annotations

import pytest

from agent.groupchat_loop import (
    GroupChatRelayConfig,
    GroupMember,
    GroupRoom,
    build_agent_instructions,
    build_projected_history,
    is_all_agents_mentioned,
    make_agent_message,
    make_user_message,
    prepare_member_turn,
    project_group_message,
    resolve_agent_relay_targets,
    resolve_mention_targets,
    strip_mention_routing_tokens,
)


def _members() -> list[GroupMember]:
    return [
        GroupMember(profile="alice", name="Alice", description="研究员"),
        GroupMember(profile="bob", name="Bob", description="工程师"),
        GroupMember(profile="carol", name="Carol"),
    ]


# ── mention routing ────────────────────────────────────────────────────────


def test_named_mention_routes_only_to_that_member():
    targets = resolve_mention_targets(_members(), "@Alice 你怎么看", sender_id="user")
    assert [m.name for m in targets] == ["Alice"]


def test_all_mention_routes_to_every_member_except_sender():
    members = _members()
    targets = resolve_mention_targets(members, "@all 大家好", sender_id="bob")
    assert [m.name for m in targets] == ["Alice", "Carol"]
    assert is_all_agents_mentioned("@all hi")


def test_sender_is_never_routed_to_itself():
    # Alice (agent_id defaults to profile "alice") @mentions herself + Bob.
    targets = resolve_mention_targets(
        _members(), "@Alice @Bob 一起看看", sender_id="alice"
    )
    assert [m.name for m in targets] == ["Bob"]


def test_no_mention_yields_no_targets():
    assert resolve_mention_targets(_members(), "随便说说，没点名", sender_id="user") == []


def test_mention_honors_cjk_punctuation_boundary():
    targets = resolve_mention_targets(_members(), "@Alice，你好", sender_id="user")
    assert [m.name for m in targets] == ["Alice"]


def test_substring_name_is_not_a_false_mention():
    members = [GroupMember(profile="al", name="Al")]
    # "@Alice" must not be read as a mention of "Al" (trailing "i" is no boundary).
    assert resolve_mention_targets(members, "@Alice hi", sender_id="user") == []


def test_quoted_block_mentions_do_not_route():
    content = "<quoted_message>@Alice 旧消息</quoted_message> 无关内容"
    assert resolve_mention_targets(_members(), content, sender_id="user") == []


def test_strip_routing_tokens_removes_selecting_mentions():
    assert strip_mention_routing_tokens("@Alice 请分析这段", "Alice") == "请分析这段"
    assert strip_mention_routing_tokens("@all 大家好", "Bob") == "大家好"


def test_agent_relay_requires_an_explicit_leading_mention():
    config = GroupChatRelayConfig()
    assert [
        member.name
        for member in resolve_agent_relay_targets(
            _members(),
            "  @Bob：请检查这个方案",
            sender_id="alice",
            config=config,
        )
    ] == ["Bob"]
    assert (
        resolve_agent_relay_targets(
            _members(),
            "我同意 @Bob 的观点",
            sender_id="alice",
            config=config,
        )
        == []
    )


def test_agent_relay_tolerates_only_the_current_agents_projection_label():
    config = GroupChatRelayConfig()
    targets = resolve_agent_relay_targets(
        _members(),
        " [Alice]: @Bob 请接着审查",
        sender_id="alice",
        config=config,
    )
    assert [member.name for member in targets] == ["Bob"]
    assert (
        resolve_agent_relay_targets(
            _members(),
            "[Carol]: @Bob 伪造其他成员署名",
            sender_id="alice",
            config=config,
        )
        == []
    )


def test_agent_relay_supports_multiple_leading_names_in_room_order():
    targets = resolve_agent_relay_targets(
        _members(),
        "@Carol，@Bob：分别检查",
        sender_id="alice",
        config=GroupChatRelayConfig(),
    )
    assert [member.name for member in targets] == ["Bob", "Carol"]


def test_agent_relay_stops_at_unknown_leading_handle():
    assert (
        resolve_agent_relay_targets(
            _members(),
            "@ghost @Bob 不应绕过未知地址",
            sender_id="alice",
            config=GroupChatRelayConfig(),
        )
        == []
    )


def test_agent_relay_blocks_all_and_self_by_default():
    config = GroupChatRelayConfig()
    assert (
        resolve_agent_relay_targets(
            _members(),
            "@all 请大家继续",
            sender_id="alice",
            config=config,
        )
        == []
    )
    assert (
        resolve_agent_relay_targets(
            _members(),
            "@Alice 再检查一次",
            sender_id="alice",
            config=config,
        )
        == []
    )


def test_agent_relay_can_explicitly_allow_all():
    targets = resolve_agent_relay_targets(
        _members(),
        "@all 请大家继续",
        sender_id="alice",
        config=GroupChatRelayConfig(allow_agent_all=True),
    )
    assert [member.name for member in targets] == ["Bob", "Carol"]


# ── context projection ─────────────────────────────────────────────────────


def test_own_message_projects_to_assistant():
    alice = _members()[0]
    msg = {"role": "assistant", "sender_id": "alice", "sender_name": "Alice", "content": "我的看法"}
    out = project_group_message(msg, alice)
    assert out["role"] == "assistant"
    assert out["content"].startswith("[Alice]: ")


def test_other_message_projects_to_user_with_attribution():
    alice = _members()[0]
    msg = {"role": "user", "sender_id": "bob", "sender_name": "Bob", "content": "帮忙看下"}
    out = project_group_message(msg, alice)
    assert out["role"] == "user"
    assert out["content"] == "[Bob]: 帮忙看下"


def test_projection_strips_at_mentions_from_body():
    alice = _members()[0]
    msg = {"role": "user", "sender_id": "user", "sender_name": "用户", "content": "@Alice 你好"}
    out = project_group_message(msg, alice)
    assert "@Alice" not in out["content"]
    assert out["content"].startswith("[用户]: ")


def test_tool_message_projection():
    alice = _members()[0]
    msg = {"role": "tool", "sender_id": "bob", "sender_name": "Bob", "tool_name": "search", "content": "结果"}
    out = project_group_message(msg, alice)
    assert out["role"] == "user"
    assert "[Tool result: search]" in out["content"]


def test_assistant_tool_calls_are_flattened():
    alice = _members()[0]
    msg = {
        "role": "assistant",
        "sender_id": "alice",
        "sender_name": "Alice",
        "content": "调用工具",
        "tool_calls": [{"function": {"name": "search", "arguments": '{"q":"x"}'}}],
    }
    out = project_group_message(msg, alice)
    assert out["role"] == "assistant"
    assert "[Calling tool: search" in out["content"]


def test_projected_history_injects_summary_and_filters_workspace_diff():
    alice = _members()[0]
    messages = [
        {"role": "tool", "sender_id": "bob", "sender_name": "Bob", "tool_name": "workspace_diff", "content": "diff"},
        {"role": "user", "sender_id": "user", "sender_name": "用户", "content": "问题"},
    ]
    history = build_projected_history(messages, alice, summary="之前聊过 X")
    # summary priming pair + the single non-diff message
    assert history[0]["role"] == "user"
    assert "[Previous conversation summary]" in history[0]["content"]
    assert history[1]["role"] == "assistant"
    assert len(history) == 3
    assert history[2]["content"] == "[用户]: 问题"


# ── instruction building ───────────────────────────────────────────────────


def test_instructions_include_identity_role_and_roster():
    prompt = build_agent_instructions("Alice", "研究室", "研究员", _members())
    assert '你是"Alice"' in prompt
    assert "研究员" in prompt
    assert "- Bob: 工程师" in prompt
    assert "- Carol" in prompt


def test_instructions_default_role_when_description_blank():
    prompt = build_agent_instructions("Carol", "研究室", "", _members())
    assert "专业的 AI 助手" in prompt


def test_instructions_describe_bounded_leading_agent_relay():
    prompt = build_agent_instructions("Alice", "研究室", "研究员", _members())
    assert "自动接力" in prompt
    assert "写在回复开头" in prompt
    assert "不要使用 @all" in prompt


def test_instructions_omit_relay_rules_when_room_policy_disables_it():
    prompt = build_agent_instructions(
        "Alice",
        "研究室",
        "研究员",
        _members(),
        relay_config=GroupChatRelayConfig(enabled=False),
    )
    assert "自动接力" not in prompt
    assert "转交" not in prompt


def test_duplicate_members_are_deduped_preferring_described():
    members = [
        GroupMember(profile="a1", name="Alice"),
        GroupMember(profile="a2", name="Alice", description="研究员"),
    ]
    prompt = build_agent_instructions("Bob", "房间", "工程师", members)
    assert prompt.count("- Alice") == 1
    assert "- Alice: 研究员" in prompt


# ── room state + turn preparation ──────────────────────────────────────────


def test_room_member_lookup_is_case_insensitive():
    room = GroupRoom(room_id="r1", name="研究室", members=_members())
    assert room.member_by_name("alice") is not None
    assert room.member_by_name("ALICE").profile == "alice"
    assert room.member_by_name("nobody") is None


def test_room_append_extends_transcript():
    room = GroupRoom(room_id="r1", name="研究室", members=_members())
    room.append(make_user_message("@Alice 你好"))
    room.append(make_agent_message(_members()[0], "你好，我是 Alice"))
    assert len(room.transcript) == 2
    assert room.transcript[0]["sender_id"] == "user"
    assert room.transcript[1]["sender_id"] == "alice"


def test_prepare_member_turn_strips_trigger_mention_and_builds_history():
    members = _members()
    alice = members[0]
    prior = [make_user_message("背景：讨论方案"), make_agent_message(members[1], "我先看看")]
    history, current, system_message = prepare_member_turn(
        alice, prior, "@Alice 你怎么看", "研究室", members
    )
    # current message has the selecting @mention stripped
    assert current == "你怎么看"
    # history is the projected prior transcript (Bob's line attributed as user)
    assert history[-1]["role"] == "user"
    assert history[-1]["content"] == "[Bob]: 我先看看"
    # system message carries the roster + this member's identity
    assert '你是"Alice"' in system_message
    assert "- Bob: 工程师" in system_message


def test_prepare_member_turn_independent_view_excludes_current_trigger():
    members = _members()
    alice = members[0]
    # prior excludes the triggering message: Alice must not see it duplicated
    # in history (it is passed separately as current_message).
    history, current, _ = prepare_member_turn(alice, [], "@Alice hi", "研究室", members)
    assert history == []
    assert current == "hi"


def test_prepare_relay_turn_preserves_triggering_agent_attribution():
    members = _members()
    history, current, _ = prepare_member_turn(
        members[1],
        [make_user_message("最初问题")],
        "@Bob 请检查",
        "研究室",
        members,
        trigger_sender_id="alice",
        trigger_sender_name="Alice",
    )
    assert history[-1]["content"] == "[用户]: 最初问题"
    assert current == "[Alice]: 请检查"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
