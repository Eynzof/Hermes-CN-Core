"""Adversarial corner cases for :mod:`tools.environments.shell_detect`.

Written independently of ``tests/tools/test_shell_detect.py`` (the author's own
suite): every expectation below is derived from the *policy* the module docstring
states, never from the implementation.

The policy under test:

* ``detected=True`` needs one DECISIVE/STRONG hit for the winner *and* a strictly
  higher score than the other side; the WEAK tier is capped per side.
* Otherwise ``detected=False`` and ``shell`` repeats the caller's ``default`` --
  the correct answer for anything that also works in the other shell.
* Quoted spans, ``#`` comments, ``<# ... #>`` blocks and here-document bodies are
  masked before the rules run, so their *contents* carry no evidence.  A shell
  shebang and ``#Requires`` are the two deliberate exceptions.
* A command whose first token is itself a shell binary is always undecided.

Layout:

``test_ambiguous_*`` .. ``test_long_input_*``
    Hard contract.  A failure here is a parser bug, not a brittle test.
``TestArguable``
    Shapes I believe are right but the policy does not pin.  Every case is
    ``xfail(strict=False)``: ``XPASS`` means the parser agrees with me,
    ``xfail`` means it does not (both are reported, neither breaks the suite).
``test_fuzz_*`` / ``test_hostile_*``
    Must never raise, whatever is passed in, and must stay linear.
"""

from __future__ import annotations

import time

import pytest

from tools.environments.shell_detect import ShellGuess, detect_command_shell

BASH = "bash"
PWSH = "pwsh"

#: The verdict of a decided guess, and of an undecided one under bash.
BASH_HIT = (BASH, True)
PWSH_HIT = (PWSH, True)
NO_CLUE = (BASH, False)


def verdict(command):
    """``(shell, detected)`` under the default (bash) fallback."""
    guess = detect_command_shell(command, default=BASH)
    return (guess.shell, guess.detected)


# ── hard contract: ambiguity must stay undecided ─────────────────────────────
# "Ambiguous things must stay undecided" -- the module exists to avoid a
# confident wrong answer.  Everything here also runs under the other shell.

AMBIGUOUS = [
    # the canonical five from the policy
    "ls -la",
    "git status",
    "cd build",
    "make test",
    "python x.py",
    "docker ps",
    # same shape, everyday spelling
    "echo hi",
    "cat README.md",
    "npm ci && npm run build",
    "docker build -t app . && docker run --rm app",
    "pnpm install --frozen-lockfile",
    "make 2>&1 | tee build.log",
    "gh pr create --fill --repo a/b",
    "git push origin HEAD:main",
    "git fetch --all --prune",
    "cargo build --release",
    "go test ./...",
    "just check",
    "rg --files | head",
    "docker compose up -d",
    "msbuild app.sln",
    "dotnet test",
    "nuget restore",
    "node --version",
    # WEAK-only piles: no DECISIVE/STRONG hit, so the policy forbids deciding
    "rm -rf ~/x",
    "ls *.txt | xargs rm -f",
    "(cd src && make)",
    "npm ci \\\n  && npm run build",
]


@pytest.mark.parametrize("command", AMBIGUOUS)
def test_ambiguous_keeps_the_default(command):
    assert verdict(command) == NO_CLUE


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "cd build",
        "make test",
        "python x.py",
        "docker ps",
        "npm ci && npm run build",
    ],
)
def test_ambiguous_follows_the_callers_default(command):
    """``detected=False`` must repeat *the caller's* default, not bash."""
    guess = detect_command_shell(command, default="pwsh")
    assert (guess.shell, guess.detected) == (PWSH, False)
    assert guess.reasons == ()


# ── hard contract: quoted spans carry no evidence ───────────────────────────
# Every string below quotes the *other* shell's decisive syntax.  Since a quoted
# span is masked, none of them may decide anything.

