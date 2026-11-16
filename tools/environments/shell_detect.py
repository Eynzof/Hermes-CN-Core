"""Decide whether a terminal command line was written for bash or for PowerShell.

The ``terminal`` tool takes a per-call ``shell`` argument.  Its default is ``auto``:
on a host that can run *both* shells (Windows with Git for Windows installed) the
tool has to choose one per command.  This module is that choice, and it is a
heuristic over text, not a shell engine.  A wrong answer runs the command in the
wrong shell, so every ambiguous shape is biased toward *no decision*
(``detected=False``): the caller then keeps its own configured default.  Precision
matters far more than recall here — a confident wrong answer is the bug this module
exists to prevent, not a feature it is meant to chase.

Two phases:

1. **Mask** (:func:`_scan`) — one left-to-right scan replaces quoted regions,
   comments, here-document bodies and backtick spans with spaces, keeping the
   length and every newline so any offset stays valid.  The scan is also the only
   place that can *see* those constructs, so it records them there: a here-document
   operator, a ``<#`` block comment, a ``#Requires`` directive, a backtick or
   backslash line continuation, a drive-letter path.  Each becomes evidence.
2. **Score** (:func:`_tally`) — declarative rule tables (:data:`_BASH_RULES`,
   :data:`_PWSH_RULES`) run over the masked text.  A rule weighs :data:`DECISIVE`
   (a lone hit decides that side), :data:`STRONG` (outranks any WEAK pile) or
   :data:`WEAK`; WEAK is capped per side, so the three weights *order* the tiers
   instead of summing them away.  A decision also needs one DECISIVE/STRONG hit
   *and* a strictly higher score, so a tie is never resolved by guessing.

A command whose first token is itself a shell invocation (``pwsh -c "…"``,
``cmd /c dir``) is never decided either: both shells run those, and the wrapped text
is quoted, hence masked, so its body is not evidence about the caller's shell.

Only the first :data:`MAX_ANALYZED` characters are examined and every pattern is
linear in the input (no nested quantifier over the same span), so a huge or hostile
command costs a bounded amount of time.  Nothing here does I/O, mutates module
state, or imports outside ``re``/``dataclasses``/``typing`` — the module is imported
while the ``terminal`` tool registers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Pattern, Sequence

SHELL_BASH = "bash"
SHELL_PWSH = "pwsh"

#: A lone hit on a DECISIVE rule settles that side on its own, even against a STRONG
#: hit that also collected a full WEAK pile (8 > 4 + 3).
DECISIVE = 8
#: Strong evidence: needs no partner, but loses to any DECISIVE hit.
STRONG = 4
#: A hint that also holds under the other shell; cannot decide anything by itself.
WEAK = 1

#: Prefix length the analysis is capped at, in characters.  A command longer than
#: this is judged on its head only: the alternative is unbounded work inside a tool
#: call, and command lines are never this long in practice (a 200 KB paste is
#: already pathological).
MAX_ANALYZED = 200_000

#: Most evidence labels a :class:`ShellGuess` carries.
_MAX_REASONS = 5

#: Ceiling on what the WEAK tier may contribute per side, so "many weak hints"
#: (five POSIX utility names in one pipeline) cannot outvote one STRONG signal.
#: Kept below :data:`STRONG` on purpose: the tiers only mean something if one good
#: signal outranks any number of hints.
_WEAK_CAP = 3

__all__ = [
    "DECISIVE",
    "MAX_ANALYZED",
    "SHELL_BASH",
    "SHELL_PWSH",
    "STRONG",
    "WEAK",
    "ShellGuess",
    "detect_command_shell",
]


@dataclass(frozen=True)
class ShellGuess:
    """Which shell a command line looks written for, and how sure that is.

    ``detected`` is ``True`` only when positive evidence decided it.  ``False``
    means "no decision": ``shell`` then simply repeats the caller's default, so a
    consumer must treat the two fields together — reading ``shell`` alone would turn
    a fallback into a confident answer.

    ``reasons`` carries short evidence labels, strongest first, at most
    :data:`_MAX_REASONS`, and only for a detected guess.  ``bash_score`` and
    ``pwsh_score`` are the weighted totals the decision came from, for logging.
    """

    shell: str
    detected: bool
    reasons: tuple[str, ...] = ()
    bash_score: int = 0
    pwsh_score: int = 0


# ── rule model ───────────────────────────────────────────────────────────────

#: Which text a regex rule reads: the masked code, or the untouched input.
_MASK = "mask"
_RAW = "raw"

#: A regex rule matches *something*; a predicate rule answers with a label detail
#: (the cmdlet name, say) or ``None`` for "no evidence".  Rules that only the mask
#: scan can see are predicates over the scan result.
_Matcher = Pattern[str] | Callable[["_Scan"], Optional[str]]


@dataclass(frozen=True)
class _Rule:
    """One piece of evidence: what it is worth, how it reads, what it looks for."""

    weight: int
    #: ``"bash:here-doc"``, or a ``"pwsh:cmdlet:{0}"`` template filled with the
    #: matcher's detail so the label names the token that fired.
    reason: str
    matcher: _Matcher
    #: Ignored by predicate matchers, which own their own view of the scan.
    scope: str = _MASK


@dataclass
class _Tally:
    """Mutable running total for one side (internal; never leaves this module)."""

    score: int = 0
    #: True once any DECISIVE or STRONG rule fired: without it nothing is decided.
    anchored: bool = False
    labels: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class _Scan:
    """What one masking pass saw: the code text plus the constructs it hid.

    ``masked`` has the same length as the analyzed input and the same newlines, so
    a regex hit in it can be quoted back with its offsets.  Written by :func:`_scan`
    and read-only afterwards; the flags are accumulated while the mask is built.
    """

    raw: str
    masked: str = ""
    heredocs: int = 0
    here_strings: int = 0
    block_comment: bool = False
    requires: bool = False
    backtick_continuation: bool = False
    backslash_continuation: bool = False
    drive_path: bool = False


# ── phase 1: the mask ────────────────────────────────────────────────────────

#: Characters that may open something the mask hides.  Every other character is
#: ordinary code text and is left exactly as it is.
_SPECIALS = frozenset("\\\"'`#<")

#: A ``#`` opens a comment only at the start of a word.
_WORD_BREAKS = frozenset(" \t\n;|&()<>{}")

#: A here-document delimiter: a shell word without quoting.
_DELIMITER_RE = re.compile(r"[\w.@-]+")


def _line_end(text: str, start: int) -> int:
    """Index of the newline that ends the line starting at *start* (or ``len``)."""
    found = text.find("\n", start)
    return len(text) if found < 0 else found


def _blank(chars: list[str], start: int, end: int) -> None:
    """Overwrite ``chars[start:end]`` with spaces, keeping every newline."""
    for index in range(max(start, 0), min(end, len(chars))):
        if chars[index] != "\n":
            chars[index] = " "


def _quoted_end(text: str, start: int, ansi: bool = False) -> int:
    """Index just past the quote that opens at *start* (``len`` when unterminated).

    Single quotes are literal in both shells; inside double quotes a backslash
    escapes the next character for bash.  bash's ANSI-C form (``$'…'``, and the
    localised ``$"…"``, which is why *ansi* covers both quote characters) honours
    the same escape inside single quotes too, so ``$'it\\'s'`` is one string rather
    than a string that ends at the escape and leaves the rest of the line readable.
    An unterminated quote masks the rest of the input: the alternative is guessing
    where the string was meant to end.
    """
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\" and (ansi or quote == '"'):
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return len(text)


def _block_comment_end(text: str, start: int) -> int:
    """Index just past the ``#>`` closing a ``<#`` comment (or ``len``)."""
    found = text.find("#>", start + 2)
    return len(text) if found < 0 else found + 2


