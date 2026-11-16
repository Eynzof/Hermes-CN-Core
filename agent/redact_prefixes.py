"""Prefix matcher construction and credential token boundary guards."""

import re

def _last_char_class(pattern: str) -> "str | None":
    """Text of the last ``[...]`` class in ``pattern`` (escape-aware), or None.

    For the built-in shape ``<literal prefix><body class><quantifier>`` this is the
    pattern's body class.
    """
    last = None
    i = 0
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "[":
            j = i + 1
            if pattern[j:j + 1] == "^":
                j += 1
            if pattern[j:j + 1] == "]":  # a leading "]" is literal
                j += 1
            while j < len(pattern) and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            last = pattern[i:j + 1]
            i = j + 1
            continue
        i += 1
    return last


def _trailing_guard(pattern: str) -> str:
    """Lookahead ending ``pattern``: reject a following alphanumeric (word boundary), and ``_``/``-``
    only when the pattern's body class admits them. Never stricter than the shared guard, so a
    plugin pattern whose last class is a delimiter check (``(?=[\\s.,])``) still matches."""
    body_class = _last_char_class(pattern)
    try:  # a "[" inside a (?#...) comment is no class: fall back to the shared guard
        seps = "".join(c for c in "_-" if re.fullmatch(body_class, c)) if body_class else None
    except re.error:
        seps = None
    return r"(?![A-Za-z0-9_-])" if seps is None else f"(?![A-Za-z0-9{seps}])"


def _compile_prefix_matcher(patterns: list) -> re.Pattern[str]:
    # Each branch gets its own guard (_trailing_guard): a following alphanumeric always
    # blocks, ``_``/``-`` only when the body class admits them. The old shared
    # ``(?![A-Za-z0-9_-])`` failed ``ghp_…[A-Za-z0-9]{10,}`` at the body's end and at every
    # backtrack step, so a token followed by ``_``/``-`` was missed and shipped in cleartext
    # (#142852). A pattern without a ``[...]`` class falls back to the shared class.
    return re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(f"(?:{p}){_trailing_guard(p)}" for p in patterns) + r")")


def _compile_prefix_sub_matcher(patterns: list) -> re.Pattern[str]:
    """_PREFIX_RE for the masking pass, also admitting a key glued to an identifier by ``-``/``_``
    (``backup-sk-…``, ``x-ghp_…``), which _PREFIX_RE rejects so ``scikit-learn`` stays whole. One
    fixed-width lookbehind keeps main's single scan; _mask_prefix_match masks a glued match only
    when its body is opaque, so ``backup-sk-learn`` stays whole too (#147240)."""
    # Branches grouped by their literal first char (every pattern opens on one, see
    # register_redaction_patterns): admitting ``-``/``_`` before a key makes every separator a
    # candidate, and a flat 60-way alternation per candidate doubled the cost of a ``_ghp_`` flood.
    # Groups are disjoint by first char, so leftmost-first match order is unchanged.
    by_first: dict = {}
    for pattern in patterns:
        by_first.setdefault(pattern[0], []).append(f"(?:{pattern[1:]}){_trailing_guard(pattern)}")
    body = "|".join(f"{re.escape(c)}(?:{'|'.join(rest)})" for c, rest in by_first.items())
    return re.compile(r"(?<![A-Za-z0-9])(" + body + r")")
