"""Behaviour pins for the ``auto`` shell heuristic.

``detect_command_shell`` decides whether a terminal command line was written for
bash or for PowerShell, and the ``terminal`` tool's ``shell="auto"`` default trusts
that answer.  Two things are therefore under test here and nowhere else:

* **Precision.** :data:`UNDECIDED` lists shapes the heuristic must NOT answer: a
  confident wrong answer runs the user's command in the wrong shell, so a new rule
  that starts deciding one of those is a regression, not an improvement.
* **Evidence.** Every decided case names the rule that decided it, so a test reads
  as the rule it pins (``test_heredoc_is_bash``) rather than as an expectation
  nobody can trace back.

``VERIFIED_*`` are the commands the integration was specified against; the rest is
one test per rule tier.  Only ``pytest.mark.parametrize`` is used — no fixtures.
"""

from __future__ import annotations

import time

import pytest

from tools.environments.shell_detect import (
    MAX_ANALYZED,
    SHELL_BASH,
    SHELL_PWSH,
    ShellGuess,
    detect_command_shell,
)

#: Commands the spec requires to be decided as bash.
VERIFIED_BASH = [
    'x=abc; echo "bash:${x:1}"',
    'for f in *.txt; do wc -l "$f"; done',
    "apt-get install -y curl 2>&1 | tee /tmp/install.log",
    "cat > /tmp/notes.md <<'EOF'\nhello\nEOF",
    "docker run --rm -v $(pwd):/app node:20 npm ci",
    "chmod +x ./scripts/build.sh && ./scripts/build.sh",
    "if [ -f package.json ]; then npm ci; fi",
    "while true; do sleep 1; done &",
]

#: Commands the spec requires to be decided as pwsh.
VERIFIED_PWSH = [
    "Write-Output ('ps:' + (2+2))",
    "Get-ChildItem -Path . -Recurse -Filter *.ps1 | Select-Object -First 5",
    "$files = Get-ChildItem *.ps1 -Recurse",
    "Get-Process | Where-Object { $_.CPU -gt 50 } | Sort-Object CPU -Descending",
    "$env:PATH -split ';' | Where-Object { Test-Path $_ }",
    "[System.IO.File]::WriteAllText('out.txt', 'hi')",
    "$cfg = @{ Name = 'x'; Port = 8080 }",
    "if (Test-Path $p) { Remove-Item $p -Force }",
    "Get-ChildItem C:\\Users -Recurse -ErrorAction SilentlyContinue",
]

#: Commands the spec requires to stay undecided (the caller's default decides).
VERIFIED_UNDECIDED = [
    "git status",
    'git commit -m "Get-ChildItem"',
    "ls -la",
    'echo "Get-ChildItem"',
    "echo 'x=1'",
    'python -c "print(1)"',
    "# Get-ChildItem",
    "",
    "make test",
    "npm run build",
    "docker ps -a",
    "grep -e Foo-Bar file.txt",
]

#: Neutral commands that work under both shells: no evidence either way.
NEUTRAL = [
    "ls",
    "cd build",
    "echo hi",
    "cat README.md",
    "pwd",
    "mkdir out",
    "python script.py",
    "curl -s https://example.com",
    "git log --oneline | head -5",
    "rsync -av src/ dst/",
    "docker compose up -d",
    "echo $HOME",
]


