"""Tests for the todo tool module."""

import orjson

from tools.todo_tool import TodoStore, todo_tool


class TestWriteAndRead:
    def test_write_replaces_list(self):
        store = TodoStore()
        items = [
            {"id": "1", "content": "First task", "status": "pending"},
            {"id": "2", "content": "Second task", "status": "in_progress"},
        ]
        result = store.write(items)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["status"] == "in_progress"

    def test_read_returns_copy(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "pending"}])
        items = store.read()
        items[0]["content"] = "MUTATED"
        assert store.read()[0]["content"] == "Task"

    def test_write_deduplicates_duplicate_ids(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "First version", "status": "pending"},
            {"id": "2", "content": "Other task", "status": "pending"},
            {"id": "1", "content": "Latest version", "status": "in_progress"},
        ])
        assert result == [
            {"id": "2", "content": "Other task", "status": "pending"},
            {"id": "1", "content": "Latest version", "status": "in_progress"},
        ]


class TestHasItems:
    def test_empty_store(self):
        store = TodoStore()
        assert store.has_items() is False

    def test_non_empty_store(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "x", "status": "pending"}])
        assert store.has_items() is True


class TestFormatForInjection:
    def test_empty_returns_none(self):
        store = TodoStore()
        assert store.format_for_injection() is None

    def test_non_empty_has_markers(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Do thing", "status": "completed"},
            {"id": "2", "content": "Next", "status": "pending"},
            {"id": "3", "content": "Working", "status": "in_progress"},
        ])
        text = store.format_for_injection()
        # Completed items are filtered out of injection
        assert "[x]" not in text
        assert "Do thing" not in text
        # Active items are included
        assert "[ ]" in text
        assert "[>]" in text
        assert "Next" in text
        assert "Working" in text
        assert "context compression" in text.lower()


class TestMergeMode:
    def test_update_existing_by_id(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Original", "status": "pending"},
        ])
        store.write(
            [{"id": "1", "status": "completed"}],
            merge=True,
        )
        items = store.read()
        assert len(items) == 1
        assert items[0]["status"] == "completed"
        assert items[0]["content"] == "Original"

    def test_merge_appends_new(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "First", "status": "pending"}])
        store.write(
            [{"id": "2", "content": "Second", "status": "pending"}],
            merge=True,
        )
        items = store.read()
        assert len(items) == 2


class TestTodoToolFunction:
    def test_read_mode(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "pending"}])
        result = orjson.loads(todo_tool(store=store))
        assert result["summary"]["total"] == 1
        assert result["summary"]["pending"] == 1

    def test_write_mode(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "New", "status": "in_progress"}],
            store=store,
        ))
        assert result["summary"]["in_progress"] == 1

    def test_no_store_returns_error(self):
        result = orjson.loads(todo_tool())
        assert "error" in result