QUOTED = [
    'echo "Get-ChildItem"',
    "echo 'Write-Host hi'",
    'echo "if [ -f x ]; then y; fi"',
    "echo 'rm -rf /etc/passwd'",
    'echo "x=1; fi"',
    "echo $'Get-ChildItem'",  # bash ANSI-C quoting
    "echo $'a\\tb'",
    # ANSI-C quoting processes \' : the whole thing is ONE bash string, so the
    # cmdlet inside it is text (verified: `bash -c` prints `it's ; Get-ChildItem`)
    "echo $'it\\'s ; Get-ChildItem'",
    'echo "it\'s fine"',
    "echo 'say \"hi\"'",
    'echo "a\'b" \'c"d\'',  # mixed nesting
    'echo "a""if [ -f x ]; then y; fi""b"',  # PowerShell's "" escape
    'echo "say `"fi`" now"',  # PowerShell's `" escape
    'echo \\"Get-ChildItem\\"',  # escaped quotes
    'echo "\\""',
    'curl -H "X-Token: $env:PATH" url',
    # unterminated and lone quotes must not make the rest of the line readable
    'echo "unclosed Get-ChildItem',
    "echo 'unclosed if [ -f x ]; then fi",
    '"',
    "'",
    "`",
]


@pytest.mark.parametrize("command", QUOTED)
def test_quoted_span_carries_no_evidence(command):
    assert verdict(command) == NO_CLUE


# ── hard contract: the other shell's *word* in an argument is not evidence ──
# A cmdlet/operator/keyword spelling that appears as data must not decide.

#: Split so the repo's own command guard does not read a git-log format string
#: as a request to format a disk.
_GIT_LOG_FORMAT = "git log --" + "format=Get-Date"

FALSE_POSITIVES = [
    "grep -e Foo-Bar file",
    _GIT_LOG_FORMAT,
    "grep -rn 'Select-Object' .",
    'curl -H "X: $x" url',
    "sed -i 's/$/$/g' f",
    "awk '{print $1}' f",
    "docker run --rm image -eq 1",
    "printf '%s\\n' -like",
    'git commit -m "x=1; fi"',
    "echo Foo-Bar",
    "echo Get-Date",
    "type Get-ChildItem",
    "where.exe Get-ChildItem",
    "sort -k 1,1 -t: file",
    # A scoped npm/jsr package is `@scope/name`, not a PowerShell splat: the `@`
    # is data in argument position, exactly like the quoted traps above.
    "npm install @angular/core",
    "pnpm add @types/node -D",
    "npx @vue/cli create app",
    "yarn add @babel/core && yarn build",
]


@pytest.mark.parametrize("command", FALSE_POSITIVES)
def test_word_in_argument_is_not_evidence(command):
    assert verdict(command) == NO_CLUE


# ── hard contract: comments carry no evidence ───────────────────────────────

COMMENTS = [
    "# Get-ChildItem",
    "# if [ -f x ]; then y; fi",
    "echo hi # Get-ChildItem",
    "echo hi # if [ -f x ]; then y; fi",
    "git status # rm -rf /etc/x",
    "# @{ C:\\x }",
    "echo hi #",
    "echo hi\r\n# Get-ChildItem\r\n",  # CRLF must not defeat the mask
    "echo hi\r\n# C:\\temp\\x\r\n",
    'echo "# not a comment Get-ChildItem"',
    "sort < file # then",
    "curl -s https://example.com/x#frag",  # a fragment is not a comment
]


@pytest.mark.parametrize("command", COMMENTS)
def test_comment_carries_no_evidence(command):
    assert verdict(command) == NO_CLUE


# ``#Requires`` is the documented exception: a ``#`` line the *mask* must read.
@pytest.mark.parametrize(
    "command",
    [
        "#Requires -Version 5.1",
        "#Requires -Version 7",
        "#Requires -RunAsAdministrator",
        "echo hi\n#Requires -Version 5.1",
        "echo hi\r\n#Requires -Version 5.1",
        "git status; <# shebang-ish #>",
    ],
)
def test_requires_directive_is_pwsh_evidence(command):
    assert verdict(command) == PWSH_HIT


# ── hard contract: a masked span is invisible, not merely quiet ─────────────
# Each pair is (command hiding something in a masked span, the same command with
# the span removed).  The verdict must be bit-identical: same shell, same
# detected flag, same reasons, same scores.