class TestPublicApi:
    """The shape the integration is written against."""

    def test_returns_a_shell_guess(self):
        guess = detect_command_shell("Get-ChildItem")
        assert isinstance(guess, ShellGuess)
        assert guess.shell == SHELL_PWSH
        assert guess.detected is True
        assert isinstance(guess.reasons, tuple)

    @pytest.mark.parametrize("value", [None, b"x=1", 123, 4.5, [], {}, object()])
    def test_non_string_input_is_undecided(self, value):
        guess = detect_command_shell(value)
        assert guess == ShellGuess(SHELL_BASH, False, (), 0, 0)

    @pytest.mark.parametrize("value", ["", " ", "\n\n", "\t \t", "\r\n"])
    def test_blank_input_is_undecided(self, value):
        guess = detect_command_shell(value)
        assert guess.detected is False
        assert guess.shell == SHELL_BASH

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("bash", SHELL_BASH),
            ("  bash  ", SHELL_BASH),
            ("pwsh", SHELL_PWSH),
            ("powershell", SHELL_PWSH),
            ("  PWSH ", SHELL_PWSH),
            ("ps7", SHELL_PWSH),
            # the spellings a Windows caller actually holds
            ("pwsh.exe", SHELL_PWSH),
            ("powershell.exe", SHELL_PWSH),
            ("PowerShell 7", SHELL_PWSH),
            ("PowerShell 5.1", SHELL_PWSH),
            ("git-bash", SHELL_BASH),
            (None, SHELL_BASH),
            ("", SHELL_BASH),
            (" ", SHELL_BASH),
            ("garbage", SHELL_BASH),
            (5, SHELL_BASH),
            (b"pwsh", SHELL_BASH),
        ],
    )
    def test_default_is_coerced(self, raw, expected):
        guess = detect_command_shell("git status", default=raw)
        assert guess.detected is False
        assert guess.shell == expected

    @pytest.mark.parametrize("command", VERIFIED_UNDECIDED + NEUTRAL)
    @pytest.mark.parametrize("default", [SHELL_BASH, SHELL_PWSH])
    def test_undecided_echoes_the_default(self, command, default):
        guess = detect_command_shell(command, default=default)
        assert (guess.shell, guess.detected, guess.reasons) == (default, False, ())

    def test_default_is_keyword_only(self):
        with pytest.raises(TypeError):
            detect_command_shell("git status", SHELL_PWSH)  # type: ignore[misc]

    def test_detection_wins_over_the_default(self):
        guess = detect_command_shell("Get-ChildItem", default=SHELL_BASH)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)

    @pytest.mark.parametrize(
        "command", VERIFIED_BASH + VERIFIED_PWSH + VERIFIED_UNDECIDED
    )
    def test_repeated_calls_are_identical(self, command):
        assert detect_command_shell(command) == detect_command_shell(command)

    @pytest.mark.parametrize("command", VERIFIED_UNDECIDED + NEUTRAL)
    def test_undecided_has_no_reasons(self, command):
        assert detect_command_shell(command).reasons == ()

    @pytest.mark.parametrize("command", VERIFIED_BASH + VERIFIED_PWSH)
    def test_reasons_belong_to_the_winning_side(self, command):
        guess = detect_command_shell(command)
        assert 1 <= len(guess.reasons) <= 5
        assert all(reason.startswith(guess.shell + ":") for reason in guess.reasons)

    @pytest.mark.parametrize("command", VERIFIED_BASH + VERIFIED_PWSH)
    def test_scores_report_the_comparison(self, command):
        guess = detect_command_shell(command)
        winner, loser = (
            (guess.bash_score, guess.pwsh_score)
            if guess.shell == SHELL_BASH
            else (guess.pwsh_score, guess.bash_score)
        )
        assert winner > loser


class TestRobustness:
    """A malformed command must never break a tool call."""

    @pytest.mark.parametrize(
        "command",
        [
            "'",
            '"',
            "`",
            "'''",
            'echo "unterminated',
            "echo 'unterminated",
            "echo `unterminated",
            "\x00\x00",
            "x\x00=1",
            "中文 🚀 echo hi",
            "emoji 🧪 Get-ChildItem",
            "echo \xff\xfe",
            "\\" * 64,
            "((((((((((",
        ],
    )
    def test_malformed_input_returns_a_guess(self, command):
        guess = detect_command_shell(command)
        assert isinstance(guess, ShellGuess)
        assert guess.shell in (SHELL_BASH, SHELL_PWSH)

    def test_unterminated_heredoc_still_is_bash(self):
        # The operator itself is the evidence; the unterminated body only means the
        # mask runs to the end of the input.
        guess = detect_command_shell("cat <<EOF\nno terminator here")
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)
        assert "bash:here-doc" in guess.reasons

    def test_one_megabyte_blob_does_not_raise(self):
        guess = detect_command_shell("echo hi; " * 120_000)
        assert isinstance(guess, ShellGuess)

    def test_prefix_cap_bounds_the_work(self):
        command = "x=1; echo hi " * 20_000
        assert len(command) > MAX_ANALYZED
        started = time.monotonic()
        guess = detect_command_shell(command)
        elapsed = time.monotonic() - started
        assert guess.shell == SHELL_BASH
        # Generous on purpose: the point is that the analysis is bounded, not that a
        # particular machine is fast (a quadratic rule took minutes here).
        assert elapsed < 5.0

    @pytest.mark.parametrize(
        "filler",
        [" ", ";", "\n", "(", "-eq ", "$"],
    )
    def test_pathological_prefix_is_bounded(self, filler):
        started = time.monotonic()
        detect_command_shell(filler * MAX_ANALYZED)
        assert time.monotonic() - started < 5.0


