"""Temporary f04 debug probe — delete after use."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from tests.agent.test_compression_concurrent_fork import _build_agent_with_db  # noqa: E402
from hermes_state import SessionDB  # noqa: E402


def _mk(tmp_path: Path, tag: str):
    db = SessionDB(db_path=tmp_path / f"{tag}.db")
    session_id = f"DEBUG_{tag}"
    db.create_session(session_id, source="tui")
    agent = _build_agent_with_db(db, session_id)
    agent.compression_in_place = True
    agent._cached_system_prompt = "sys"
    messages = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    agent.context_compressor.compress.side_effect = lambda *_a, **_kw: [
        {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
        {"role": "user", "content": "tail"},
    ]
    return db, session_id, agent, messages


def test_probe_timeline(tmp_path: Path) -> None:
    import agent.conversation_compression as cc
    import agent.conversation_compression_reply_anchor as ccr
    import agent.prompt_builder as pb

    db, session_id, agent, messages = _mk(tmp_path, "timeline")
    t0 = {"start": 0.0}
    marks: list[tuple[str, float]] = []

    def mark(name):
        marks.append((name, time.monotonic() - t0["start"]))

    def wrap(mod, attr, label):
        orig = getattr(mod, attr, None)
        if not callable(orig):
            mark(f"{label}:MISSING")
            return

        def _w(*a, **kw):
            mark(f"{label}:in")
            out = orig(*a, **kw)
            mark(f"{label}:out")
            return out
        setattr(mod, attr, _w)

    wrap(cc, "_run_summary_phase", "summary_phase")
    wrap(cc, "_candidate_rejected", "candidate_rejected")
    wrap(cc, "_warn_summary_or_aux_fallback", "warn_fallback")
    wrap(ccr, "_ensure_compressed_keeps_last_assistant_reply", "reply_anchor")
    wrap(cc, "_fold_todo_snapshot", "fold_todo")
    wrap(cc, "_ensure_compressed_has_user_turn", "ensure_user_turn")
    wrap(cc, "_rebuild_system_prompt_at_boundary", "rebuild_sp")
    wrap(pb, "build_system_prompt", "build_sp")
    wrap(cc, "_commit_compaction", "commit")
    wrap(cc, "_finish_compaction_boundary", "finish_boundary")
    orig_archive = db.archive_and_compact

    def _archive(*a, **kw):
        mark("archive:in")
        return orig_archive(*a, **kw)

    db.archive_and_compact = _archive
    orig_side = agent.context_compressor.compress.side_effect

    def _compress(*a, **kw):
        mark("stub_compress:in")
        return orig_side(*a, **kw)

    agent.context_compressor.compress.side_effect = _compress
    err = {}

    def _run():
        mark("thread:start")
        try:
            agent._compress_context(messages, "sys", approx_tokens=120_000)
        except BaseException:
            import traceback
            err["v"] = traceback.format_exc()
        mark("thread:done")

    t = threading.Thread(target=_run, daemon=True)
    t0["start"] = time.monotonic()
    t.start()
    t.join(timeout=60)
    for name, ts in marks:
        print(f"MARK {ts:8.3f}  {name}")
    print("ERR:", err.get("v"))
    assert False, "probe"
