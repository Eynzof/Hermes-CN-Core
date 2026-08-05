# Test-fix report — batch fixA (agent + run_agent + performance)

Per-file results (all 16 files green; each run isolated with
`HERMES_HOME=$(mktemp -d) .venv/Scripts/python.exe -m pytest <file> -q -p no:cacheprovider`).

## Files FIXED (16/16)

- `tests/agent/test_local_probe_disk_cache.py` -> FIXED (`agent/model_metadata.py`)
  `detect_local_server_type` reordered: upstream disk-L2 cache check now runs BEFORE the
  fork's `_endpoint_reachable` gate, so a fresh disk hit skips ALL HTTP (including the
  reachability HEAD probe). Previously the gate fired first, breaking the
  `mock_client.assert_not_called()` contract (2 tests). 7 passed.

- `tests/agent/test_probe_cache_followups.py` -> FIXED (test file)
  The 2 `fetch_endpoint_model_metadata` IPv4 tests patched only `requests.get`, but the
  fork's `_endpoint_reachable` gate (kept from the fork side of the merge; upstream
  removed it) issues a real HEAD probe that fails against a dead localhost port and
  short-circuits before `requests.get`. Stubbed `_endpoint_reachable=True` (same pattern
  the tests already use for `detect_local_server_type`). 10 passed.

- `tests/agent/test_model_metadata.py` -> FIXED (`agent/model_metadata.py` + test file)
  (a) Removed a DUPLICATE older `_endpoint_scoped_context_length` (Kimi-only) that
  shadowed the full version incl. the NVIDIA 262K NIM scoping (test
  `test_nvidia_deepseek_v4_pro_context_is_endpoint_scoped`). (b)
  `IncrementalTokenEstimator.estimate` now caches per-message TOKEN contributions
  (`_estimate_message_tokens_without_images` + image cost) instead of sum-chars-single-
  division — the stateless path (upstream's per-message-rounded `estimate_tokens_rough`
  memo) no longer agrees with the old estimator, breaking the byte-identical
  equivalence invariant. (c) 4 `TestFetchEndpointModelMetadata` tests stub
  `_endpoint_reachable=True` (same CN-gate reason as above). 74 passed.

- `tests/agent/test_models_dev.py` -> FIXED (`agent/models_dev.py` + test file)
  The merge dropped upstream's background-refresh/backoff/singleflight machinery
  (`_fetch_models_dev_from_network`, `_mark_stale_cache_grace`, `_commit_registry`,
  `_note_refresh_failure`, `_background_refresh_models_dev`,
  `_start_background_refresh_models_dev`, `_models_dev_retry_after`,
  `_models_dev_refresh_in_flight`, `_models_dev_fetch_lock`/`_refresh_lock`,
  `_MODELS_DEV_RETRY_DELAY`). Restored them and rewrote `fetch_models_dev` as the
  upstream cache hierarchy (stale mem/disk served + background refresh; singleflight
  foreground fetch under a lock; 5-min failure backoff) while KEEPING the CN P-028
  snapshot rescue on `allow_network=False` and on forced-refresh failure; non-forced
  no-cache failure returns `{}` per upstream. Updated the upstream
  `test_network_disabled_never_fetches[missing]` case to stub `_load_bundled_snapshot={}`
  (P-028 snapshot coverage lives in `TestModelsDevOfflineFirst`). 23 passed.

- `tests/agent/test_tool_executor_context_engine.py` -> FIXED (`agent/tool_executor.py`
  + test file)
  (a) Merge artifact: `_append_cancelled_tool_results` was inserted INSIDE
  `execute_tool_calls_sequential`, leaving the main function with an empty body
  (docstring only) — the whole executor silently did nothing. Reordered so the helper
  stands alone and the executor keeps its body. (b) CN fixture needed
  `_incremental_persistence_failed=False` (upstream guard; `getattr` on a MagicMock is
  truthy → early return). (c) context-engine non-compact branch still tuple-unpacked
  `_run_agent_tool_execution_middleware`, which now returns `_ManagedToolResult` →
  switched to `_managed_values()`. 7 passed.

- `tests/performance/test_conversation_loop.py` -> FIXED (via `agent/model_metadata.py`)
  No file change needed here; the `IncrementalTokenEstimator` fix restored
  `estimate_messages_tokens_rough` equivalence. 9 passed.

- `tests/run_agent/test_agent_guardrails.py` -> FIXED (`agent/agent_runtime_helpers.py`)
  Removed the upstream `repair_empty_non_final_messages` heal call from
  `sanitize_api_messages` — the merged heal-first ordering made the P-024 empty-content
  DROP pass dead (placeholders leaked into the wire copy). The fork contract (P-024,
  FORK_NOTES) is DROP; the heal function stays defined for its direct unit tests.
  33 passed.

- `tests/run_agent/test_malformed_tool_arguments.py` -> 10 passed (no change needed).