def _backtick_continues(text: str, start: int) -> bool:
    """True when the backtick at *start* is PowerShell's line continuation."""
    index = start + 1
    while index < len(text) and text[index] in " \t":
        index += 1
    return text.startswith("\n", index)


def _backtick_end(text: str, start: int) -> int:
    """Index just past a same-line closing backtick, else just past the backtick.

    bash reads ``…`` ``…`` as command substitution, so the span is opaque here;
    a stray backtick (the PowerShell escape character, usually) is masked alone.
    """
    close = text.find("`", start + 1, _line_end(text, start))
    return start + 1 if close < 0 else close + 1


def _heredoc_end(text: str, start: int) -> int:
    """Index just past the here-document opened by the ``<<`` at *start*.

    The mask starts at the operator itself: the scanner already recorded the
    here-document as evidence, so nothing inside it needs to stay readable.  A
    delimiter that cannot be parsed masks the rest of the input — masking too much
    only costs evidence, masking too little can invent it.
    """
    index = start + 2
    if text.startswith("-", index):  # `<<-` allows indented bodies
        index += 1
    while index < len(text) and text[index] in " \t":
        index += 1
    if index < len(text) and text[index] in "'\"":
        quote = text[index]
        close = text.find(quote, index + 1)
        if close < 0:
            return len(text)
        delimiter = text[index + 1 : close]
        index = close + 1
    else:
        word = _DELIMITER_RE.match(text, index)
        if word is None:
            return len(text)
        delimiter = word.group(0)
        index = word.end()
    if not delimiter:
        return len(text)
    line_start = _line_end(text, index)
    while line_start < len(text):
        body_start = line_start + 1
        body_end = _line_end(text, body_start)
        # Lenient about indentation and a trailing CR: terminating early unmasks
        # text, terminating late only loses evidence.
        if text[body_start:body_end].strip(" \t\r") == delimiter:
            return body_end
        line_start = body_end
    return len(text)


def _is_drive_path(text: str, index: int) -> bool:
    """True for a ``C:\\…`` drive path, which bash would read as escapes."""
    if index and (text[index - 1].isalnum() or text[index - 1] in "_:"):
        return False
    return (
        text[index].isascii()
        and text[index].isalpha()
        and text.startswith(":\\", index + 1)
    )


def _span(text: str, index: int, state: _Scan) -> int:
    """End of the region the special character at *index* opens (``index`` = none).

    Returning ``index`` means "ordinary code character": nothing is masked.
    """
    char = text[index]
    if char == "\\":
        if text.startswith("\n", index + 1):
            state.backslash_continuation = True
            return index + 1  # keep the newline so line structure survives
        return index + 2
    if char in "\"'":
        return _quoted_end(text, index)
    if char == "`":
        if _backtick_continues(text, index):
            state.backtick_continuation = True
            return index + 1
        return _backtick_end(text, index)
    if char == "#":
        if index and text[index - 1] not in _WORD_BREAKS:
            return index  # `a#b` is a word, not a comment
        if text.startswith("Requires", index + 1):
            state.requires = True
        return _line_end(text, index)
    if text.startswith("<#", index):
        state.block_comment = True
        return _block_comment_end(text, index)
    if text.startswith("<<<", index):
        state.here_strings += 1
        return index + 3  # the here-string operator only: its word is ordinary text
    if text.startswith("<<", index):
        state.heredocs += 1
        return _heredoc_end(text, index)
    return index