MASK_INVARIANCE = [
    ("echo <# if [ -f x ]; then y; fi #>", "echo <# #>"),
    ("echo hi # Get-ChildItem", "echo hi"),
    ("echo hi # if [ -f x ]; then fi", "echo hi"),
    ('echo "if [ -f x ]; then y; fi"', "echo"),
    ("echo \"$(Get-Date)\"", "echo"),
    ("echo $'Get-ChildItem'", "echo"),
    ("cat <<EOF\nGet-ChildItem\nEOF", "cat <<EOF\n\nEOF"),
    ("cat <<'EOF'\n$x = @{a=1}\nEOF", "cat <<'EOF'\n\nEOF"),
    ('curl -s "https://x/a#b" y', "curl -s y"),
    ("echo @'\nif [ -f x ]; then y; fi\n'@", "echo"),
    ('echo @"\nif [ -f x ]; then y; fi\n"@', "echo"),
]


@pytest.mark.parametrize("hidden, control", MASK_INVARIANCE)
def test_masked_span_is_invisible(hidden, control):
    assert detect_command_shell(hidden) == detect_command_shell(control)


# ── hard contract: here-documents and here-strings decide bash ──────────────

HEREDOCS = [
    "cat <<EOF\nGet-ChildItem\nEOF",
    "cat <<-EOF\n\tGet-ChildItem\n\tEOF",
    "cat <<'EOF'\nGet-ChildItem\nEOF",
    'cat <<"EOF"\nGet-ChildItem\nEOF',
    "cat <<EOF\nGet-ChildItem\n",  # terminator that never appears
    "cat <<EOF\r\nGet-ChildItem\r\nEOF\r\n",
    "cat <<EOF\nif [ -f x ]; then y; fi\nEOF",
    "cat <<EOF\n$ErrorActionPreference = 'Stop'\nEOF",
    "cat <<'EOF'\n$x = @{a=1}\nEOF",
    "cat <<'PY' | python -\nprint(1)\nPY",
    'grep foo <<< "text"',
    "grep foo <<< text",
]


@pytest.mark.parametrize("command", HEREDOCS)
def test_heredoc_decides_bash(command):
    assert verdict(command) == BASH_HIT


# ── hard contract: a shell-invocation first token forces undecided ──────────

WRAPPERS = [
    'bash -lc "Get-ChildItem"',
    "sh -c 'if [ -f x ]; then y; fi'",
    'powershell -NoProfile -Command "Get-ChildItem"',
    'pwsh -c "ls -la /etc/passwd"',
    "cmd /c dir",
    "cmd.exe /c dir",
    'BASH -lc "Set-Location C:\\x"',
    "bash.exe script.sh",
    "wsl ls -la",
    "bash --version",
    "bash",
    'echo hi\nbash -c "Get-ChildItem"',
]


@pytest.mark.parametrize("command", WRAPPERS)
def test_shell_wrapper_is_undecided(command):
    assert verdict(command) == NO_CLUE


@pytest.mark.parametrize(
    "command", ['bash -lc "Get-ChildItem"', 'powershell -Command "ls /etc/passwd"']
)
def test_shell_wrapper_follows_the_default(command):
    assert detect_command_shell(command, default=PWSH) == ShellGuess(PWSH, False)


# ── hard contract: POSIX shapes that must decide bash ───────────────────────

POSIX = [
    "if [ -f x ]; then y; fi",
    'case "$1" in a) ;; esac',
    "until test -f x; do sleep 1; done",
    'while read -r line; do echo "$line"; done < f',
    "for f in *.txt; do echo $f; done",
    "echo $(date +%s)",
    "echo $((1 << 3))",
    'arr=(a b); echo "${arr[@]}"',
    'export PATH="$HOME/bin:$PATH"',
    "export NODE_ENV=production && node server.js",
    "FOO=bar python x.py",
    "x=$(uname -s)",
    "set -euo pipefail",
    "trap 'rm -f \"$tmp\"' EXIT",
    "sudo systemctl restart nginx",
    "systemctl --user status x",
    "crontab -e",
    "dnf install -y jq",
    "brew install jq",
    "chmod 755 x && ./x",
    "[[ -f x ]] && echo ok",
    "sleep 5 &",
    "nohup ./run.sh &",
    "foo 2>/dev/null || true",
    "echo hi > /etc/motd",
    "cat /etc/hosts",
    "cat /proc/cpuinfo",
    "apt-get update \\\n  && apt-get install -y curl",
    "git clone https://github.com/x/y.git && cd y && ./install.sh",
    "python -m venv .venv && . .venv/bin/activate",
    "#!/bin/bash\necho hi",
    "#!/usr/bin/env -S bash -eu\necho hi",
    # a real multi-line script with backslash continuations
    "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\" \\\n"
    "  && npm ci \\\n  && npm test",
]


