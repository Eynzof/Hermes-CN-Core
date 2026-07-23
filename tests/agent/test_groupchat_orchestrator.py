"""Behavioral tests for the bounded group-chat relay orchestrator."""

from __future__ import annotations

from agent.groupchat_loop import GroupChatRelayConfig, GroupMember, GroupRoom
from agent.groupchat_orchestrator import (
    GroupChatChainControl,
    GroupTurnResult,
    run_group_chat_chain,
)


def _room(
    *,
    config: GroupChatRelayConfig | None = None,
) -> GroupRoom:
    return GroupRoom(
        room_id="room-1",
        name="评审室",
        members=[
            GroupMember(profile="planner", name="planner"),
            GroupMember(profile="critic", name="critic"),
            GroupMember(profile="synthesizer", name="synthesizer"),
        ],
        relay_config=config or GroupChatRelayConfig(),
    )


def test_leading_mentions_drive_a_three_agent_relay_chain():
    room = _room()
    invocations = []
    events = []

    def invoke(dispatch, _control):
        invocations.append(dispatch)
        replies = {
            "planner": "@critic 请审查方案",
            "critic": "@synthesizer 请综合结论",
            "synthesizer": "最终结论",
        }
        return GroupTurnResult(replies[dispatch.target.name])

    result = run_group_chat_chain(
        room,
        "@planner 提出方案",
        invoke,
        GroupChatChainControl("chain-1"),
        lambda name, payload: events.append((name, payload)),
    )

    assert [item.target.name for item in invocations] == [
        "planner",
        "critic",
        "synthesizer",
    ]
    assert [item.mention_depth for item in invocations] == [1, 2, 3]
    assert [item.route_kind for item in invocations] == ["user", "relay", "relay"]
    assert result.status == "complete"
    assert result.turns == 3
    assert result.replied == ["planner", "critic", "synthesizer"]
    assert events[-1][0] == "chain_complete"

    # The relay trigger is passed as the current message, not duplicated in
    # history. The next wave still sees every other completed earlier reply.
    assert [m["sender_name"] for m in invocations[1].prior_messages] == ["用户"]
    assert [m["sender_name"] for m in invocations[2].prior_messages] == [
        "用户",
        "planner",
    ]


def test_initial_members_share_one_snapshot_and_next_wave_sees_both():
    room = _room()
    invocations = []

    def invoke(dispatch, _control):
        invocations.append(dispatch)
        if dispatch.target.name == "planner":
            return GroupTurnResult("@synthesizer 综合")
        if dispatch.target.name == "critic":
            return GroupTurnResult("批评意见")
        return GroupTurnResult("综合完成")

    run_group_chat_chain(
        room,
        "@planner @critic 同时评审",
        invoke,
        GroupChatChainControl("chain-2"),
        lambda _name, _payload: None,
    )

    planner, critic, synthesizer = invocations
    assert planner.prior_messages == critic.prior_messages == []
    assert [m["sender_name"] for m in synthesizer.prior_messages] == [
        "用户",
        "critic",
    ]


def test_incidental_agent_mention_does_not_schedule_another_wave():
    room = _room()
    called = []

    def invoke(dispatch, _control):
        called.append(dispatch.target.name)
        return GroupTurnResult("我同意 @critic 的观点")

    result = run_group_chat_chain(
        room,
        "@planner 回答",
        invoke,
        GroupChatChainControl("chain-3"),
        lambda _name, _payload: None,
    )

    assert called == ["planner"]
    assert result.stop_reason == "complete"


def test_depth_guard_stops_a_ping_pong_chain_exactly_at_the_limit():
    room = _room(
        config=GroupChatRelayConfig(max_depth=2, max_turns=8),
    )
    called = []
    events = []

    def invoke(dispatch, _control):
        called.append(dispatch.target.name)
        reply = "@critic 继续" if dispatch.target.name == "planner" else "@planner 继续"
        return GroupTurnResult(reply)

    result = run_group_chat_chain(
        room,
        "@planner 开始",
        invoke,
        GroupChatChainControl("chain-4"),
        lambda name, payload: events.append((name, payload)),
    )

    assert called == ["planner", "critic"]
    assert result.status == "stopped"
    assert result.stop_reason == "max_depth"
    assert events[-1][0] == "chain_stopped"


def test_turn_guard_bounds_branching_relay():
    room = _room(
        config=GroupChatRelayConfig(max_depth=4, max_turns=2),
    )
    called = []

    def invoke(dispatch, _control):
        called.append(dispatch.target.name)
        return GroupTurnResult("@critic @synthesizer 都继续")

    result = run_group_chat_chain(
        room,
        "@planner 开始",
        invoke,
        GroupChatChainControl("chain-5"),
        lambda _name, _payload: None,
    )

    assert called == ["planner", "critic"]
    assert result.status == "stopped"
    assert result.stop_reason == "max_turns"


def test_error_reply_is_recorded_but_never_relayed():
    room = _room()
    called = []

    def invoke(dispatch, _control):
        called.append(dispatch.target.name)
        return GroupTurnResult("@critic 不应执行", status="error")

    result = run_group_chat_chain(
        room,
        "@planner 开始",
        invoke,
        GroupChatChainControl("chain-6"),
        lambda _name, _payload: None,
    )

    assert called == ["planner"]
    assert result.replied == []
    assert room.transcript[-1]["status"] == "error"


def test_interrupt_clears_the_remaining_wave():
    room = _room()
    control = GroupChatChainControl("chain-7")
    called = []

    def invoke(dispatch, _control):
        called.append(dispatch.target.name)
        control.request_stop("interrupted")
        return GroupTurnResult("partial", status="interrupted")

    result = run_group_chat_chain(
        room,
        "@planner @critic 同时评审",
        invoke,
        control,
        lambda _name, _payload: None,
    )

    assert called == ["planner"]
    assert result.status == "stopped"
    assert result.stop_reason == "interrupted"