def _scan(text: str) -> _Scan:
    """Mask *text* (phase 1) and report the constructs the mask hid."""
    chars = list(text)
    scan = _Scan(raw=text)
    index = 0
    while index < len(text):
        if text[index] in "\"'" and index and text[index - 1] == "$":
            # bash's `$'…'` / `$"…"`: the `$` is part of the quoting, so the span
            # starts there, and both quote characters honour backslash escapes.
            end = _quoted_end(text, index, ansi=True)
            _blank(chars, index - 1, end)
            index = max(end, index + 1)
            continue
        if text[index] in _SPECIALS:
            end = _span(text, index, scan)
            _blank(chars, index, end)
            index = max(end, index + 1)
            continue
        if _is_drive_path(text, index):
            scan.drive_path = True
        index += 1
    scan.masked = "".join(chars)
    return scan


# ── phase 2: the rule tables ─────────────────────────────────────────────────

#: A separator that puts what follows in command position: start of text, a
#: statement/pipe separator, or an opening bracket.  The run after it is `[ \t]`
#: and never `\s`, because a newline is *itself* a separator: `\s*` would let two
#: spellings of the same position overlap and backtrack quadratically on input
#: that is nothing but newlines.  `{` counts only when it is not `@{`: the inside
#: of a PowerShell hashtable is not a command position.
_CMD_START = r"(?:\A|[;\n|&()}]|(?<!@)\{)[ \t]*"

#: Command position for PowerShell, minus ``$(`` — bash's command substitution
#: holds bash code, so a ``Foo-Bar`` inside one is not a cmdlet.
_CMD_START_NO_SUBST = r"(?:\A|[;\n|&{}]|(?<!\$)\()[ \t]*"

#: bash's approved shebang interpreters.  A non-shell shebang (python, node) says
#: nothing about the *caller's* shell, so it is not evidence either way.
_SHELL_INTERPRETERS = "bash|sh|dash|zsh|ksh|mksh|fish"

_BASH_ADMIN_TOOLS = (
    "sudo|chmod|chown|chgrp|mount|umount|crontab|systemctl|service|useradd|usermod"
    "|passwd|apt|apt-get|yum|dnf|apk|pacman|brew|nohup|export|unset"
)

_BASH_UTILITY_TOOLS = (
    "grep|sed|awk|find|xargs|tar|gzip|gunzip|curl|wget|make|cmake|pip3|pip|python3"
    "|node|npm|npx|git|docker|kubectl|helm|rsync|scp|ssh|tee|wc|head|tail|sort|uniq"
    "|cut|tr|du|df|ps|kill|top|env|which|whoami|pwd|ln|mkdir|rmdir|rm|cp|mv|touch"
    "|cat|echo|printf|ls|cd"
)

#: PowerShell approved verbs.  Only the verb is matched case-insensitively; the
#: noun must be Capitalised, which is what keeps `grep -e Foo-Bar` out.
_PWSH_VERBS = frozenset(
    """
    get set new remove add clear copy move test write read start stop restart invoke
    select where foreach sort group measure out export import update install
    uninstall register unregister enable disable rename resolve split join convertto
    convertfrom format compare find wait push pop show hide open close enter exit
    receive send suspend resume redo undo backup restore checkpoint debug optimize
    repair reset grant revoke publish unpublish save sync mount dismount lock unlock
    protect unprotect block unblock connect disconnect deny approve assert complete
    confirm edit extend initialize limit merge search skip step switch use watch
    """.split()
)

#: PowerShell aliases that no POSIX or cmd.exe command shares.  `ls cat cp mv rm
#: dir echo del type copy move start sort where tee` are deliberately absent: they
#: are POSIX/cmd names too, so they carry no information about the shell.
_PWSH_ALIASES = (
    "gci|gc|gps|gsv|gcm|gm|gwmi|gcb|sl|ni|ri|clc|iex|iwr|irm|ii|saps|sajb|oh|ft"
    "|fl|fw|select|foreach|measure"
)

#: Long parameter names a cmdlet accepts.  Capitalised and multi-letter, so they
#: cannot collide with bash's `-rf`-style bundled short flags.
_PWSH_PARAMETERS = (
    "Path|LiteralPath|Destination|Recurse|Force|Filter|Include|Exclude|ErrorAction"
    "|WarningAction|InformationAction|Encoding|ItemType|Value|Name|First|Last|Skip"
    "|ExpandProperty|Property|AsJob|ThrottleLimit|Credential|ComputerName|Verbose"
    "|Debug|WhatIf|Confirm|Depth|Stream|Raw|TotalCount|Tail|Head|Wait|PassThru"
    "|NoClobber|Container|ReadCount|Delimiter|Pattern|SimpleMatch|CaseSensitive"
    "|Quiet|Online|Proxy|Method|Body|Headers|Uri|UseBasicParsing|Format|InputObject"
    "|MemberType|NewName|TypeName|Append|Nonewline|Separator"
)

#: The PowerShell types a command casts to.  A closed list on purpose: `[abc]` in a
#: bash glob is not a cast.
_PWSH_TYPE_NAMES = (
    "int|string|bool|double|long|float|decimal|byte|char|datetime|timespan|regex"
    "|array|hashtable|ordered|pscustomobject|pscredential|switch|xml|void|object"
    "|type|guid|version|uri|ipaddress|scriptblock|datetimeoffset"
)