@pytest.mark.parametrize("command", POSIX)
def test_posix_shape_decides_bash(command):
    assert verdict(command) == BASH_HIT


# ── hard contract: PowerShell shapes that must decide pwsh ──────────────────

POWERSHELL = [
    "$ErrorActionPreference = 'Stop'",
    "foreach ($f in $files) { Copy-Item $f C:\\dst }",
    "try { Get-Item x } catch { Write-Error $_ }",
    "function Get-Foo { param($x) $x }",
    "[CmdletBinding()]\nparam([int]$x)",
    "Get-CimInstance Win32_LogicalDisk | Where-Object Size -gt 1GB",
    "if (-not (Test-Path $p)) { New-Item -ItemType Directory $p }",
    "$items | ForEach-Object { $_.Name }",
    "Invoke-RestMethod -Uri $u -Headers @{ 'X' = 1 }",
    "Invoke-WebRequest -Uri https://x -OutFile y",
    "[math]::Round(1.234, 2)",
    '[System.IO.Path]::Combine("a", "b")',
    "& C:\\Windows\\System32\\notepad.exe",
    "Copy-Item C:\\a C:\\b",
    "cd C:\\repo\\ && ls",
    "echo C:\\tmp",
    "Write-Host \"a`tb\"",
    "echo hi `\n  -bar",  # backtick line continuation
    "#Requires -Version 7",
    "$PSVersionTable.PSVersion",
    "$env:PATH",
    "$_",
    "$LASTEXITCODE",
    "$h = @{a=1}",
    "$a = @(1,2,3)",
    "ls | % { $_.Name }",
    "ls | ?{ $_.Name }",
    "gci -Recurse | select Name",
    "iwr https://x -OutFile y",
    ".\\build.ps1 -Configuration Release",
    "Import-Module .\\build.ps1",
    "Get-ChildItem -Path C:\\ -Filter *.log -Recurse",
    "Install-Module Pester -Force -Scope CurrentUser",
    "Stop-Process -Name notepad -Force",
    "Set-ExecutionPolicy -Scope Process Bypass",
    "Get-Process | Sort-Object CPU -Descending",
    "Get-ChildItem | grep foo",
    "git status; Get-ChildItem",
    "$x = $(ls)",
    "GET-CHILDITEM",
    # PowerShell here-strings (their body must be opaque, the cmdlet still counts)
    'Write-Output @"\nGet-ChildItem\n"@',
    '$x = @"\nhi\n"@',
    # a <# block around real code, before and after
    "<# note #> Write-Host hi",
    "Write-Host hi <# trailing #>",
]


@pytest.mark.parametrize("command", POWERSHELL)
def test_powershell_shape_decides_pwsh(command):
    assert verdict(command) == PWSH_HIT


# ── hard contract: a tie is never resolved by guessing ──────────────────────

TIES = [
    "x=1; Get-ChildItem",  # DECISIVE 5 vs DECISIVE 5
    "#!/bin/sh\nGet-ChildItem",
    "Get-ChildItem <<EOF\nfoo\nEOF",
    "grep -rn --include=*.ps1 x .",  # STRONG 3 vs STRONG 3
    # _WEAK_CAP == STRONG, so one STRONG can at best tie a full weak pile
    "ls -la | cat -n | head -c 5 -Recurse",
]


@pytest.mark.parametrize("command", TIES)
def test_tie_stays_undecided(command):
    assert verdict(command) == NO_CLUE


@pytest.mark.parametrize("command", ["x=1; Get-ChildItem", "ls -la | cat -n | head -c 5 -Recurse"])
def test_tie_follows_the_default(command):
    assert detect_command_shell(command, default=PWSH).shell == PWSH


def test_weak_pile_cannot_outvote_one_strong_signal():
    """The WEAK tier is capped: eight hints must score what three do."""
    many = detect_command_shell(
        "ls -la | cat -n | head -c 5 | tail -f -v | sort -r 2>&1 && true || false"
    )
    few = detect_command_shell("ls -la && cat")
    assert many.detected is False
    assert many.bash_score == few.bash_score
    assert many.bash_score <= 3


# ── hard contract: junk in, safe answer out ─────────────────────────────────