- `tests/run_agent/test_run_agent.py` -> FIXED (`agent/tool_executor.py` + test file)
  (a) Duplicate `tool_complete_callback` in `execute_tool_calls_sequential` — the CN
  pre-persist block fired alongside the upstream post-persist block (double callback);
  removed the CN leftover, matching upstream's single post-persist call. (b) the
  `agent_swarm` inline branch still tuple-unpacked the middleware → `_managed_values()`.
  (c) `test_post_hook_ownership_contract_lists_exercised_tools`: the fork's
  `AGENT_RUNTIME_POST_HOOK_TOOL_NAMES` deliberately includes `agent_swarm` (its inline
  path owns its post hook; the executor fires it via `agent_runtime_owns_post_tool_hook`)
  — the merged upstream test's `_CASES` was missing the case; added it with a
  `_dispatch_agent_swarm` monkeypatch. 239 passed (1 benign thread warning).

- `tests/run_agent/test_sanitize_single_pass.py` -> FIXED (via
  `agent/agent_runtime_helpers.py`) — same heal removal as guardrails; the differential
  fuzz reference `_multi_pass_reference` has no heal step, so the fused pass must not
  heal. 9 passed.

- `tests/run_agent/test_session_meta_filtering.py` -> 12 passed (no change needed).

- `tests/run_agent/test_steer.py` -> FIXED (`run_agent.py` + `agent/agent_runtime_helpers.py`
  + test file)
  (a) `steer()` now mirrors the queued text into the deprecated `_pending_steer`
  attribute (lock-guarded, newline-concat) and `_drain_pending_steer()` clears the
  mirror — the merge dropped the `_pending_steer` wiring while `_bare_agent`/tests still
  read it (P-023/P-041). (b) `clear_interrupt()` deduped a doubled comment block.
  (c) Restored upstream's real `apply_pending_steer_to_tool_results` (marker injection;
  the merge's no-op dropped the executor's post-budget steer delivery) and made
  `AIAgent._apply_pending_steer_to_tool_results` a forwarder. The stale CN
  "is_no_op" test now asserts the forwarder; the 2 upstream marker tests (previously
  retargeted at the conversation-loop drain) are back to the method contract. 40 passed.

- `tests/run_agent/test_streaming_stale_timeout.py` -> FIXED
  (`agent/chat_completion_helpers.py`)
  The P-022 bounded-escalation init block (`_stale_kill_grace`, `_max_stale_kills`,
  `_stale_kill_count`, `_last_stale_kill_at`, `_chunk_time_at_last_kill`) was dropped by
  the upstream sync → `UnboundLocalError` in `interruptible_streaming_api_call`.
  Restored the block from the fork's pre-merge implementation. 3 passed.

- `tests/run_agent/test_tool_batch_segmentation.py` -> FIXED (`agent/tool_executor.py`
  + `agent/agent_runtime_helpers.py` + test file)
  (a) The concurrent executor kept a per-tool steer drain that upstream had removed
  ("Drain pending user steers between collected results") — it consumed the steer
  BEFORE aggregate budget enforcement, so the truncated replacement discarded the
  marker; removed the leftover (the post-budget drain in the whole-batch finalizer is
  the single owner). (b) The CN-modified `test_steer_lands_exactly_once_in_mixed_batch`
  (assert no-injection + pending) contradicted the restored injection contract — reverted
  to upstream's exactly-once expectation. 33 passed.

- `tests/run_agent/test_tool_call_guardrail_runtime.py` -> FIXED (test file)
  `test_relay_rewrite_precedes_...`: checkpoint path assertion hard-coded the POSIX
  `/approved/path`; on Windows `_resolve_path_for_task` normalizes it to the drive-
  prefixed absolute path. Expected path now `os.path.abspath(...)` on `nt` (protocol
  failure class 5 — Windows path assertion). 10 passed.

- `tests/run_agent/test_tool_call_incremental_persistence.py` -> FIXED
  (`agent/tool_executor.py`)
  The concurrent per-tool result callback flushed the session DB TWICE (upstream's
  `if not _flush_session_db_after_tool_progress(...): return` + a leftover unconditional
  trailing flush) → `flushed_tool_ids == ['c1','c1','c2','c2']`. Removed the duplicate
  trailing flush; each tool result now flushes exactly once in order. 11 passed.

## Summary

- Files fixed: **16/16** (13 required code/test changes; 2 passed as-is; 1 fixed via a
  shared production change from another file in the batch).
- Files remaining failing: **0** (each of the 16 files passes its isolated run;
  total 531 tests).
- Production files changed: `agent/model_metadata.py`, `agent/models_dev.py`,
  `agent/tool_executor.py`, `agent/agent_runtime_helpers.py`,
  `agent/chat_completion_helpers.py`, `run_agent.py` (all `git add`ed).
- Test files changed: `tests/agent/test_probe_cache_followups.py`,
  `tests/agent/test_model_metadata.py`, `tests/agent/test_models_dev.py`,
  `tests/agent/test_tool_executor_context_engine.py`, `tests/run_agent/test_run_agent.py`,
  `tests/run_agent/test_steer.py`, `tests/run_agent/test_tool_batch_segmentation.py`,
  `tests/run_agent/test_tool_call_guardrail_runtime.py` (all `git add`ed).
- Notable cross-batch note: `test_partial_stream_finish_reason.py::test_poisoned_resumed_history_repaired_on_send`
  (NOT in this batch) asserts the upstream heal placeholder survives `sanitize_api_messages`;
  it conflicts with the fork's P-024 DROP contract that this batch restored — the owning
  batch should update that upstream test to the fork contract (empty stub is dropped).