#: Comparison/logical operators.  Longest first, so `-isnot` wins over `-is` even
#: before the trailing `(?![\w-])` gets a say.  On their own these are not
#: evidence: `grep -in`, `find -not` and `awk -f` are real bash flags with the same
#: spelling, so :func:`_pwsh_operator` only trusts an operator that sits next to a
#: PowerShell value.
_PWSH_OPERATORS = (
    "notlike|notmatch|notcontains|contains|replace|split|join|isnot|like|match"
    "|notin|band|bxor|shl|shr|bor|xor|eq|ne|lt|gt|le|ge|in|is|as|or|and|not|f"
)

#: The automatic variables; PowerShell only, apart from `$_` (bash's "last
#: argument"), which the spec deliberately counts because unquoted `$_` in a bash
#: command is rare enough to be a typo.
_RE_PWSH_AUTOMATIC = re.compile(
    r"\$(?:true|false|null|_|PSItem|PSVersionTable|PSScriptRoot|LASTEXITCODE"
    r"|ErrorActionPreference|StackTrace|Matches|PSHOME|MyInvocation|ExecutionContext"
    r"|Host)(?!\w)",
    re.IGNORECASE,
)

_RE_PWSH_OPERATOR = re.compile(
    r"(?<![\w-])-(?P<detail>" + _PWSH_OPERATORS + r")(?![\w-])"
)

#: The head of a `source FILE` / `. FILE` line; :func:`_source_operand` reads the word.
_RE_BASH_SOURCE = re.compile(_CMD_START + r"(?P<head>source|\.)[ \t]+")

#: A `.ps1` mention.  Its *token* decides what it is: `build.ps1` names a file,
#: `*.ps1` is the glob pattern a POSIX command line writes (`find . -name *.ps1`).
_RE_PS1 = re.compile(r"\.ps1\b", re.IGNORECASE)

#: Characters that end a shell word, for the bounded token walks below.
_WORD_ENDS = frozenset(" \t\n;|&(){}<>")

#: How far back a token walk looks.  Nothing a shell runs is a single word this
#: long, and the cap is what keeps a walk over a hostile 200 KB "word" linear.
_TOKEN_LOOKBACK = 256

#: What separates one command from the next: a token right after one of these
#: starts in command position, where a program (not an argument) sits.
_SEPARATORS = frozenset(";\n|&(){}")

#: A `Verb-Noun` token in command position.  `$(` counts here (unlike the other
#: PowerShell rules) because an approved-verb cmdlet inside a subexpression is
#: still PowerShell; :func:`_pwsh_cmdlet` decides the looser shape separately.
_RE_PWSH_CMDLET = re.compile(_CMD_START + r"(?P<detail>[A-Za-z]+-[A-Za-z0-9]+)")

#: A shell-invocation as the *first* token: the rest of the line is that shell's
#: argv, which both shells can run, and which quoting makes unreadable anyway.
_RE_SHELL_WRAPPER = re.compile(
    r"\A\s*(?:pwsh|powershell|cmd|bash|sh|dash|zsh|ksh|mksh|fish|wsl)(?:\.exe)?"
    r"(?![\w.-])",
    re.IGNORECASE,
)


def _bash_shebang(scan: _Scan) -> Optional[str]:
    """A bash-family shebang on the first line (a comment to the mask, hence raw)."""
    first = scan.raw.split("\n", 1)[0].strip()
    if not first.startswith("#!"):
        return None
    match = re.match(
        r"#![ \t]*(?:\S*/)*(?:env[ \t]+(?:-S[ \t]+)?)?(?P<detail>"
        + _SHELL_INTERPRETERS
        + r")(?![\w-])",
        first,
        re.IGNORECASE,
    )
    return None if match is None else match.group("detail")


def _flag(attribute: str) -> Callable[[_Scan], Optional[str]]:
    """Matcher for evidence only the mask scan can see (``heredocs`` and friends)."""

    def matcher(scan: _Scan) -> Optional[str]:
        return "" if getattr(scan, attribute) else None

    return matcher


def _pwsh_cmdlet(scan: _Scan) -> Optional[str]:
    """A ``Verb-Noun`` token in command position, with a Capitalised noun.

    Two accepted shapes: a PowerShell approved verb (any case) plus a Capitalised
    noun — `Get-ChildItem`, `get-ChildItem` — and any `[A-Z][a-z]+-[A-Z]…` token,
    which is how a third-party module's `Foo-Bar` looks.  Everything else (a
    lowercase `apt-get`, an `ls -la` flag, a `Foo-Bar` inside an argument) is not
    evidence.

    Only the *approved-verb* shape is read inside bash's `$( … )`, because
    `echo $(Get-Date)` is a PowerShell subexpression while `echo $(Foo-Bar)` runs a
    command whose name bash will accept literally.
    """
    for match in _RE_PWSH_CMDLET.finditer(scan.masked):
        verb, _sep, noun = match.group("detail").partition("-")
        if not noun[:1].isupper():
            continue
        if verb.lower() in _PWSH_VERBS:
            return match.group("detail")
        if re.fullmatch(r"[A-Z][a-z]+", verb) and not _in_substitution(match, scan):
            return match.group("detail")
    return None