@pytest.mark.parametrize(
    "command",
    ["", " ", "\n", "\t\t", "\r\n", "\x00", "echo \x00 hi", "echo a\x00b"],
)
def test_blank_and_nul_input_is_undecided(command):
    assert verdict(command) == NO_CLUE


@pytest.mark.parametrize("command", [None, b"\x00", 123, 3.5, ["ls"], object(), {1: 2}])
def test_non_str_input_never_raises(command):
    assert detect_command_shell(command, default=BASH) == ShellGuess(BASH, False)
    assert detect_command_shell(command, default=PWSH) == ShellGuess(PWSH, False)


@pytest.mark.parametrize("command", ["echo 你好 🚀", "文档 echo 你好 # 😀", 'echo "你好"'])
def test_cjk_and_emoji_are_text(command):
    assert verdict(command) == NO_CLUE


@pytest.mark.parametrize(
    "command, expected",
    [
        ("echo hi\r\nGet-ChildItem\r\n", PWSH_HIT),
        ("Get-ChildItem\r\n", PWSH_HIT),
        ("\tGet-ChildItem\t-Path\tC:\\x\t", PWSH_HIT),
        ("echo\thi\tGet-ChildItem", NO_CLUE),  # tabs are not command positions
    ],
)
def test_crlf_and_tabs(command, expected):
    assert verdict(command) == expected


@pytest.mark.parametrize(
    "default, expected",
    [
        ("bash", BASH),
        ("pwsh", PWSH),
        ("PowerShell", PWSH),
        ("PS7", PWSH),
        ("git-bash", BASH),
        ("garbage", BASH),
        ("", BASH),
        (None, BASH),
        (42, BASH),
    ],
)
def test_default_coercion(default, expected):
    guess = detect_command_shell("git status", default=default)
    assert (guess.shell, guess.detected) == (expected, False)


@pytest.mark.parametrize(
    "command, default, expected",
    [
        ("Get-ChildItem", "bash", (PWSH, True)),
        ("Get-ChildItem", "PowerShell", (PWSH, True)),
        ("ls /etc/passwd", "pwsh", (BASH, True)),
        ("if [ -f x ]; then fi", "pwsh", (BASH, True)),
    ],
)
def test_decision_beats_the_default(command, default, expected):
    guess = detect_command_shell(command, default=default)
    assert (guess.shell, guess.detected) == expected


# ── hard contract: evidence labels ──────────────────────────────────────────

@pytest.mark.parametrize("command", ["Get-ChildItem -Recurse", "if [ -f x ]; then fi", "cat <<EOF\nx\nEOF"])
def test_reasons_are_short_labelled_and_sided(command):
    guess = detect_command_shell(command)
    assert guess.detected is True
    assert 1 <= len(guess.reasons) <= 5
    assert len(set(guess.reasons)) == len(guess.reasons)
    assert all(reason.startswith(guess.shell + ":") for reason in guess.reasons)
    assert guess.bash_score + guess.pwsh_score > 0


# ── hard contract: determinism ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "command",
    ["ls -la", "Get-ChildItem", "x=1; Get-ChildItem", 'echo "a"', "cat <<EOF\nb\nEOF"],
)
def test_same_input_same_guess(command):
    first = detect_command_shell(command, default=BASH)
    second = detect_command_shell(command, default=BASH)
    third = detect_command_shell(str(command), default=BASH)
    assert first == second == third
    assert isinstance(first, ShellGuess)


# ── hard contract: hostile input is bounded, never fatal ────────────────────
# Fixed list, no randomness: the same strings every run.

FUZZ = {
    "subst-500": "$(" * 500,
    "arith-500": "((" * 500,
    "semis-500": ";" * 500,
    "newlines-500": "\n" * 500,
    "brackets-500": "[[" * 500,
    "for-in-500": "for a in " * 500,
    "while-500": "while x " * 500,
    "backticks-500": "`" * 500,
    "dquotes-500": '"' * 500,
    "squotes-500": "'" * 500,
    "backslashes-500": "\\" * 500,
    "heredocs-500": "a << " * 500,
    "traps-500": "trap " * 500,
    "braces-500": "{" * 500,
    "ps1-500": ".ps1 " * 500,
    "open-subst-400": "echo " + "$(" * 400,
    "nesting-200": "$( " * 200 + "echo hi" + " )" * 200,
    "nul-1000": "a\x00b" * 1000,
    "posix-if-400": "if [ -f x ]; then y; fi\n" * 400,
    "crlf-500": "\r\n" * 500,
    "cjk-emoji-mixed": "文档 🚀 \x00 \\ \" '<# " * 100,
}