class TestTodoStoreBounds:
    """Bounds on persisted todo state (GHSA-5g4g-6jrg-mw3g hardening).

    The todo list is re-injected into context after every compression event,
    so an unbounded item — whether authored by the model or replayed from
    caller-supplied history on the API server's _hydrate_todo_store path —
    would defeat the compression it rides through. These pin the caps.
    Not a security boundary (the API surface is authenticated and the caller
    supplies their own history); this is footgun containment / parity.
    """

    def test_oversized_content_is_truncated(self):
        from tools.todo_tool import MAX_TODO_CONTENT_CHARS
        store = TodoStore()
        store.write([{"id": "1", "content": "A" * 50001, "status": "pending"}])
        item = store.read()[0]
        assert len(item["content"]) <= MAX_TODO_CONTENT_CHARS
        assert item["content"].endswith("… [truncated]")

    def test_injection_block_is_bounded(self):
        from tools.todo_tool import MAX_TODO_CONTENT_CHARS
        store = TodoStore()
        store.write([{"id": "1", "content": "A" * 50001, "status": "pending"}])
        inj = store.format_for_injection()
        # Before the fix this was ~50085 chars; now it tracks the cap.
        assert len(inj) < MAX_TODO_CONTENT_CHARS + 200

    def test_merge_update_content_is_capped(self):
        """The merge path updates content directly, bypassing _validate —
        verify it is capped too."""
        from tools.todo_tool import MAX_TODO_CONTENT_CHARS
        store = TodoStore()
        store.write([{"id": "1", "content": "short", "status": "pending"}])
        store.write([{"id": "1", "content": "B" * 50001}], merge=True)
        assert len(store.read()[0]["content"]) <= MAX_TODO_CONTENT_CHARS

    def test_item_count_is_bounded(self):
        from tools.todo_tool import MAX_TODO_ITEMS
        store = TodoStore()
        store.write([
            {"id": str(i), "content": f"task {i}", "status": "pending"}
            for i in range(5000)
        ])
        assert len(store.read()) == MAX_TODO_ITEMS

    def test_normal_list_is_unchanged(self):
        """No regression: ordinary plans pass through untouched (no marker,
        same content, same order)."""
        store = TodoStore()
        store.write([
            {"id": "1", "content": "write the report", "status": "in_progress"},
            {"id": "2", "content": "review PR", "status": "pending"},
        ])
        items = store.read()
        assert [i["content"] for i in items] == ["write the report", "review PR"]
        assert "[truncated]" not in items[0]["content"]




# --- Hardening additions (TodoList-grade stability) -------------------------

from tools.todo_tool import (
    _ALL_DONE_REMINDER,
    MAX_TODO_CODE_CHARS,
    MAX_TODO_NOTES_CHARS,
    run_verification_code,
)


class TestNotesAndCodeFields:
    def test_round_trip_through_write_and_read(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": "some details", "code": "print('ok')"},
        ])
        assert result[0]["notes"] == "some details"
        assert result[0]["code"] == "print('ok')"
        assert store.read()[0]["notes"] == "some details"
        assert store.read()[0]["code"] == "print('ok')"

    def test_empty_notes_and_code_are_omitted(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": "   ", "code": ""},
        ])
        assert "notes" not in result[0]
        assert "code" not in result[0]

    def test_none_notes_and_code_are_omitted(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": None, "code": None},
        ])
        assert "notes" not in result[0]
        assert "code" not in result[0]

    def test_caps_apply_with_truncation_marker(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": "n" * 50001, "code": "c" * 50001},
        ])
        assert len(result[0]["notes"]) <= MAX_TODO_NOTES_CHARS
        assert len(result[0]["code"]) <= MAX_TODO_CODE_CHARS
        assert result[0]["notes"].endswith("\u2026 [truncated]")
        assert result[0]["code"].endswith("\u2026 [truncated]")

    def test_merge_keeps_old_notes_code_when_new_empty(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": "old notes", "code": "print(1)"},
        ])
        store.write([{"id": "1", "notes": "", "code": None}], merge=True)
        item = store.read()[0]
        assert item["notes"] == "old notes"
        assert item["code"] == "print(1)"

    def test_merge_updates_notes_code_when_provided(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": "old notes", "code": "print(1)"},
        ])
        store.write([{"id": "1", "notes": "new notes", "code": "print(2)"}], merge=True)
        item = store.read()[0]
        assert item["notes"] == "new notes"
        assert item["code"] == "print(2)"

    def test_merge_caps_updated_notes_and_code(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": "x", "code": "y"},
        ])
        store.write([{"id": "1", "notes": "n" * 50001, "code": "c" * 50001}], merge=True)
        item = store.read()[0]
        assert len(item["notes"]) <= MAX_TODO_NOTES_CHARS
        assert len(item["code"]) <= MAX_TODO_CODE_CHARS

    def test_non_string_values_coerced(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "notes": 123, "code": ["import", "os"]},
        ])
        assert result[0]["notes"] == "123"
        assert result[0]["code"] == "['import', 'os']"


