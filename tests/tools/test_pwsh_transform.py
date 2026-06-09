"""Tests for pwsh_transform warning propagation in proccess_pwsh.py."""

import pytest

from tools.environments.proccess_pwsh import pwsh_transform


class TestPwshTransformReturnsTuple:
    """pwsh_transform now returns (transformed_code, warnings_list)."""

    def test_no_transform_returns_empty_warnings(self):
        """Code with no PS7 syntax returns unchanged code and empty warnings."""
        code = "Write-Output 'hello'"
        result, warnings = pwsh_transform(code)
        assert result == code
        assert warnings == []

    def test_ternary_generates_warning(self):
        """Ternary operator transformation produces a warning."""
        code = "$result = $a ? $b : $c"
        result, warnings = pwsh_transform(code)
        assert "if" in result
        assert "else" in result
        assert len(warnings) == 1
        assert "ternary" in warnings[0]
        assert "Line 1:" in warnings[0]
        assert "$a ? $b : $c" in warnings[0]

    def test_null_coalescing_generates_warning(self):
        """Null-coalescing ?? transformation produces a warning."""
        code = "$result = $a ?? $b"
        result, warnings = pwsh_transform(code)
        assert "$null -ne" in result
        assert "else" in result
        assert len(warnings) == 1
        assert "null-coalescing" in warnings[0]
        assert "Line 1:" in warnings[0]
        # Should NOT say "null-coalescing assignment"
        assert "assignment" not in warnings[0]

    def test_null_coalescing_assignment_generates_warning(self):
        """Null-coalescing assignment ??= transformation produces a warning."""
        code = "$a ??= 'default'"
        result, warnings = pwsh_transform(code)
        assert "$null -eq" in result
        assert len(warnings) == 1
        assert "null-coalescing assignment" in warnings[0]
        assert "Line 1:" in warnings[0]

    def test_pipeline_chain_and_generates_warning(self):
        """Pipeline chain && transformation produces a warning."""
        code = "cmd1 && cmd2"
        result, warnings = pwsh_transform(code)
        assert "$?" in result
        assert "if" in result
        assert len(warnings) == 1
        assert "pipeline chain" in warnings[0]
        assert "Line 1:" in warnings[0]
        assert "&&" in warnings[0]

    def test_pipeline_chain_or_generates_warning(self):
        """Pipeline chain || transformation produces a warning."""
        code = "cmd1 || cmd2"
        result, warnings = pwsh_transform(code)
        assert "-not $?" in result
        assert "if" in result
        assert len(warnings) == 1
        assert "pipeline chain" in warnings[0]
        assert "||" in warnings[0]

    def test_null_conditional_dot_generates_warning(self):
        """Null-conditional member access ?. produces a warning."""
        code = "$obj?.Property"
        result, warnings = pwsh_transform(code)
        assert "$null -ne" in result
        assert len(warnings) == 1
        assert "null-conditional member access" in warnings[0]
        assert "Line 1:" in warnings[0]

    def test_null_conditional_bracket_generates_warning(self):
        """Null-conditional index ?[ produces a warning."""
        code = "$arr?[0]"
        result, warnings = pwsh_transform(code)
        assert "$null -ne" in result
        assert len(warnings) == 1
        assert "null-conditional index" in warnings[0]
        assert "Line 1:" in warnings[0]

    def test_multiple_transformations_multiple_warnings(self):
        """Code with multiple PS7 features produces multiple warnings."""
        code = "$a = $b ?? $c\n$d = $e ? $f : $g"
        result, warnings = pwsh_transform(code)
        assert len(warnings) == 2
        assert "Line 1:" in warnings[0]
        assert "null-coalescing" in warnings[0]
        assert "Line 2:" in warnings[1]
        assert "ternary" in warnings[1]

    def test_mixed_line_multiple_warnings(self):
        """A line with both ternary and ?? generates two warnings."""
        code = "$x = $a ?? $b; $y = $c ? $d : $e"
        result, warnings = pwsh_transform(code)
        assert len(warnings) == 2
        assert "Line 1:" in warnings[0]
        assert "Line 1:" in warnings[1]

    def test_warning_contains_original_and_rewritten(self):
        """Each warning shows both the original syntax and the rewritten form."""
        code = "$a ? $b : $c"
        result, warnings = pwsh_transform(code)
        assert "rewritten to" in warnings[0]
        assert "$a ? $b : $c" in warnings[0]
        assert "if" in warnings[0]

    def test_plain_code_no_warnings(self):
        """Plain PowerShell 5.1 code produces no warnings."""
        code = 'if ($true) { Write-Output "yes" } else { Write-Output "no" }'
        result, warnings = pwsh_transform(code)
        assert result == code
        assert warnings == []

    def test_not_confused_by_question_mark_in_string(self):
        """A ? inside a string does not trigger a ternary warning."""
        code = '$msg = "is this a ternary? no"'
        result, warnings = pwsh_transform(code)
        assert result == code
        assert warnings == []

    def test_not_confused_by_double_question_in_string(self):
        """?? inside a string does not trigger a null-coalescing warning."""
        code = '$msg = "what?? no way"'
        result, warnings = pwsh_transform(code)
        assert result == code
        assert warnings == []

    def test_line_numbers_are_one_based(self):
        """Warnings use 1-based line numbers (first line is Line 1)."""
        code = "$a ?? $b"
        result, warnings = pwsh_transform(code)
        assert warnings[0].startswith("Line 1:")

    def test_multiline_line_numbers_correct(self):
        """Multi-line code has correct line numbers in warnings."""
        code = "# comment\n$x = 1\n$y = $a ?? $b"
        result, warnings = pwsh_transform(code)
        assert len(warnings) == 1
        assert warnings[0].startswith("Line 3:")

    def test_pipeline_chain_rewritten_text(self):
        """Warning for chain operator includes clear before/after."""
        code = "do_this && do_that"
        result, warnings = pwsh_transform(code)
        assert "do_this && do_that" in warnings[0]
        assert "do_this; if ($?) { do_that }" in warnings[0]

    def test_null_conditional_dot_rewritten_text(self):
        """Warning for null-conditional dot shows original and rewritten."""
        code = "$config?.GetValue()"
        result, warnings = pwsh_transform(code)
        assert "null-conditional member access" in warnings[0]
        assert "$config?.GetValue()" in warnings[0]
        assert "rewritten to" in warnings[0]