class TestBashEvidence:
    """One test per bash rule tier."""

    @pytest.mark.parametrize(
        ("command", "label"),
        [
            ("#!/bin/bash", "bash:shebang:bash"),
            ("#!/usr/bin/env bash", "bash:shebang:bash"),
            ("#!/bin/sh", "bash:shebang:sh"),
            ("#!/usr/bin/env zsh", "bash:shebang:zsh"),
        ],
    )
    def test_shebang_is_bash(self, command, label):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)
        assert guess.reasons[0] == label

    @pytest.mark.parametrize(
        "command", ["#!/usr/bin/env python3", "#!/usr/bin/node", "#!/bin/perl"]
    )
    def test_non_shell_shebang_is_not_evidence(self, command):
        assert detect_command_shell(command).detected is False

    def test_heredoc_is_bash(self):
        guess = detect_command_shell("cat > notes.md <<'EOF'\nbody\nEOF")
        assert guess.shell == SHELL_BASH
        assert guess.reasons[0] == "bash:here-doc"

    def test_here_string_is_bash(self):
        guess = detect_command_shell("cat <<< 'text'")
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)

    def test_param_expansion_is_bash(self):
        guess = detect_command_shell("echo ${HOME}/lib")
        assert guess.reasons[0] == "bash:param-expansion"

    def test_arith_expansion_is_bash(self):
        guess = detect_command_shell("echo $((1 + 2))")
        assert guess.reasons[0] == "bash:arith-expansion"

    @pytest.mark.parametrize(
        "command",
        [
            "for f in *.txt; do echo $f; done",
            "while true; do sleep 1; done",
            "until false; do sleep 1; done",
            "if [ -f x ]; then echo y; fi",
            'case "$1" in a) echo a;; esac',
            "select x in a b; do echo $x; done",
        ],
    )
    def test_compound_keyword_is_bash(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)

    def test_test_bracket_is_bash(self):
        guess = detect_command_shell('[[ -n "$1" ]]')
        assert guess.reasons[0] == "bash:test-bracket"

    @pytest.mark.parametrize(
        "command", ["FOO=bar", "x=1", "LC_ALL=C sort f", "PATH=/usr/bin:$PATH ls"]
    )
    def test_env_assignment_is_bash(self, command):
        guess = detect_command_shell(command)
        assert guess.shell == SHELL_BASH
        assert "bash:env-assignment" in guess.reasons

    @pytest.mark.parametrize(
        "command", ["export PATH=/usr/bin", "readonly X=1", "local y=2"]
    )
    def test_declaration_is_bash(self, command):
        guess = detect_command_shell(command)
        assert "bash:declaration" in guess.reasons

    @pytest.mark.parametrize("command", ["source ~/.bashrc", ". ./activate"])
    def test_source_is_bash(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)
        assert guess.reasons[0].startswith("bash:source")

    def test_windows_relative_path_is_not_source(self):
        # `.\script.ps1` masks to `. script.ps1`, which must not read as bash's `.`.
        guess = detect_command_shell(".\\script.ps1")
        assert guess.shell == SHELL_PWSH

    @pytest.mark.parametrize(
        "command", ["set -euo pipefail", "set -x", "set -o pipefail"]
    )
    def test_set_option_is_bash(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0].startswith("bash:set-option")

    def test_trap_exit_is_bash(self):
        guess = detect_command_shell("trap 'cleanup' EXIT")
        assert guess.reasons[0] == "bash:trap-exit:EXIT"

    def test_background_ampersand_is_bash(self):
        guess = detect_command_shell("sleep 5 &")
        assert guess.reasons[0] == "bash:background"

    def test_double_ampersand_is_not_backgrounding(self):
        # `&&` is PowerShell 7 syntax too: it must stay in the weak tier.
        guess = detect_command_shell("sleep 1 && sleep 2")
        assert guess.detected is False

    @pytest.mark.parametrize(
        "command", ["ls > /dev/null", "cat /etc/hosts", "ls /proc/1"]
    )
    def test_posix_device_path_is_bash(self, command):
        guess = detect_command_shell(command)
        assert guess.shell == SHELL_BASH
        assert "bash:posix-path" in guess.reasons

    @pytest.mark.parametrize(
        "command",
        ["sudo systemctl restart nginx", "apt-get install -y curl", "brew install jq"],
    )
    def test_admin_tool_is_bash(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)
        assert guess.reasons[0].startswith("bash:admin-tool")

    @pytest.mark.parametrize("command", ["./configure", "/usr/bin/python3 -V"])
    def test_absolute_path_command_is_bash(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0].startswith("bash:absolute-path")

    def test_command_substitution_is_bash(self):
        guess = detect_command_shell("docker run -v $(pwd):/app img")
        assert guess.reasons[0] == "bash:command-substitution"

    def test_command_substitution_yields_to_a_cmdlet_inside_it(self):
        # `$( )` exists in both shells, so its body decides.
        assert detect_command_shell("echo $(Get-Date)").shell == SHELL_PWSH

    @pytest.mark.parametrize("command", NEUTRAL)
    def test_posix_utility_alone_is_undecided(self, command):
        assert detect_command_shell(command).detected is False

    @pytest.mark.parametrize(
        "command",
        [
            'echo "Get-ChildItem"',
            "grep -rn 'Write-Host' .",
            'git commit -m "Get-ChildItem"',
        ],
    )
    def test_quoted_text_is_not_evidence(self, command):
        assert detect_command_shell("ls").detected is False
        assert detect_command_shell(command).shell != SHELL_PWSH

    @pytest.mark.parametrize(
        "command",
        [
            "echo $'Get-ChildItem'",
            "echo $'it\\'s ; Get-ChildItem'",
            'echo $"Get-ChildItem"',
        ],
    )
    def test_ansi_c_quoting_is_one_string(self, command):
        # bash's `$'…'` (and `$"…"`) honour `\'`, so the span is a single string and
        # nothing inside it — the escaped quote included — becomes readable code.
        assert detect_command_shell(command).detected is False

    @pytest.mark.parametrize(
        "command",
        ["find . -name '*.py' -mtime -7", "find . -name *.ps1", "find /var -type d"],
    )
    def test_find_predicate_is_bash(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)
        assert guess.reasons[0].startswith("bash:find-predicate")

    @pytest.mark.parametrize(
        "command", ["find . -name *.ps1", "grep -rn --include=*.ps1 x .", "ls x*.ps1"]
    )
    def test_ps1_glob_is_not_a_script(self, command):
        # `*.ps1` names a *shape* of file — what a POSIX line writes — and only
        # `build.ps1` names one, so the pattern carries no pwsh evidence.
        assert "pwsh:script-file" not in detect_command_shell(command).reasons

    @pytest.mark.parametrize(
        "command", ["# Get-ChildItem", "# x=1", "ls # Get-ChildItem"]
    )
    def test_comment_is_not_evidence(self, command):
        assert detect_command_shell(command).detected is False