class TestVerificationGate:
    def test_success_keeps_completed(self, monkeypatch):
        calls = []

        def fake_run(code, timeout=30):
            calls.append(code)
            return True, "verified ok"

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "completed",
                    "code": "print('ok')"}],
            store=store,
        ))
        assert result["todos"][0]["status"] == "completed"
        assert result["warnings"] == []
        assert calls == ["print('ok')"]

    def test_failure_reverts_to_pending(self, monkeypatch):
        def fake_run(code, timeout=30):
            return False, "assertion failed: boom"

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "completed",
                    "code": "print('x')"}],
            store=store,
        ))
        item = result["todos"][0]
        assert item["status"] == "pending"
        assert item["notes"].startswith("[verification failed] ")
        assert "assertion failed: boom" in item["notes"]
        assert any("failed verification" in w for w in result["warnings"])

    def test_failure_output_capped_in_notes_and_warning(self, monkeypatch):
        def fake_run(code, timeout=30):
            return False, "E" * 5000

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "completed",
                    "code": "print('x')"}],
            store=store,
        ))
        note = result["todos"][0]["notes"]
        assert len(note) <= len("[verification failed] ") + 500
        for w in result["warnings"]:
            if "failed verification" in w:
                assert len(w) <= len("Item '1' marked completed failed "
                                     "verification and was reverted to pending: ") + 200

    def test_no_code_no_run(self, monkeypatch):
        calls = []

        def fake_run(code, timeout=30):
            calls.append(code)
            return False, "should not run"

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "completed"}],
            store=store,
        )
        assert calls == []

    def test_non_completed_transitions_no_run(self, monkeypatch):
        calls = []

        def fake_run(code, timeout=30):
            calls.append(code)
            return False, "should not run"

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        todo_tool(
            todos=[
                {"id": "1", "content": "Task", "status": "pending",
                 "code": "print(1)"},
                {"id": "2", "content": "Task 2", "status": "in_progress",
                 "code": "print(2)"},
            ],
            store=store,
        )
        assert calls == []

    def test_already_completed_no_reverify(self, monkeypatch):
        calls = []

        def fake_run(code, timeout=30):
            calls.append(code)
            return True, "ok"

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Task", "status": "completed",
             "code": "print(1)"},
        ])
        assert calls == ["print(1)"]
        todo_tool(todos=[{"id": "1", "status": "completed"}], store=store, merge=True)
        assert calls == ["print(1)"]

    def test_runner_raising_does_not_break_write(self, monkeypatch):
        def fake_run(code, timeout=30):
            raise RuntimeError("runner exploded")

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "completed",
                    "code": "print('x')"}],
            store=store,
        ))
        # Write still succeeded; item reverted to pending with a warning.
        assert result["todos"][0]["status"] == "pending"
        assert "runner exploded" in result["todos"][0]["notes"]
        assert any("failed verification" in w for w in result["warnings"])

    def test_merge_transition_to_completed_verifies_stored_code(self, monkeypatch):
        calls = []

        def fake_run(code, timeout=30):
            calls.append(code)
            return True, "ok"

        monkeypatch.setattr("tools.todo_tool.run_verification_code", fake_run)
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Task", "status": "pending",
             "code": "print(1)"},
        ])
        assert calls == []
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "status": "completed"}],
            store=store,
            merge=True,
        ))
        assert result["todos"][0]["status"] == "completed"
        assert calls == ["print(1)"]


class TestRunVerificationCode:
    def test_inline_python_success(self):
        success, output = run_verification_code("print(1 + 1)")
        assert success is True
        assert "2" in output

    def test_inline_python_failure(self):
        success, output = run_verification_code("raise ValueError('nope')")
        assert success is False
        assert "nope" in output

    def test_empty_code_is_noop_success(self):
        assert run_verification_code("") == (True, "")
        assert run_verification_code("   ") == (True, "")

    def test_none_code_is_noop_success(self):
        assert run_verification_code(None) == (True, "")

    def test_bang_shell_command_runs(self):
        import sys
        success, output = run_verification_code(
            f"!{sys.executable} -c \"print('shell ok')\""
        )
        assert success is True
        assert "shell ok" in output

    def test_py_file_path_runs(self, tmp_path):
        py_file = tmp_path / "verify.py"
        py_file.write_text("print('from file')", encoding="utf-8")
        success, output = run_verification_code(str(py_file))
        assert success is True
        assert "from file" in output

    def test_bad_command_returns_error_not_raise(self):
        success, output = run_verification_code("!definitely_not_a_real_command_xyz")
        assert success is False
        assert output  # some error text

    def test_timeout_kills_and_reports(self):
        success, output = run_verification_code(
            "import time; time.sleep(30)", timeout=1
        )
        assert success is False
        assert "timed out after 1s" in output