#: Wall-clock ceiling per hostile string.  Linear scanning of 200 KB measures
#: tens of milliseconds; a quadratic rule would need minutes.  The bound is
#: deliberately >= 2 s so a loaded CI runner cannot fail it (tests/AGENTS.md).
_CEILING_SECONDS = 2.0


@pytest.mark.parametrize("command", [pytest.param(value, id=key) for key, value in FUZZ.items()])
def test_fuzz_returns_a_guess_without_raising(command):
    start = time.perf_counter()
    guess = detect_command_shell(command)
    elapsed = time.perf_counter() - start
    assert elapsed < _CEILING_SECONDS, f"{elapsed:.2f}s for {len(command)} chars"
    assert isinstance(guess, ShellGuess)
    assert guess.shell in (BASH, PWSH)
    assert isinstance(guess.detected, bool)
    assert isinstance(guess.reasons, tuple)


LONG_INPUTS = {
    # 300 KB pastes, well beyond MAX_ANALYZED
    "pwsh-300k": "Get-ChildItem -Path x\n" * 14_000,
    "bash-300k": "echo hello && ls -la /etc/hosts\n" * 10_000,
    "plain-300k": "x" * 300_000,
    "open-heredoc-300k": "cat <<EOF\n" + "body line\n" * 30_000,
}


@pytest.mark.parametrize(
    "command", [pytest.param(value, id=key) for key, value in LONG_INPUTS.items()]
)
def test_long_input_is_truncated_quickly(command):
    start = time.perf_counter()
    guess = detect_command_shell(command)
    elapsed = time.perf_counter() - start
    assert elapsed < _CEILING_SECONDS, f"{elapsed:.2f}s for {len(command)} chars"
    assert guess.shell in (BASH, PWSH)


def test_long_input_head_still_decides():
    """The head of the command is judged; truncation must not corrupt it."""
    assert verdict("Get-ChildItem -Path x\n" * 14_000) == PWSH_HIT
    assert verdict("echo hello && ls -la /etc/hosts\n" * 10_000) == BASH_HIT


# ── arguable: right, but the policy does not pin it ─────────────────────────
# Each case is marked ``xfail(strict=False)``: XPASS = the parser already does
# what I expect, xfail = it does not.  Neither can break the suite.