def _in_substitution(match: re.Match[str], scan: _Scan) -> bool:
    """True when the match sits directly inside bash's ``$(`` … ``)``."""
    start = match.start("detail")
    return start >= 2 and scan.masked[start - 2 : start] == "$("


def _bash_continuation(scan: _Scan) -> Optional[str]:
    """A trailing backslash-newline: bash's continuation, never PowerShell's."""
    return "" if scan.backslash_continuation else None


def _bash_trap(scan: _Scan) -> Optional[str]:
    """`trap … EXIT` — read to the end of the line, not a fixed word window.

    The condition is usually quoted, so the mask leaves nothing but blanks between
    `trap` and `EXIT`; a line-wide scan finds it while staying linear (each line is
    matched at most once, because the pattern runs to the newline).
    """
    for match in re.finditer(_CMD_START + r"trap[ \t][^\n]*", scan.masked):
        if re.search(r"\bEXIT\b", match.group(0)):
            return "EXIT"
    return None


def _word_start(text: str, end: int) -> Optional[int]:
    """Start of the shell word ending at *end*, or ``None`` past the walk cap."""
    start = end
    while start and text[start - 1] not in _WORD_ENDS:
        if end - start >= _TOKEN_LOOKBACK:
            return None
        start -= 1
    return start


def _command_position(scan: _Scan, start: int) -> bool:
    """True when a token starting at *start* sits where a command name would."""
    index = start - 1
    while index >= 0 and scan.masked[index] in " \t":
        index -= 1
    return index < 0 or scan.masked[index] in _SEPARATORS


def _bash_source(scan: _Scan) -> Optional[str]:
    """`source FILE` / `. FILE`, but not `..\\path`, `.\\build.ps1` or `x.ps1`.

    The mask blanks an escape into a blank, so `.\\script.ps1` would read as
    `. script.ps1`.  Reading the operand out of the raw text separates a real blank
    (bash's `.`) from a Windows path separator (PowerShell's `.\\`), and a `.ps1`
    operand is PowerShell's dot-source — or a script bash was never handed — so
    either spelling is evidence *against* bash, not for it.
    """
    for match in _RE_BASH_SOURCE.finditer(scan.masked):
        if scan.raw[match.end() - 1] not in " \t":
            continue
        operand = _source_operand(scan, match.end())
        if not operand or operand.startswith(".\\") or operand.lower().endswith(".ps1"):
            continue
        return match.group("head")
    return None


def _source_operand(scan: _Scan, start: int) -> str:
    """The word a `.`/`source` head at *start* would read, taken from the raw text."""
    text = scan.raw
    index = start
    while index < len(text) and text[index] in " \t":
        index += 1
    end = index
    while end < len(text) and text[end] not in _WORD_ENDS:
        end += 1
    return text[index:end]


def _ps1_token(scan: _Scan, match: re.Match[str]) -> Optional[tuple[int, str]]:
    """``(start, token)`` for a `.ps1` match — ``None`` when it is a glob pattern.

    A pattern is not a script: `find . -name *.ps1` and `grep --include=*.ps1` name
    a *shape* of file, which is how a POSIX command line talks about `.ps1` files.
    The walk is over the raw text so a masked `\\` cannot split the token.
    """
    start = _word_start(scan.raw, match.start())
    if start is None:
        return None
    head = scan.masked[start : match.start()]
    if "*" in head or "?" in head:
        return None
    return start, scan.raw[start : match.end()]


def _pwsh_script_file(scan: _Scan) -> Optional[str]:
    """A `.ps1` file named as a file, anywhere on the line."""
    for match in _RE_PS1.finditer(scan.masked):
        found = _ps1_token(scan, match)
        if found is not None:
            return found[1]
    return None


def _pwsh_script_command(scan: _Scan) -> Optional[str]:
    """A `.ps1` invoked at command position: `./x.ps1`, `.\\build.ps1`, `. .\\x.ps1`.

    Running a `.ps1` is PowerShell's job — bash cannot execute one — so where a
    command name would sit the suffix decides alone.  As an argument it stays a
    STRONG hint only: `find . -name x.ps1` is a POSIX line naming such a file.
    """
    for match in _RE_PS1.finditer(scan.masked):
        found = _ps1_token(scan, match)
        if found is not None and _command_position(scan, found[0]):
            return found[1]
    return None


def _bash_set_option(scan: _Scan) -> Optional[str]:
    """`set -e` / `set -x` / `set -o pipefail` — no PowerShell equivalent.

    Matched loosely (`set -<letters>`) and judged afterwards: a bundled `-euo`
    must count, but `[-A-Za-z]*[ex][-A-Za-z]*` in one pattern would backtrack
    quadratically over a long dash-word.
    """
    match = re.search(_CMD_START + r"set[ \t]+(?P<detail>-[A-Za-z]*)", scan.masked)
    if match is None:
        return None
    flags = match.group("detail")
    return flags if any(letter in flags for letter in "exo") else None


def _value_before(text: str, start: int) -> bool:
    """True when the token just before *start* names a PowerShell value.

    This guard is what keeps the shared spellings out: `if [ "$x" -eq 1 ]` (POSIX
    `test`, variable masked away) and `grep -in foo` (a command name, not a value)
    both leave the operator silent, while `$x -in $list` decides.
    """
    index = start - 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    if index < 0:
        return False
    if text[index] in ")]}":
        return True
    end = index + 1
    while index >= 0 and text[index] not in " \t\n;|&()[]{}" and end - index <= 64:
        index -= 1
    return "$" in text[index + 1 : end]