class TestPwshEvidence:
    """One test per pwsh rule tier."""

    @pytest.mark.parametrize(
        "command",
        ["Get-ChildItem", "Write-Output 1", "Remove-Item x", "Sort-Object Name"],
    )
    def test_cmdlet_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)
        assert guess.reasons[0].startswith("pwsh:cmdlet")

    @pytest.mark.parametrize(
        "command",
        [
            "Get-Process | Where-Object { $_.CPU -gt 50 }",
            "if (Test-Path $p) { Remove-Item $p -Force }",
            "ls; Get-Date",
            "& Get-Item x",
        ],
    )
    def test_cmdlet_in_command_position_is_pwsh(self, command):
        assert detect_command_shell(command).shell == SHELL_PWSH

    def test_custom_verb_noun_is_pwsh(self):
        guess = detect_command_shell("Foo-Bar -Path .")
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)

    @pytest.mark.parametrize(
        "command", ["grep -e Foo-Bar file.txt", "echo Foo-Bar", "ls Foo-Bar"]
    )
    def test_custom_verb_noun_mid_argument_is_not_evidence(self, command):
        assert detect_command_shell(command).detected is False

    def test_lowercase_hyphenated_tool_is_not_a_cmdlet(self):
        # `apt-get` is shaped like a cmdlet but its verb is not an approved one.
        assert detect_command_shell("apt-get install -y curl").shell == SHELL_BASH

    def test_quoted_bash_is_not_bash(self):
        guess = detect_command_shell("Write-Output 'ls -la'")
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)

    @pytest.mark.parametrize(
        "command", ["$env:PATH", "$global:x = 1", "$using:cfg", "$script:y"]
    )
    def test_drive_variable_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0].startswith("pwsh:drive-variable")

    @pytest.mark.parametrize(
        "command", ["$x = 1", "$files = Get-ChildItem", "$obj.Prop = 2"]
    )
    def test_variable_assignment_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert guess.shell == SHELL_PWSH
        assert "pwsh:variable-assignment" in guess.reasons

    @pytest.mark.parametrize(
        "command",
        [
            "[System.IO.File]::Exists($p)",
            "[regex]::Match($s, $p)",
            "[math]::Round(1.5)",
        ],
    )
    def test_static_member_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0].startswith("pwsh:static-member")

    @pytest.mark.parametrize(
        "command", ["[int]$x", "[string[]]$a", "[pscustomobject]$o"]
    )
    def test_cast_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0].startswith("pwsh:cast")

    def test_hashtable_is_pwsh(self):
        guess = detect_command_shell("@{ Name = 'x' }")
        assert guess.reasons[0] == "pwsh:hashtable"

    def test_array_literal_is_pwsh(self):
        assert detect_command_shell("@(1, 2, 3)").reasons[0] == "pwsh:array-literal"

    def test_splat_is_pwsh(self):
        guess = detect_command_shell("Write-Host @params")
        assert "pwsh:splat:params" in guess.reasons

    @pytest.mark.parametrize(
        "command",
        [
            "npm install @angular/core",
            "pnpm add @types/node -D",
            "yarn add @babel/core && yarn build",
            "npx @vue/cli create app",
            "ssh user@host uptime",
        ],
    )
    def test_scoped_package_is_not_a_splat(self, command):
        # `@scope/name` is a package name and `user@host` is an address: the `@` is
        # glued to the rest of a data word, so neither is PowerShell's `@params`.
        assert detect_command_shell(command).detected is False

    @pytest.mark.parametrize(
        "command",
        ["try { echo hi } catch { echo err }", "try { echo hi } finally { echo done }"],
    )
    def test_try_catch_is_pwsh(self, command):
        # bash has no `try`: it would take a user function named `try` *and* a literal
        # `catch {` argument, a spelling no bash script uses.
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)
        assert guess.reasons[0] == "pwsh:try-catch"

    @pytest.mark.parametrize(
        "command", ["try() { echo hi; }", "function try { echo hi; }"]
    )
    def test_bash_try_function_is_not_evidence(self, command):
        assert detect_command_shell(command).detected is False

    def test_param_block_is_pwsh(self):
        assert detect_command_shell("param($a, $b)").reasons[0] == "pwsh:param-block"

    def test_cmdlet_binding_is_pwsh(self):
        guess = detect_command_shell("[CmdletBinding()]\nparam()")
        assert "pwsh:cmdlet-binding" in guess.reasons

    def test_requires_directive_is_pwsh(self):
        guess = detect_command_shell("#Requires -Version 7")
        assert guess.reasons[0] == "pwsh:requires"

    def test_block_comment_is_pwsh(self):
        assert detect_command_shell("<# note #>").reasons[0] == "pwsh:block-comment"

    @pytest.mark.parametrize("command", ["begin { }", "process { }", "end { }"])
    def test_script_block_is_pwsh(self, command):
        assert detect_command_shell(command).reasons[0] == "pwsh:script-block"

    @pytest.mark.parametrize(
        "command", ["$_.Name", "$true", "$null", "$LASTEXITCODE", "$PSVersionTable"]
    )
    def test_automatic_variable_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0] == "pwsh:automatic-variable"

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("$x -eq 1", SHELL_PWSH),
            ("$x -in $list", SHELL_PWSH),
            ('if [ "$x" -eq 1 ]', None),
            ("grep -in foo bar.txt", None),
            # `-not`/`-name` are `test`'s and `find`'s flags, not PowerShell's: the
            # `find` predicate is what decides this one, and it decides bash.
            ("find . -not -name x", SHELL_BASH),
            ("awk -f prog.awk data", None),
        ],
    )
    def test_operator_needs_a_value_beside_it(self, command, expected):
        guess = detect_command_shell(command)
        assert (guess.shell if guess.detected else None) == expected

    def test_backtick_continuation_is_pwsh(self):
        guess = detect_command_shell("Write-Host `\n  hi")
        assert "pwsh:backtick-continuation" in guess.reasons

    @pytest.mark.parametrize("command", ["ls | %{ $_ }", "ls | ? { $_ }"])
    def test_filter_block_is_pwsh(self, command):
        assert "pwsh:filter-block" in detect_command_shell(command).reasons

    def test_windows_path_is_pwsh(self):
        guess = detect_command_shell("Get-Item C:\\Windows")
        assert "pwsh:windows-path" in guess.reasons

    def test_windows_path_alone_is_not_decisive(self):
        # A single `C:\` reference is STRONG, so a decisive bash signal outranks it.
        assert detect_command_shell("C:\\Windows").detected is True
        assert detect_command_shell("cd C:\\Windows && x=1").shell == SHELL_BASH

    def test_ps1_reference_is_pwsh(self):
        assert "pwsh:script-file" in detect_command_shell("Get-Item build.ps1").reasons

    @pytest.mark.parametrize("command", [".\\build.ps1", "./deploy.ps1"])
    def test_ps1_at_command_position_is_decisive(self, command):
        # bash cannot execute a `.ps1`, so where a command name sits the suffix is a
        # decision on its own, not a hint that another signal can outvote.
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)
        assert guess.reasons[0].startswith("pwsh:script-command")

    def test_ps1_as_an_argument_is_only_strong(self):
        guess = detect_command_shell("Get-Item build.ps1")
        assert "pwsh:script-file" in guess.reasons
        assert not any(
            reason.startswith("pwsh:script-command") for reason in guess.reasons
        )

    @pytest.mark.parametrize(
        "command", [". .\\build.ps1", ".\\build.ps1 -Configuration Release"]
    )
    def test_dot_sourced_ps1_is_pwsh_not_bash(self, command):
        # `. .\build.ps1` is spelled exactly like bash's `. FILE`, but the operand is a
        # PowerShell script: `.\` is a path separator, not an escape.
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)
        assert not any(reason.startswith("bash:source") for reason in guess.reasons)

    @pytest.mark.parametrize(
        "command",
        ["Get-Item -LiteralPath x", "Get-ChildItem -ErrorAction Stop"],
    )
    def test_named_parameter_with_its_command_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)

    @pytest.mark.parametrize("command", ["ls -Recurse", "head -c 5 -Recurse"])
    def test_named_parameter_alone_is_a_hint_not_evidence(self, command):
        # A parameter qualifies the command it binds to.  The spelling alone cannot
        # tell `ls -Recurse` (a cmdlet alias) from a bash pipeline that happens to end
        # in a PowerShell-shaped switch, so it is WEAK: the cmdlet/alias rules decide.
        assert detect_command_shell(command).detected is False

    @pytest.mark.parametrize(
        "command", ["-NoProfile -Command x", "-ExecutionPolicy Bypass"]
    )
    def test_host_flag_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert guess.reasons[0].startswith("pwsh:host-flag")

    @pytest.mark.parametrize("command", ["gci", "iex", "iwr", "foreach", "measure"])
    def test_alias_is_pwsh(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)
        assert guess.reasons[0].startswith("pwsh:alias")