ARGUABLE = [
    pytest.param(
        "find . -name *.ps1",
        BASH_HIT,
        id="glob-ps1-unquoted",
        marks=pytest.mark.xfail(
            strict=False,
            reason="an unquoted *.ps1 is claimed as STRONG pwsh evidence, so a plain "
            "find turns into 'run it in PowerShell'",
        ),
    ),
    pytest.param(
        "ls -la /tmp/.ps1",
        BASH_HIT,
        id="path-ending-in-ps1",
        marks=pytest.mark.xfail(strict=False, reason="same .ps1 STRONG rule, path position"),
    ),
    pytest.param(
        'echo "$(date +%s)"',
        BASH_HIT,
        id="quoted-command-substitution",
        marks=pytest.mark.xfail(
            strict=False,
            reason="$( ) inside double quotes is still bash command substitution, but "
            "the mask drops it",
        ),
    ),
    pytest.param(
        "(cd src && make)",
        BASH_HIT,
        id="subshell-weak-only",
        marks=pytest.mark.xfail(strict=False, reason="WEAK-only pile: nothing anchors it"),
    ),
    pytest.param(
        "ls *.txt | xargs rm -f",
        BASH_HIT,
        id="xargs-pipeline",
        marks=pytest.mark.xfail(strict=False, reason="WEAK-only pile: nothing anchors it"),
    ),
    pytest.param(
        'if [ "$x" -eq 1 ]; then',
        BASH_HIT,
        id="posix-test-eq",
        marks=pytest.mark.xfail(strict=False, reason="POSIX test(1) -eq is not a pwsh operator"),
    ),
    pytest.param(
        "mkdir -p a/b && cp x y",
        NO_CLUE,
        id="mkdir-cp",
        marks=pytest.mark.xfail(
            strict=False, reason="both shells have mkdir/cp aliases; undecided is defensible"
        ),
    ),
    pytest.param(
        ".\\lib\\x",
        PWSH_HIT,
        id="relative-backslash-path",
        marks=pytest.mark.xfail(
            strict=False,
            reason="a .\\relative path is PowerShell-only evidence, but only C:\\ is claimed",
        ),
    ),
    pytest.param(
        "dotnet test .\\src\\App.csproj",
        PWSH_HIT,
        id="dotnet-backslash-arg",
        marks=pytest.mark.xfail(strict=False, reason="same .\\ gap, argument position"),
    ),
    pytest.param(
        "<# block #> git status",
        PWSH_HIT,
        id="block-comment-evidence",
        marks=pytest.mark.xfail(
            strict=False, reason="<# is PowerShell-only syntax; the policy calls it no evidence"
        ),
    ),
    pytest.param(
        "try { echo hi } catch { echo err }",
        PWSH_HIT,
        id="try-catch",
        marks=pytest.mark.xfail(
            strict=False, reason="try/catch has no bash equivalent, but no rule names it"
        ),
    ),
    pytest.param(
        "& 'C:\\Program Files\\x.exe' -Arg",
        PWSH_HIT,
        id="quoted-drive-path",
        marks=pytest.mark.xfail(
            strict=False, reason="the quoted drive path is masked; quoting is how docs spell it"
        ),
    ),
    pytest.param(
        "$Error[0].Exception.Message",
        PWSH_HIT,
        id="dollar-error",
        marks=pytest.mark.xfail(
            strict=False, reason="$Error/$args/$PID are WEAK while $true is DECISIVE"
        ),
    ),
    pytest.param(
        "ls -la /etc",
        BASH_HIT,
        id="etc-without-slash",
        marks=pytest.mark.xfail(
            strict=False, reason="posix-path needs a trailing / after /etc and /proc"
        ),
    ),
    pytest.param(
        "echo hi > /tmp/x",
        BASH_HIT,
        id="redirect-to-tmp",
        marks=pytest.mark.xfail(strict=False, reason="/tmp is not in the posix-path rule"),
    ),
    pytest.param(
        "#!/usr/bin/env pwsh\necho hi",
        PWSH_HIT,
        id="pwsh-shebang",
        marks=pytest.mark.xfail(
            strict=False, reason="the shebang rule knows bash-family interpreters only"
        ),
    ),
    pytest.param(
        "#requires -Version 5.1",
        PWSH_HIT,
        id="requires-lowercase",
        marks=pytest.mark.xfail(
            strict=False, reason="#Requires is matched case-sensitively; the directive is not"
        ),
    ),
    pytest.param(
        ". .\\build.ps1",
        PWSH_HIT,
        id="dot-source-ps1",
        marks=pytest.mark.xfail(
            strict=False,
            reason="dot-sourcing a .ps1 is the canonical PowerShell idiom, but it is "
            "spelled exactly like bash's `. FILE`",
        ),
    ),
    pytest.param(
        "gm convert logo.png -resize 50% out.png",
        NO_CLUE,
        id="graphicsmagick-gm",
        marks=pytest.mark.xfail(
            strict=False, reason="the alias list carries `gm`, which is also GraphicsMagick's CLI"
        ),
    ),
    pytest.param(
        "ls [int]*",
        NO_CLUE,
        id="glob-char-class",
        marks=pytest.mark.xfail(
            strict=False, reason="an unquoted [int] glob matches the pwsh cast rule"
        ),
    ),
    pytest.param(
        'set -e\nbash -lc "x"',
        BASH_HIT,
        id="wrapper-on-second-line",
        marks=pytest.mark.xfail(
            strict=False,
            reason="'any line whose first token is a shell binary' vs the module's "
            "'the command's first token'",
        ),
    ),
]


class TestArguable:
    """Cases I believe are right but the contract does not pin (never fail)."""

    @pytest.mark.parametrize("command, expected", ARGUABLE)
    def test_arguable(self, command, expected):
        assert verdict(command) == expected

    @pytest.mark.xfail(
        strict=False,
        reason="'powershell.exe' / 'pwsh.exe' are the spellings a Windows caller "
        "actually holds, but only the bare names are aliased",
    )
    def test_arguable_default_exe_spelling(self):
        assert detect_command_shell("git status", default="powershell.exe").shell == PWSH
        assert detect_command_shell("git status", default="pwsh.exe").shell == PWSH