def _pwsh_operator(scan: _Scan) -> Optional[str]:
    """A comparison/logical operator sitting next to a PowerShell value."""
    for match in _RE_PWSH_OPERATOR.finditer(scan.masked):
        if _value_before(scan.masked, match.start()):
            return match.group("detail")
    return None


def _rx(weight: int, reason: str, pattern: str, flags: int = 0) -> _Rule:
    """A regex rule; the pattern is compiled once, while the table is built."""
    return _Rule(weight, reason, re.compile(pattern, flags))


#: Rule tables run in order; labels are reported strongest-first, then in this
#: order, so the most specific evidence reads first.
_BASH_RULES: tuple[_Rule, ...] = (
    # Decisive: syntax that only bash accepts.
    _Rule(DECISIVE, "bash:shebang:{0}", _bash_shebang),
    _Rule(DECISIVE, "bash:here-doc", _flag("heredocs")),
    _Rule(DECISIVE, "bash:here-string", _flag("here_strings")),
    _rx(DECISIVE, "bash:param-expansion", r"\$\{"),
    _rx(DECISIVE, "bash:arith-expansion", r"\$\(\("),
    _rx(DECISIVE, "bash:compound-keyword",
        _CMD_START + r"(?:fi|done|esac|then|elif)\b"),
    # `for f in *.txt; do` / `select x in a b; do`.  `[^\n;]*` cannot match the `;`
    # that has to follow it, so the pattern has exactly one way to match a span and
    # cannot backtrack quadratically.
    _rx(DECISIVE, "bash:for-do", _CMD_START + r"(?:for|select)[ \t]+\S+[ \t]+in\b"
        r"[^\n;]*(?:;|\n)[ \t]*do\b"),
    _rx(DECISIVE, "bash:case-in", _CMD_START + r"(?:case|select)[ \t]+\S+[ \t]+in\b"),
    _rx(DECISIVE, "bash:while-do",
        _CMD_START + r"(?:while|until)[ \t]+\S[^\n;]*(?:;|\n)[ \t]*do\b"),
    # `[[ ` needs the blank to be valid bash, which is also what keeps a wall of `[`
    # from making the engine re-scan the rest of the input at every `[[`.
    _rx(DECISIVE, "bash:test-bracket", r"\[\[[ \t]"),
    _rx(DECISIVE, "bash:env-assignment", _CMD_START + r"[A-Za-z_][A-Za-z0-9_]*=(?!=)"),
    _rx(DECISIVE, "bash:declaration", _CMD_START + r"(?:export|readonly|local|declare)"
        r"[ \t]+[A-Za-z_]\w*[= ]"),
    _Rule(DECISIVE, "bash:source:{0}", _bash_source),
    _Rule(DECISIVE, "bash:set-option:{0}", _bash_set_option),
    _Rule(DECISIVE, "bash:trap-exit:{0}", _bash_trap),
    # A trailing `&` stays DECISIVE on purpose.  PowerShell 7 does background with it
    # too, but the Windows hosts this tool meets are usually on 5.1, where `cmd &` is
    # a parser error; and a line that ends in `&` and is *meant* for bash is far more
    # common than a pwsh-7 job line, which also usually names a cmdlet (`Start-Job`).
    _rx(DECISIVE, "bash:background", r"(?<![&|>])&[ \t]*(?:\n|\Z)"),
    _rx(DECISIVE, "bash:posix-path",
        r"(?<![\w.])(?:/dev/(?:null|stdout|stderr|fd)|/proc/|/etc/)"),
    # Strong: names and paths a POSIX host has and Windows does not.
    _rx(STRONG, "bash:admin-tool:{0}",
        _CMD_START + r"(?P<detail>" + _BASH_ADMIN_TOOLS + r")\b"),
    _rx(STRONG, "bash:absolute-path:{0}",
        _CMD_START + r"(?P<detail>(?:/|\.{1,2}/)[\w.$/-]+)"),
    _rx(STRONG, "bash:command-substitution", r"\$\((?!\()"),
    # `find . -name '*.py' -mtime -7`: PowerShell has no `find`, and the predicate is
    # what tells the POSIX tool from the bare utility name.  Bounded between `find`
    # and the predicate, because an unbounded lazy scan would rescan the line once
    # per `find` in a hostile input.
    _rx(STRONG, "bash:find-predicate:{0}",
        _CMD_START + r"find[ \t]+[^\n;|]{0,160}?(?<![\w-])-(?P<detail>name|iname|type"
        r"|exec|mtime|maxdepth|print0|newer)\b"),
    # Weak: shapes that also work in PowerShell, or that bash and PowerShell share.
    _rx(WEAK, "bash:variable:{0}", r"\$(?P<detail>PATH|USER|SHELL|UID|PWD|HOME)(?!\w)"),
    _rx(WEAK, "bash:special-parameter:{0}", r"\$(?P<detail>[1-9@*#?$!])(?!\w)"),
    _rx(WEAK, "bash:and-or", r"&&|\|\|"),
    _rx(WEAK, "bash:fd-redirect", r">>?\s*&\s*\d|&>\s*\S"),
    _rx(WEAK, "bash:redirect-path", r">>?\s*(?:/|\.{1,2}/|~)"),
    _rx(WEAK, "bash:pipe", r"(?<!\|)\|(?!\|)"),
    _rx(WEAK, "bash:utility:{0}",
        _CMD_START + r"(?P<detail>" + _BASH_UTILITY_TOOLS + r")\b"),
    _rx(WEAK, "bash:short-flags:{0}",
        r"(?<![\w-])-(?P<detail>[a-z]{2,}|[a-z]\d+)(?![\w-])"),
    _rx(WEAK, "bash:long-option:{0}", r"(?<![\w-])--(?P<detail>[a-z][a-z-]*)(?![\w-])"),
    _rx(WEAK, "bash:home-path", r"(?<![\w./~-])~(?:/|\s|$)"),
    _Rule(WEAK, "bash:line-continuation", _bash_continuation),
)

