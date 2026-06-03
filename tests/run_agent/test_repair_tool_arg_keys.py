"""Tests for repair_tool_arg_keys().

LLMs frequently return tool call arguments with incorrect keys (e.g. "file"
instead of "path", "cmd" instead of "command").  repair_tool_arg_keys()
fixes these by:

1. Exact-match passthrough for keys already in the schema
2. Alias mapping via TOOL_FIELD_ALIASES
3. Fuzzy matching via difflib.get_close_matches with adaptive cutoff

The function runs *before* coerce_tool_args() so repaired keys then have
their values coerced as usual.
"""

from unittest.mock import patch

from model_tools import (
    repair_tool_arg_keys,
    coerce_tool_args,
    set_arg_repair_callback,
    get_arg_repair_callback,
    handle_function_call,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _mock_schema(properties):
    """Build a minimal tool schema with the given properties."""
    return {
        "name": "test_tool",
        "description": "test",
        "parameters": {
            "type": "object",
            "properties": properties,
        },
    }


# ── 1. Passthroughs — no repair needed ────────────────────────────────────


class TestPassthroughs:
    """Cases where repair_tool_arg_keys should return args unchanged."""

    def test_exact_match_no_change(self):
        schema = _mock_schema({
            "path": {"type": "string"},
            "limit": {"type": "integer"},
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"path": "foo.py", "limit": 50}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == args
            # When no repair is needed the original dict is returned as-is.
            assert result is args

    def test_empty_args(self):
        assert repair_tool_arg_keys("test_tool", {}) == {}

    def test_none_args(self):
        assert repair_tool_arg_keys("test_tool", None) is None

    def test_unknown_tool_no_schema(self):
        with patch("model_tools.registry.get_schema", return_value=None):
            args = {"path": "foo.py"}
            result = repair_tool_arg_keys("unknown_tool", args)
            assert result == args

    def test_tool_with_no_properties(self):
        schema = {
            "name": "test_tool",
            "parameters": {"type": "object"},
        }
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"path": "foo.py"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == args


# ── 2. Alias mapping — common alias repairs ───────────────────────────────


class TestAliasMapping:
    """Repairs driven by the TOOL_FIELD_ALIASES lookup table."""

    def test_file_to_path(self):
        schema = _mock_schema({"path": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"file": "foo.py"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"path": "foo.py"}

    def test_title_to_name(self):
        schema = _mock_schema({"name": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"title": "My Skill"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"name": "My Skill"}

    def test_cmd_to_command(self):
        schema = _mock_schema({"command": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"cmd": "ls"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"command": "ls"}

    def test_dir_to_path(self):
        schema = _mock_schema({"path": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"dir": "/tmp"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"path": "/tmp"}

    def test_body_to_content(self):
        schema = _mock_schema({"content": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"body": "hello"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"content": "hello"}

    def test_link_to_image_url(self):
        schema = _mock_schema({"image_url": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"link": "https://example.com/img.png"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"image_url": "https://example.com/img.png"}

    def test_q_to_query(self):
        schema = _mock_schema({"query": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"q": "python testing"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"query": "python testing"}

    def test_id_to_session_id(self):
        schema = _mock_schema({"session_id": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"process_id": "sess-123"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"session_id": "sess-123"}

    # TASK aliases
    def test_jobs_to_tasks(self):
        schema = _mock_schema({"tasks": {"type": "array"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"jobs": ["a", "b"]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"tasks": ["a", "b"]}

    def test_batch_to_tasks(self):
        schema = _mock_schema({"tasks": {"type": "array"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"batch": ["x", "y"]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"tasks": ["x", "y"]}

    def test_tools_to_toolsets(self):
        schema = _mock_schema({"toolsets": {"type": "array"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"tools": ["read_file", "terminal"]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"toolsets": ["read_file", "terminal"]}

    # TODO aliases
    def test_items_to_todos(self):
        schema = _mock_schema({"todos": {"type": "array"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"items": [{"title": "fix bug"}]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"todos": [{"title": "fix bug"}]}

    def test_update_to_merge(self):
        schema = _mock_schema({"merge": {"type": "boolean"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"update": True}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"merge": True}

    # INPUT aliases
    def test_input_to_text(self):
        schema = _mock_schema({"text": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"input": "hello world"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"text": "hello world"}

    # SEARCH aliases
    def test_search_type_to_target(self):
        schema = _mock_schema({"target": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"search_type": "files"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"target": "files"}

    def test_queries_to_question(self):
        schema = _mock_schema({"question": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"queries": "what is python?"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"question": "what is python?"}


# ── 3. Normalization — casing / separator normalization ───────────────────


class TestNormalization:
    """Keys that differ only in case or separators fall through to fuzzy match.

    The implementation does not perform explicit case or hyphen normalization;
    difflib.get_close_matches handles these when the strings are close enough.
    """

    def test_case_insensitive_match(self):
        """Capitalised key close enough to be caught by fuzzy matching."""
        schema = _mock_schema({"command": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"Command": "ls"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"command": "ls"}

    def test_hyphen_to_underscore(self):
        """Hyphenated key close enough to be caught by fuzzy matching."""
        schema = _mock_schema({"background_color": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"background-color": "red"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"background_color": "red"}


# ── 4. Fuzzy matching — typo tolerance ────────────────────────────────────


class TestFuzzyMatching:
    """Typo tolerance via difflib.get_close_matches."""

    def test_single_char_typo(self):
        schema = _mock_schema({"command": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"commmand": "ls"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"command": "ls"}

    def test_two_char_typo(self):
        schema = _mock_schema({"background": {"type": "boolean"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"backgroud": True}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"background": True}

    def test_close_miss(self):
        schema = _mock_schema({"question": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"quesion": "hello"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"question": "hello"}


# ── 5. Edge cases — safety / robustness ───────────────────────────────────


class TestEdgeCases:
    """Safety and robustness behaviours."""

    def test_unknown_key_preserved(self):
        """Keys that cannot be mapped are left untouched."""
        schema = _mock_schema({"command": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"command": "ls", "xyz_no_such_key": 123}
            result = repair_tool_arg_keys("test_tool", args)
            assert result["command"] == "ls"
            assert result["xyz_no_such_key"] == 123

    def test_multiple_repairs_in_one_call(self):
        """Alias and fuzzy repairs can happen in the same invocation."""
        schema = _mock_schema({
            "path": {"type": "string"},
            "command": {"type": "string"},
            "background": {"type": "boolean"},
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"file": "x.py", "cmd": "ls", "backgroud": True}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {
                "path": "x.py",
                "command": "ls",
                "background": True,
            }

    def test_no_false_positive_fuzzy(self):
        """A wildly incorrect long key must not be randomly matched."""
        schema = _mock_schema({
            "command": {"type": "string"},
            "background": {"type": "boolean"},
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"xyz_no_such_key": "nope"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"xyz_no_such_key": "nope"}

    def test_repair_then_coerce_interaction(self):
        """Repair runs before coercion: keys are fixed, then values are coerced."""
        schema = _mock_schema({
            "path": {"type": "string"},
            "limit": {"type": "integer"},
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"file": "README.md", "lines": "50"}
            repaired = repair_tool_arg_keys("test_tool", args)
            assert repaired == {"path": "README.md", "limit": "50"}

            coerced = coerce_tool_args("test_tool", repaired)
            assert coerced == {"path": "README.md", "limit": 50}
            assert isinstance(coerced["limit"], int)


# ── 6. Real tools — integration against actual registry schemas ───────────


class TestRealTools:
    """Integration tests using the live tool registry (no mocks)."""

    def test_read_file_real_schema(self):
        """read_file: alias repair for 'file'→'path' and 'lines'→'limit'."""
        args = {"file": "README.md", "lines": "50"}
        result = repair_tool_arg_keys("read_file", args)
        assert result == {"path": "README.md", "limit": "50"}

    def test_terminal_real_schema(self):
        """terminal: alias repair for 'cmd'→'command' and 'bg'→'background'."""
        args = {"cmd": "ls", "bg": "true"}
        result = repair_tool_arg_keys("terminal", args)
        assert result == {"command": "ls", "background": "true"}

    def test_clarify_real_schema(self):
        """clarify: alias repair for 'query'→'question'."""
        args = {"query": "What?"}
        result = repair_tool_arg_keys("clarify", args)
        assert result == {"question": "What?"}


# ── 7. Per-tool alias overrides ───────────────────────────────────────────


class TestToolSpecificAliases:
    """Per-tool aliases in TOOL_SPECIFIC_ALIASES take precedence."""

    def test_delegate_task_task_to_goal(self):
        """delegate_task: 'task'→'goal' overrides global 'task'→'prompt'."""
        schema = _mock_schema({"goal": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"task": "write tests"}
            result = repair_tool_arg_keys("delegate_task", args)
            assert result == {"goal": "write tests"}

    def test_delegate_task_prompt_to_goal(self):
        """delegate_task: 'prompt'→'goal'."""
        schema = _mock_schema({"goal": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"prompt": "write tests"}
            result = repair_tool_arg_keys("delegate_task", args)
            assert result == {"goal": "write tests"}

    def test_cronjob_command_to_action(self):
        """cronjob: 'command'→'action' overrides global 'command'→'acp_command'."""
        schema = _mock_schema({"action": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"command": "create"}
            result = repair_tool_arg_keys("cronjob", args)
            assert result == {"action": "create"}

    def test_cronjob_background_to_no_agent(self):
        """cronjob: 'background'→'no_agent' overrides global 'background'→'context'."""
        schema = _mock_schema({"no_agent": {"type": "boolean"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"background": True}
            result = repair_tool_arg_keys("cronjob", args)
            assert result == {"no_agent": True}

    def test_tool_specific_takes_precedence_over_global(self):
        """When global and tool-specific disagree, tool-specific wins."""
        schema = _mock_schema(
            {"toolsets": {"type": "array"}, "enabled_toolsets": {"type": "array"}}
        )
        with patch(
            "model_tools.TOOL_SPECIFIC_ALIASES",
            {"test_tool": {"tools": "enabled_toolsets"}},
        ):
            with patch("model_tools.registry.get_schema", return_value=schema):
                args = {"tools": ["web"]}
                result = repair_tool_arg_keys("test_tool", args)
                # Without the override, global would map tools→toolsets.
                # With the override, it should map tools→enabled_toolsets.
                assert result == {"enabled_toolsets": ["web"]}

    def test_unknown_tool_falls_back_to_global(self):
        """Tools without specific aliases still use global aliases."""
        schema = _mock_schema({"prompt": {"type": "string"}})
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"task": "write tests"}
            result = repair_tool_arg_keys("some_other_tool", args)
            assert result == {"prompt": "write tests"}


# ── 8. Recursive repair — nested objects and arrays ───────────────────────


class TestRecursiveRepair:
    """Recursive key repair inside nested objects and arrays of objects."""

    def test_nested_object_alias_repair(self):
        """Field names inside a nested object are repaired via aliases."""
        schema = _mock_schema({
            "config": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "command": {"type": "string"},
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"config": {"file": "x.py", "cmd": "ls"}}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"config": {"path": "x.py", "command": "ls"}}

    def test_nested_object_fuzzy_repair(self):
        """Typo keys inside a nested object are repaired via fuzzy match."""
        schema = _mock_schema({
            "settings": {
                "type": "object",
                "properties": {
                    "background": {"type": "boolean"},
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"settings": {"backgroud": True}}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"settings": {"background": True}}

    def test_array_of_objects_alias_repair(self):
        """Field names inside array items are repaired via aliases."""
        schema = _mock_schema({
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "command": {"type": "string"},
                    },
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {
                "tasks": [
                    {"file": "a.py", "cmd": "ls"},
                    {"file": "b.py", "cmd": "cat"},
                ]
            }
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {
                "tasks": [
                    {"path": "a.py", "command": "ls"},
                    {"path": "b.py", "command": "cat"},
                ]
            }

    def test_array_of_objects_fuzzy_repair(self):
        """Typo keys inside array items are repaired via fuzzy match."""
        schema = _mock_schema({
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                    },
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"items": [{"quesion": "hello"}, {"quesion": "world"}]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"items": [{"question": "hello"}, {"question": "world"}]}

    def test_deeply_nested_object_in_array(self):
        """Repairs propagate through arrays → objects → arrays."""
        schema = _mock_schema({
            "batch": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {
                "batch": [
                    {"steps": [{"file": "a.py"}]},
                    {"steps": [{"file": "b.py"}]},
                ]
            }
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {
                "batch": [
                    {"steps": [{"path": "a.py"}]},
                    {"steps": [{"path": "b.py"}]},
                ]
            }

    def test_nested_object_with_no_schema_properties_ignored(self):
        """Objects without nested properties in the schema are left untouched."""
        schema = _mock_schema({
            "metadata": {
                "type": "object",
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"metadata": {"foo": "bar"}}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"metadata": {"foo": "bar"}}

    def test_array_of_non_objects_ignored(self):
        """Arrays that don't contain objects per schema are left untouched."""
        schema = _mock_schema({
            "tags": {
                "type": "array",
                "items": {"type": "string"},
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"tags": ["a", "b", "c"]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"tags": ["a", "b", "c"]}

    def test_mixed_array_items_non_dicts_preserved(self):
        """Non-dict items in an array of objects are preserved as-is."""
        schema = _mock_schema({
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"tasks": ["not_a_dict", {"file": "x.py"}]}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"tasks": ["not_a_dict", {"path": "x.py"}]}

    def test_string_value_not_recursed(self):
        """A string value for an object-typed key is left untouched."""
        schema = _mock_schema({
            "config": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"config": "just_a_string"}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"config": "just_a_string"}

    def test_top_level_exact_match_with_nested_schema_no_nested_data(self):
        """When top-level keys match and nested data is absent, return original."""
        schema = _mock_schema({
            "config": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
            }
        })
        with patch("model_tools.registry.get_schema", return_value=schema):
            args = {"config": {"path": "foo.py"}}
            result = repair_tool_arg_keys("test_tool", args)
            assert result == {"config": {"path": "foo.py"}}


# ── 9. Callback mechanism ─────────────────────────────────────────────────


class TestArgRepairCallback:
    """Tests for the optional argument repair callback hook."""

    def test_set_get_callback(self):
        """Basic set/get round-trip for the callback."""

        def cb():
            pass

        set_arg_repair_callback(cb)
        assert get_arg_repair_callback() is cb
        set_arg_repair_callback(None)
        assert get_arg_repair_callback() is None

    def test_callback_called_on_repair(self):
        """Callback is invoked when argument keys are repaired."""
        calls = []

        def cb(tool_name, original_keys, repaired_keys):
            calls.append((tool_name, original_keys, repaired_keys))

        set_arg_repair_callback(cb)
        try:
            schema = _mock_schema({"path": {"type": "string"}})
            with patch("model_tools.registry.get_schema", return_value=schema):
                handle_function_call("todo", {"file": "x.py"})
            assert len(calls) == 1
            assert calls[0] == ("todo", ["file"], ["path"])
        finally:
            set_arg_repair_callback(None)

    def test_callback_not_called_when_no_repair(self):
        """Callback is NOT invoked when arguments already match the schema."""
        calls = []

        def cb(tool_name, original_keys, repaired_keys):
            calls.append((tool_name, original_keys, repaired_keys))

        set_arg_repair_callback(cb)
        try:
            schema = _mock_schema({"path": {"type": "string"}})
            with patch("model_tools.registry.get_schema", return_value=schema):
                handle_function_call("todo", {"path": "x.py"})
            assert len(calls) == 0
        finally:
            set_arg_repair_callback(None)

    def test_callback_exception_does_not_break_dispatch(self):
        """A callback that raises must not prevent the tool from dispatching."""

        def cb(tool_name, original_keys, repaired_keys):
            raise RuntimeError("boom")

        set_arg_repair_callback(cb)
        try:
            schema = _mock_schema({"path": {"type": "string"}})
            with patch("model_tools.registry.get_schema", return_value=schema):
                result = handle_function_call("todo", {"file": "x.py"})
            assert "todo must be handled by the agent loop" in result
        finally:
            set_arg_repair_callback(None)
