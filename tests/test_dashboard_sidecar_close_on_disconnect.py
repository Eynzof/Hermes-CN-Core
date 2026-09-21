from agent.re_compat import re
from pathlib import Path

CHAT_SIDEBAR = Path(__file__).resolve().parent.parent / "web/src/components/ChatSidebar.tsx"

# String literals in web/src are formatted by .prettierrc (singleQuote: true, semi: false),
# so the merged ChatSidebar.tsx — upstream's copy of the file won the merge — carries
# 'tool' / 'session.create' and no trailing semicolon on `return {...}`. These regexes
# therefore accept either quote style and don't anchor on a semicolon: the contract under
# test is the params the sidecar sends, not the formatter's output. The behavioural twin of
# this file is web/src/lib/chat-sidebar-session-params.test.ts (vitest, real import).
_SQ = "[\"']"


def _sidecar_params_body(source: str) -> str:
    """The object literal returned by ``sidecarSessionCreateParams`` (brace-balanced)."""
    helper = re.search(
        r"function\s+sidecarSessionCreateParams\([^)]*\)[^{]*\{\s*return\s*\{", source
    )
    assert helper, "sidecarSessionCreateParams helper not found"
    assert re.search(
        rf"{_SQ}session\.create{_SQ}\s*,\s*sidecarSessionCreateParams\(profile\)", source
    ), "sidecar session.create call does not use the guarded params helper"
    depth = 1
    for index in range(helper.end(), len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[helper.end():index]
    raise AssertionError("sidecarSessionCreateParams return literal is unbalanced")


def test_sidecar_session_create_requests_close_on_disconnect():
    """The sidecar must opt its session into close_on_disconnect so the gateway
    reaps the slash_worker on WS disconnect (the #21370/#21467 leak)."""
    source = CHAT_SIDEBAR.read_text(encoding="utf-8", errors="replace")
    assert re.search(r"close_on_disconnect:\s*true", _sidecar_params_body(source))


def test_sidecar_session_create_scopes_profile():
    """The sidecar must pass the dashboard's selected profile so model/credential
    info matches the PTY child under profile-scoped chat."""
    source = CHAT_SIDEBAR.read_text(encoding="utf-8", errors="replace")
    body = _sidecar_params_body(source)
    assert re.search(r"close_on_disconnect:\s*true", body)
    assert re.search(rf"source:\s*{_SQ}tool{_SQ}", body)
    assert re.search(r"\.\.\.\(profile\s*\?\s*\{\s*profile\s*\}\s*:\s*\{\}\)", body)