class TestRegressionGuard:
    def test_completed_cannot_reopen(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "completed"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "pending"}],
            store=store,
        ))
        assert result["todos"][0]["status"] == "completed"
        assert any(
            "cannot be re-opened" in w and "clamped back to completed" in w
            for w in result["warnings"]
        )

    def test_cancelled_cannot_reopen(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "cancelled"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Task", "status": "in_progress"}],
            store=store,
        ))
        assert result["todos"][0]["status"] == "cancelled"
        assert any("clamped back to cancelled" in w for w in result["warnings"])

    def test_merge_mode_clamp(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "completed"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "status": "pending"}],
            store=store,
            merge=True,
        ))
        assert result["todos"][0]["status"] == "completed"
        assert any("cannot be re-opened" in w for w in result["warnings"])

    def test_fresh_id_untouched(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "completed"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "2", "content": "Brand new", "status": "pending"}],
            store=store,
        ))
        assert result["todos"][0]["status"] == "pending"
        assert result["warnings"] == []


class TestSingleInProgress:
    def test_auto_fix_keeps_first(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[
                {"id": "a", "content": "First", "status": "in_progress"},
                {"id": "b", "content": "Second", "status": "in_progress"},
                {"id": "c", "content": "Third", "status": "pending"},
            ],
            store=store,
        ))
        statuses = {t["id"]: t["status"] for t in result["todos"]}
        assert statuses["a"] == "in_progress"
        assert statuses["b"] == "completed"
        assert statuses["c"] == "pending"
        assert any(
            "Auto-fixed 1 extra in_progress item(s)" in w
            for w in result["warnings"]
        )

    def test_auto_fix_false_returns_error(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[
                {"id": "a", "content": "First", "status": "in_progress"},
                {"id": "b", "content": "Second", "status": "in_progress"},
            ],
            store=store,
            auto_fix=False,
        ))
        assert "error" in result

    def test_read_with_auto_fix_false_unaffected(self):
        store = TodoStore()
        store.write(
            [
                {"id": "a", "content": "First", "status": "in_progress"},
                {"id": "b", "content": "Second", "status": "in_progress"},
            ],
            auto_fix=False,
        )
        result = orjson.loads(todo_tool(store=store, auto_fix=False))
        assert "error" not in result
        assert result["summary"]["in_progress"] == 2


class TestArchiving:
    def test_replace_drops_completed_archives(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Done task", "status": "completed"},
            {"id": "2", "content": "Active task", "status": "pending"},
        ])
        result = orjson.loads(todo_tool(
            todos=[{"id": "3", "content": "Fresh plan", "status": "pending"}],
            store=store,
        ))
        assert result["summary"]["archived"] == 1
        assert len(store._archived) == 1
        assert store._archived[0]["id"] == "1"

    def test_pending_drops_not_archived(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Abandoned", "status": "pending"},
            {"id": "2", "content": "Cancelled task", "status": "cancelled"},
        ])
        store.write([{"id": "3", "content": "New", "status": "pending"}])
        assert len(store._archived) == 1
        assert store._archived[0]["id"] == "2"

    def test_cancelled_drops_archived(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Cancelled", "status": "cancelled"}])
        store.write([{"id": "2", "content": "Next", "status": "pending"}])
        assert len(store._archived) == 1
        assert store._archived[0]["id"] == "1"

    def test_cap_500_drops_oldest(self):
        store = TodoStore()
        store._archived = [
            {"id": f"old{i}", "content": f"task {i}", "status": "completed"}
            for i in range(600)
        ]
        store.write([{"id": "new", "content": "Fresh", "status": "pending"}])
        assert len(store._archived) == 500
        assert store._archived[0]["id"] == "old100"
        assert store._archived[-1]["id"] == "old599"

    def test_merge_mode_does_not_archive(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "completed"}])
        store.write([{"id": "2", "content": "New", "status": "pending"}], merge=True)
        assert store._archived == []


