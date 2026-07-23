"""Deterministic, bounded multi-agent group-chat wave orchestration.

The gateway supplies the actual profile-backed ``AIAgent`` runner. This module
owns only room transcript ordering, relay scheduling, chain metadata and stop
guards, which keeps the control flow independently testable.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from agent.groupchat_loop import (
    GroupMember,
    GroupRoom,
    USER_SENDER_ID,
    make_agent_message,
    make_user_message,
    resolve_agent_relay_targets,
    resolve_mention_targets,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class GroupDispatch:
    """One member invocation inside a chain."""

    target: GroupMember
    trigger_message: dict[str, Any]
    prior_messages: list[dict[str, Any]]
    response_message_id: str
    chain_id: str
    root_message_id: str
    parent_message_id: str
    mention_depth: int
    turn_number: int
    route_kind: str


@dataclass(frozen=True)
class GroupTurnResult:
    """Normalized result returned by the gateway's member runner."""

    content: str
    status: str = "complete"


@dataclass(frozen=True)
class GroupChainResult:
    chain_id: str
    root_message_id: str
    status: str
    stop_reason: str
    turns: int
    replied: list[str]


class GroupChatChainControl:
    """Thread-safe cancellation and active-agent interrupt hand-off."""

    def __init__(self, chain_id: str | None = None):
        self.chain_id = chain_id or _new_id("gcc")
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._stop_reason = ""
        self._active_interrupt: Callable[[], None] | None = None
        self._deadline_timer: threading.Timer | None = None

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    @property
    def stop_reason(self) -> str:
        with self._lock:
            return self._stop_reason

    def request_stop(self, reason: str) -> bool:
        """Request the first stop reason and interrupt the active member."""

        callback: Callable[[], None] | None = None
        with self._lock:
            if self._stop_event.is_set():
                return False
            self._stop_reason = str(reason or "interrupted")
            self._stop_event.set()
            callback = self._active_interrupt
        if callback is not None:
            callback()
        return True

    def set_active_interrupt(self, callback: Callable[[], None]) -> None:
        """Register the current member; interrupt immediately if already stopped."""

        call_now = False
        with self._lock:
            if self._stop_event.is_set():
                call_now = True
            else:
                self._active_interrupt = callback
        if call_now:
            callback()

    def clear_active_interrupt(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._active_interrupt is callback:
                self._active_interrupt = None

    def start_deadline(self, seconds: float) -> None:
        if seconds <= 0:
            return
        timer = threading.Timer(
            seconds,
            lambda: self.request_stop("max_chain_seconds"),
        )
        timer.daemon = True
        with self._lock:
            self._deadline_timer = timer
        timer.start()

    def cancel_deadline(self) -> None:
        with self._lock:
            timer = self._deadline_timer
            self._deadline_timer = None
        if timer is not None:
            timer.cancel()


MemberRunner = Callable[[GroupDispatch, GroupChatChainControl], GroupTurnResult]
ChainEventHandler = Callable[[str, dict[str, Any]], None]


def _event_payload(
    *,
    chain_id: str,
    root_message_id: str,
    turns: int,
    max_turns: int,
    max_depth: int,
    max_chain_seconds: float,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "chain_id": chain_id,
        "root_message_id": root_message_id,
        "turns": turns,
        "max_turns": max_turns,
        "max_depth": max_depth,
        "max_chain_seconds": max_chain_seconds,
        **extra,
    }


def run_group_chat_chain(
    room: GroupRoom,
    text: str,
    invoke_member: MemberRunner,
    control: GroupChatChainControl,
    on_event: ChainEventHandler,
    *,
    sender_name: str = "用户",
    wall_time: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    id_factory: Callable[[str], str] = _new_id,
) -> GroupChainResult:
    """Run one user-triggered chain as deterministic serial waves.

    Every dispatch in a wave receives the same snapshot. The triggering message
    itself is removed from history and passed as the current message; all other
    messages from the completed previous wave remain visible.
    """

    config = room.relay_config
    chain_id = control.chain_id
    root_message_id = id_factory("gcmsg")
    started_at = monotonic()
    user_message = make_user_message(
        text,
        sender_name=sender_name,
        timestamp=wall_time(),
        message_id=root_message_id,
        chain_id=chain_id,
    )
    room.append(user_message)

    common = {
        "chain_id": chain_id,
        "root_message_id": root_message_id,
        "max_turns": config.max_turns,
        "max_depth": config.max_depth,
        "max_chain_seconds": config.max_chain_seconds,
    }
    on_event(
        "chain_started",
        _event_payload(
            **common,
            turns=0,
            status="running",
        ),
    )

    targets = resolve_mention_targets(room.members, text, USER_SENDER_ID)
    if not targets and "@" not in text:
        targets = list(room.members)
    if not targets:
        on_event(
            "no_targets",
            _event_payload(
                **common,
                turns=0,
                status="complete",
                stop_reason="no_targets",
                text=text,
            ),
        )
        on_event(
            "chain_complete",
            _event_payload(
                **common,
                turns=0,
                status="complete",
                stop_reason="no_targets",
                replied=[],
            ),
        )
        return GroupChainResult(
            chain_id=chain_id,
            root_message_id=root_message_id,
            status="complete",
            stop_reason="no_targets",
            turns=0,
            replied=[],
        )

    wave: list[tuple[GroupMember, dict[str, Any], int, str]] = [
        (target, user_message, 1, "user") for target in targets
    ]
    turns = 0
    replied: list[str] = []
    stop_reason = ""
    control.start_deadline(config.max_chain_seconds)

    try:
        while wave:
            if control.stop_requested:
                stop_reason = control.stop_reason or "interrupted"
                break
            if monotonic() - started_at >= config.max_chain_seconds:
                control.request_stop("max_chain_seconds")
                stop_reason = control.stop_reason
                break

            wave_snapshot = list(room.transcript)
            completed_messages: list[dict[str, Any]] = []
            pending_dispatches = list(wave)

            for target, trigger_message, depth, route_kind in pending_dispatches:
                if control.stop_requested:
                    stop_reason = control.stop_reason or "interrupted"
                    break
                if turns >= config.max_turns:
                    stop_reason = "max_turns"
                    break

                turns += 1
                response_message_id = id_factory("gcmsg")
                parent_message_id = str(trigger_message.get("id") or root_message_id)
                prior_messages = [
                    message
                    for message in wave_snapshot
                    if str(message.get("id") or "") != parent_message_id
                ]
                dispatch = GroupDispatch(
                    target=target,
                    trigger_message=trigger_message,
                    prior_messages=prior_messages,
                    response_message_id=response_message_id,
                    chain_id=chain_id,
                    root_message_id=root_message_id,
                    parent_message_id=parent_message_id,
                    mention_depth=depth,
                    turn_number=turns,
                    route_kind=route_kind,
                )
                on_event(
                    "dispatch_started",
                    _event_payload(
                        **common,
                        turns=turns,
                        status="running",
                        target_agent_id=target.agent_id,
                        target_name=target.name,
                        message_id=response_message_id,
                        parent_message_id=parent_message_id,
                        mention_depth=depth,
                        route_kind=route_kind,
                    ),
                )

                try:
                    turn_result = invoke_member(dispatch, control)
                except Exception as exc:  # defensive contract boundary
                    turn_result = GroupTurnResult(
                        content=f"Error: {exc}",
                        status="error",
                    )
                    on_event(
                        "dispatch_error",
                        _event_payload(
                            **common,
                            turns=turns,
                            status="error",
                            target_agent_id=target.agent_id,
                            target_name=target.name,
                            message_id=response_message_id,
                            parent_message_id=parent_message_id,
                            mention_depth=depth,
                            route_kind=route_kind,
                            error=str(exc),
                        ),
                    )

                status = str(turn_result.status or "complete")
                if control.stop_requested and status == "complete":
                    status = "interrupted"
                message = make_agent_message(
                    target,
                    str(turn_result.content or ""),
                    timestamp=wall_time(),
                    message_id=response_message_id,
                    chain_id=chain_id,
                    root_message_id=root_message_id,
                    parent_message_id=parent_message_id,
                    mention_depth=depth,
                    status=status,
                    route_kind=route_kind,
                )
                room.append(message)
                on_event(
                    "dispatch_complete",
                    _event_payload(
                        **common,
                        turns=turns,
                        status=status,
                        target_agent_id=target.agent_id,
                        target_name=target.name,
                        message_id=response_message_id,
                        parent_message_id=parent_message_id,
                        mention_depth=depth,
                        route_kind=route_kind,
                    ),
                )
                if status == "complete":
                    replied.append(target.name)
                    completed_messages.append(message)

            if stop_reason:
                break
            if control.stop_requested:
                stop_reason = control.stop_reason or "interrupted"
                break

            next_wave: list[tuple[GroupMember, dict[str, Any], int, str]] = []
            depth_limited = False
            for message in completed_messages:
                relay_targets = resolve_agent_relay_targets(
                    room.members,
                    str(message.get("content") or ""),
                    str(message.get("sender_id") or ""),
                    config,
                )
                if not relay_targets:
                    continue
                current_depth = int(message.get("mention_depth") or 0)
                if current_depth >= config.max_depth:
                    depth_limited = True
                    continue
                next_wave.extend(
                    (target, message, current_depth + 1, "relay")
                    for target in relay_targets
                )

            if not next_wave:
                stop_reason = "max_depth" if depth_limited else "complete"
                break
            if turns >= config.max_turns:
                stop_reason = "max_turns"
                break
            wave = next_wave
    finally:
        control.cancel_deadline()

    if not stop_reason:
        stop_reason = control.stop_reason or "complete"
    stopped = stop_reason not in {"complete", "no_targets"}
    event_name = "chain_stopped" if stopped else "chain_complete"
    status = "stopped" if stopped else "complete"
    on_event(
        event_name,
        _event_payload(
            **common,
            turns=turns,
            status=status,
            stop_reason=stop_reason,
            replied=list(replied),
        ),
    )
    return GroupChainResult(
        chain_id=chain_id,
        root_message_id=root_message_id,
        status=status,
        stop_reason=stop_reason,
        turns=turns,
        replied=replied,
    )