class TestAmbiguityPolicy:
    """The cases where the honest answer is "no decision"."""

    @pytest.mark.parametrize(
        "command",
        [
            'powershell -NoProfile -Command "Get-ChildItem"',
            'pwsh -c "Get-ChildItem"',
            "cmd /c dir",
            'bash -lc "ls -la"',
            "powershell.exe -File build.ps1",
        ],
    )
    def test_shell_wrapper_is_undecided(self, command):
        guess = detect_command_shell(command)
        assert guess.detected is False
        assert guess.shell == SHELL_BASH

    def test_tie_is_undecided(self):
        # One DECISIVE bash signal (the env assignment) against one DECISIVE pwsh
        # signal (the cmdlet): a tie is never resolved by guessing.
        guess = detect_command_shell("x=1; Get-ChildItem")
        assert guess.detected is False
        assert guess.bash_score == guess.pwsh_score

    def test_weak_tier_is_capped(self):
        # The cap is what makes the tiers an order rather than a sum: eight weak bash
        # hints score what three do, so nothing is decided either way.
        many = detect_command_shell(
            "ls -la | cat -n | head -c 5 | tail -f -v | sort -r 2>&1 && true || false"
        )
        few = detect_command_shell("ls -la && cat")
        assert many.detected is False
        assert many.bash_score == few.bash_score == 3

    def test_one_strong_signal_beats_the_whole_weak_pile(self):
        # bash saturates the WEAK tier (3) and a single STRONG pwsh hit (4) still wins:
        # `_WEAK_CAP < STRONG` is what that ordering rests on.
        guess = detect_command_shell(
            "ls -la | cat -n | head -c 5 | xargs grep -n build.ps1"
        )
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)
        assert (guess.bash_score, guess.pwsh_score) == (3, 4)

    def test_one_decisive_signal_beats_a_strong_plus_a_weak(self):
        # `x=1` (DECISIVE 8) against a drive path (STRONG 4) with a parameter (WEAK 1):
        # DECISIVE > STRONG + _WEAK_CAP, so the strong side cannot add its way past.
        guess = detect_command_shell("x=1; C:\\x -Recurse")
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)
        assert (guess.bash_score, guess.pwsh_score) == (8, 5)

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("rm -rf /tmp && Get-ChildItem", SHELL_PWSH),
            ("for f in *.ps1; do echo $f; done", SHELL_BASH),
            ("Get-ChildItem | sort", SHELL_PWSH),
            ("chmod +x build.sh | tee log", SHELL_BASH),
        ],
    )
    def test_higher_score_wins(self, command, expected):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (expected, True)


class TestVerifiedExamples:
    """The exact commands the integration was specified against."""

    @pytest.mark.parametrize("command", VERIFIED_BASH)
    def test_verified_bash_command(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, True)

    @pytest.mark.parametrize("command", VERIFIED_PWSH)
    def test_verified_pwsh_command(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_PWSH, True)

    @pytest.mark.parametrize("command", VERIFIED_UNDECIDED)
    def test_verified_undecided_command(self, command):
        guess = detect_command_shell(command)
        assert (guess.shell, guess.detected) == (SHELL_BASH, False)