_PWSH_RULES: tuple[_Rule, ...] = (
    # Decisive: syntax only PowerShell accepts.
    _Rule(DECISIVE, "pwsh:cmdlet:{0}", _pwsh_cmdlet),
    _rx(DECISIVE, "pwsh:drive-variable:{0}", r"\$(?P<detail>env|global|script|local"
        r"|using|private|variable):", re.IGNORECASE),
    _rx(DECISIVE, "pwsh:variable-assignment", _CMD_START_NO_SUBST + r"\$[A-Za-z_]\w*"
        r"(?:\[[^\]\n]{0,64}\])?(?:\.[A-Za-z_]\w*)*[ \t]*[+\-*/%]?=(?!=)"),
    _rx(DECISIVE, "pwsh:static-member:{0}",
        r"\[[A-Za-z_][\w.]*(?:\[\])?\]::(?P<detail>[A-Za-z_]\w*)"),
    _rx(DECISIVE, "pwsh:cast:{0}",
        r"\[(?P<detail>" + _PWSH_TYPE_NAMES + r")\b[^\]\n]{0,32}\]"),
    _rx(DECISIVE, "pwsh:cmdlet-binding", r"\[CmdletBinding\b", re.IGNORECASE),
    _rx(DECISIVE, "pwsh:hashtable", r"@\{"),
    _rx(DECISIVE, "pwsh:array-literal", r"@\("),
    # A splat is a word of its own (`Get-ChildItem @params`), so the guards are "not
    # glued to a word" (`user@host`) and "not a scoped package" (`@angular/core` in
    # `npm install @angular/core` is data, like any quoted word).  Command position
    # cannot be required here: a real splat is always an *argument*, so the strict
    # position would only ever match `@params` as a line's first word, which is not
    # PowerShell either.
    _rx(DECISIVE, "pwsh:splat:{0}", r"(?<![\w$@])@(?P<detail>[A-Za-z_]\w*)(?![\w/])"),
    _rx(DECISIVE, "pwsh:param-block", _CMD_START_NO_SUBST + r"param[ \t]*\("),
    _rx(DECISIVE, "pwsh:script-block",
        _CMD_START_NO_SUBST + r"(?:begin|process|end)[ \t]*\{"),
    _Rule(DECISIVE, "pwsh:block-comment", _flag("block_comment")),
    _Rule(DECISIVE, "pwsh:requires", _flag("requires")),
    _Rule(DECISIVE, "pwsh:automatic-variable", _RE_PWSH_AUTOMATIC),
    _Rule(DECISIVE, "pwsh:operator:{0}", _pwsh_operator),
    _rx(DECISIVE, "pwsh:filter-block", r"\|[ \t]*[%?][ \t]*\{"),
    _Rule(DECISIVE, "pwsh:backtick-continuation", _flag("backtick_continuation")),
    # `try { … } catch { … }`: bash has no such statement and no `try` builtin, so it
    # would need a user function named `try` *and* a literal `catch {` argument (a
    # `try() { … }` definition does not match either: its `try` is not in command
    # position).  Bounded lookahead, so a wall of `try` cannot rescan the input.
    _rx(DECISIVE, "pwsh:try-catch",
        _CMD_START + r"try[ \t]*\{(?=[\s\S]{0,400}\b(?:catch|finally)[ \t]*\{)",
        re.IGNORECASE),
    _Rule(DECISIVE, "pwsh:script-command:{0}", _pwsh_script_command),
    # Strong: Windows-shaped arguments and PowerShell's own command names.
    _Rule(STRONG, "pwsh:windows-path", _flag("drive_path")),
    _Rule(STRONG, "pwsh:script-file", _pwsh_script_file),
    _rx(STRONG, "pwsh:module-cmdlet:{0}", _CMD_START_NO_SUBST
        + r"(?P<detail>(?:Import|Install|Update|Save|Get|Remove)-Module)\b"),
    _rx(STRONG, "pwsh:host-flag:{0}",
        r"(?<![\w-])-(?P<detail>NoProfile|NoLogo|NonInteractive|ExecutionPolicy"
        r"|EncodedCommand|WindowStyle)(?![\w-])"),
    _rx(STRONG, "pwsh:alias:{0}",
        _CMD_START_NO_SUBST + r"(?P<detail>" + _PWSH_ALIASES + r")\b"),
    # Weak: names that exist in both worlds, plus a parameter, which only qualifies the
    # command it is bound to: `ls -Recurse` is PowerShell (`ls` is Get-ChildItem's
    # alias) but `head -c 5 -Recurse` is a bash line with a PowerShell-looking switch,
    # and the spelling alone cannot tell the two apart.  The cmdlet/alias rules above
    # are what make a parameter line a *decision*.
    _rx(WEAK, "pwsh:parameter:{0}",
        r"(?<![\w-])-(?P<detail>" + _PWSH_PARAMETERS + r")(?![\w-])"),
    _rx(WEAK, "pwsh:automatic-weak:{0}", r"\$(?P<detail>args|input|Error|PID)(?!\w)"),
)

