from agent.re_compat import re
from pathlib import Path

CHAT_SIDEBAR = Path(__file__).resolve().parent.parent / "web/src/components/ChatSidebar.tsx"


def _sidecar_params_body(source: str) -> str:
    helper = re.search(
        r"function\s+sidecarSessionCreateParams\([^)]*\)[^{]*\{\s*return\s*\{(.*?)\};",
        source,
        re.DOTALL,
    )
    assert helper, "sidecarSessionCreateParams helper not found"
    assert re.search(
        r'"session\.create",\s*sidecarSessionCreateParams\(profile\)', source
    ), "sidecar session.create call does not use the guarded params helper"
    return helper.group(1)


def test_sidecar_session_create_requests_close_on_disconnect():
    """The sidecar must opt its session into close_on_disconnect so the gateway
    reaps the slash_worker on WS disconnect (the #21370/#21467 leak)."""
    source = CHAT_SIDEBAR.read_text(encoding="utf-8")
    assert re.search(r"close_on_disconnect:\s*true", _sidecar_params_body(source))


def test_sidecar_session_create_scopes_profile():
    """The sidecar must pass the dashboard's selected profile so model/credential
    info matches the PTY child under profile-scoped chat."""
    source = CHAT_SIDEBAR.read_text(encoding="utf-8")
    body = _sidecar_params_body(source)
    assert re.search(r"close_on_disconnect:\s*true", body)
    assert re.search(r'source:\s*"tool"', body)
    assert re.search(r"\.\.\.\(profile\s*\?\s*\{\s*profile\s*\}\s*:\s*\{\}\)", body)