class TestAllDoneReminder:
    def test_all_terminal_returns_reminder(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[
                {"id": "1", "content": "A", "status": "completed"},
                {"id": "2", "content": "B", "status": "cancelled"},
            ],
            store=store,
        ))
        assert result["message"] == _ALL_DONE_REMINDER

    def test_one_pending_no_message(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[
                {"id": "1", "content": "A", "status": "completed"},
                {"id": "2", "content": "B", "status": "pending"},
            ],
            store=store,
        ))
        assert result["message"] is None

    def test_empty_list_no_message(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(todos=[], store=store))
        assert result["message"] is None
        assert result["summary"]["total"] == 0

    def test_read_mode_all_done_reminder(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "A", "status": "completed"}])
        result = orjson.loads(todo_tool(store=store))
        assert result["message"] == _ALL_DONE_REMINDER


class TestFuzzyWarnings:
    def test_near_miss_warns(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Fix login bug", "status": "pending"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "2", "content": "fix login bug please",
                    "status": "pending"}],
            store=store,
        ))
        assert any(
            "looks like existing" in w and "Fix login bug" in w
            for w in result["warnings"]
        )

    def test_distinct_content_no_warning(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Fix login bug", "status": "pending"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "2", "content": "Write documentation",
                    "status": "pending"}],
            store=store,
        ))
        assert result["warnings"] == []

    def test_exact_same_content_skipped(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Fix login bug", "status": "pending"}])
        result = orjson.loads(todo_tool(
            todos=[{"id": "1", "content": "Fix login bug",
                    "status": "in_progress"}],
            store=store,
        ))
        assert result["warnings"] == []


class TestDuplicateContentWarnings:
    def test_same_content_different_ids_warns(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[
                {"id": "a", "content": "Write docs", "status": "pending"},
                {"id": "b", "content": "Write docs", "status": "pending"},
            ],
            store=store,
        ))
        assert any(
            "Duplicate content across ids" in w and "Write docs" in w
            for w in result["warnings"]
        )

    def test_same_content_same_id_deduped_no_warning(self):
        store = TodoStore()
        result = orjson.loads(todo_tool(
            todos=[
                {"id": "a", "content": "Write docs", "status": "pending"},
                {"id": "a", "content": "Write docs", "status": "completed"},
            ],
            store=store,
        ))
        assert result["warnings"] == []
        assert result["summary"]["total"] == 1
        assert result["todos"][0]["status"] == "completed"


class TestHydrationReplayKeepsNotesAndCode:
    def test_notes_and_code_survive_replay(self):
        store = TodoStore()
        hydrated = [
            {"id": "1", "content": "Task one", "status": "in_progress",
             "notes": "note one", "code": "print(1)"},
            {"id": "2", "content": "Task two", "status": "pending",
             "notes": "", "code": ""},
        ]
        result = store.write(hydrated, merge=False)
        assert result[0]["notes"] == "note one"
        assert result[0]["code"] == "print(1)"
        assert "notes" not in result[1]
        assert "code" not in result[1]

    def test_replay_result_round_trips_through_todo_tool(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Task", "status": "completed",
             "notes": "n", "code": "print(1)"},
        ])
        # A read-style snapshot carrying notes/code replays into a fresh store.
        fresh = TodoStore()
        snapshot = store.read()
        replayed = fresh.write(snapshot, merge=False)
        assert replayed[0]["notes"] == "n"
        assert replayed[0]["code"] == "print(1)"