# ── phase 2: scoring ─────────────────────────────────────────────────────────


def _detail(rule: _Rule, found: re.Match[str]) -> str:
    """The token a regex rule wants in its label, if the pattern named one."""
    if "detail" in found.re.groupindex:
        return found.group("detail") or ""
    return ""


def _match(rule: _Rule, scan: _Scan) -> Optional[str]:
    """``None`` when the rule does not fire, else the label detail (often ``""``)."""
    if isinstance(rule.matcher, re.Pattern):
        text = scan.raw if rule.scope == _RAW else scan.masked
        found = rule.matcher.search(text)
        return None if found is None else _detail(rule, found)
    return rule.matcher(scan)


def _tally(rules: Sequence[_Rule], scan: _Scan) -> _Tally:
    """Weighted total for one side, plus every label that contributed to it."""
    tally = _Tally()
    weak_points = 0
    for rule in rules:
        detail = _match(rule, scan)
        if detail is None:
            continue
        tally.labels.append((rule.weight, rule.reason.format(detail)))
        if rule.weight == WEAK:
            weak_points += WEAK
            continue
        tally.score += rule.weight
        tally.anchored = True
    tally.score += min(weak_points, _WEAK_CAP)
    return tally


def _labels(tally: _Tally) -> tuple[str, ...]:
    """The strongest distinct labels, most specific first."""
    ordered: list[str] = []
    for _weight, label in sorted(tally.labels, key=lambda item: -item[0]):
        if label not in ordered:
            ordered.append(label)
            if len(ordered) == _MAX_REASONS:
                break
    return tuple(ordered)


#: Spellings a caller may pass as ``default``; anything else falls back to bash.
#: A Windows caller usually holds a binary name (``powershell.exe``) or a product
#: name with a version (``PowerShell 7``), so :func:`_coerce_default` folds the case,
#: the spaces and the ``.exe`` away before the lookup.
_DEFAULT_ALIASES = {
    "bash": SHELL_BASH,
    "git-bash": SHELL_BASH,
    "gitbash": SHELL_BASH,
    "sh": SHELL_BASH,
    "posix": SHELL_BASH,
    "pwsh": SHELL_PWSH,
    "pwsh7": SHELL_PWSH,
    "powershell": SHELL_PWSH,
    "powershell5": SHELL_PWSH,
    "powershell5.1": SHELL_PWSH,
    "powershell7": SHELL_PWSH,
    "windows-powershell": SHELL_PWSH,
    "windowspowershell": SHELL_PWSH,
    "ps": SHELL_PWSH,
    "ps5": SHELL_PWSH,
    "ps5.1": SHELL_PWSH,
    "ps7": SHELL_PWSH,
}


def _coerce_default(default: object) -> str:
    """Fold a caller's *default* onto ``bash``/``pwsh``; junk means bash.

    The fallback is what an undecided command runs under, so it must always be one
    of the two real modes: an unset or unrecognised value cannot be allowed to
    reach the tool as ``None`` or as a typo.  ``pwsh.exe`` and ``PowerShell 7`` are
    the same two facts spelled the way a caller actually spells them, so the
    suffix and the spaces are normalised rather than dropped into bash.
    """
    if not isinstance(default, str):
        return SHELL_BASH
    name = default.strip().lower().removesuffix(".exe")
    return _DEFAULT_ALIASES.get(name.replace(" ", ""), SHELL_BASH)


def _decide(scan: _Scan, fallback: str) -> ShellGuess:
    """Score both sides and turn the totals into a :class:`ShellGuess`."""
    if _RE_SHELL_WRAPPER.search(scan.masked):
        # `pwsh -c "…"` / `cmd /c dir` wrap a command inside an argv line; both
        # shells run those, and the wrapped text is quoted anyway.  No decision.
        return ShellGuess(fallback, False, ())
    bash = _tally(_BASH_RULES, scan)
    pwsh = _tally(_PWSH_RULES, scan)
    winner = SHELL_PWSH if pwsh.score > bash.score else SHELL_BASH
    best, other = (pwsh, bash) if winner == SHELL_PWSH else (bash, pwsh)
    # A decision needs positive evidence AND a clear win; a tie, or a win built
    # only from WEAK hints, is exactly the case where a wrong answer costs more
    # than "no answer".
    if not best.anchored or best.score <= other.score:
        return ShellGuess(fallback, False, (), bash.score, pwsh.score)
    return ShellGuess(winner, True, _labels(best), bash.score, pwsh.score)


def detect_command_shell(command: object, *, default: str = SHELL_BASH) -> ShellGuess:
    """Guess which shell *command* was written for (see the module docstring).

    Returns ``detected=False`` with ``shell=default`` whenever the text carries no
    positive evidence, ties, or cannot be analysed at all — never raises, whatever
    the caller passes (``None``, bytes, an int, a 1 MB blob, NUL bytes, unterminated
    quotes or here-documents).
    """
    try:
        fallback = _coerce_default(default)
        if not isinstance(command, str):
            return ShellGuess(fallback, False, ())
        text = command[:MAX_ANALYZED]
        if not text.strip():
            return ShellGuess(fallback, False, ())
        return _decide(_scan(text), fallback)
    except Exception:  # health: allow BLE001 -- a malformed command must never break a tool call
        return ShellGuess(SHELL_BASH, False, ())
