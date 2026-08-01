#!/usr/bin/env python3
"""
Reproduction tests for the subagent credential-parity gap: a delegated child
can be built with a DIFFERENT base_url or api_key than the main agent's LIVE
client, which makes the child's first LLM call fail with HTTP 401.

Git-history evidence
--------------------
* 25b734845 "fix(delegate): inherit subagent endpoint from parent active client"
  — When ``parent_agent.base_url`` is a stale leftover (e.g. an old OpenRouter
  URL) while the live OpenAI client already points elsewhere (e.g. a local
  gateway), subagents routed calls to the stale endpoint and failed with
  HTTP 401. The fix added ``_inherit_parent_base_url`` so the child inherits
  the LIVE URL from ``_client_kwargs`` / ``client.base_url``.
* a67ddf598 made that fallback actually fire with httpx.URL objects.
  The api_key side was NEVER given the same treatment. In
  ``_build_child_agent`` (tools/delegate_tool.py):

      parent_api_key = getattr(parent_agent, "api_key", None)
      if (not parent_api_key) and hasattr(parent_agent, "_client_kwargs"):
          parent_api_key = parent_agent._client_kwargs.get("api_key")
      ...
      effective_api_key = override_api_key or parent_api_key

  The surface ``parent_agent.api_key`` won whenever it was truthy — even when
  it was the SAME stale value the URL fix was built to ignore — and the live
  client's key (``client.api_key``) was never consulted at all. The result was
  the asymmetric pair the URL fix was designed to prevent: the child dialed
  the live endpoint (correct URL) with a stale/foreign key (wrong api_key) →
  HTTP 401.

  Fixed by ``_inherit_parent_api_key`` (mirror of ``_inherit_parent_base_url``
  on the credential side): the live ``client.api_key`` wins, then
  ``_client_kwargs["api_key"]``, with the surface key as verbatim fallback
  (callable token providers pass through untouched). This file is the
  regression suite for that fix.

Run with:  python -m pytest tests/tools/test_delegate_credential_parity.py -v
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.delegate_tool import _build_child_agent, _inherit_parent_api_key

# ---------------------------------------------------------------------------
# A parent whose SURFACE attributes (base_url / api_key) are stale leftovers
# from a previous provider, while its LIVE client state (_client_kwargs and
# the mounted OpenAI client) points at a different endpoint + key.
# This is the exact staleness class commit 25b734845 fixed for base_url.
# ---------------------------------------------------------------------------
LIVE_URL = "http://127.0.0.1:9/v1"          # live endpoint (child must dial this)
LIVE_KEY = "sk-live-gateway-key"            # live key (child must send this)
STALE_URL = "https://openrouter.ai/api/v1"  # leftover surface URL
STALE_KEY = "sk-or-v1-stale-parent-key"     # leftover surface key (truthy!)


def _make_stale_parent(*, client_api_key=LIVE_KEY, client_kwargs_api_key=LIVE_KEY):
    """Parent whose surface attrs are stale but whose live client is healthy."""
    parent = MagicMock()
    parent.base_url = STALE_URL
    parent.api_key = STALE_KEY
    parent.provider = "custom"
    parent.api_mode = "chat_completions"
    parent.model = "some-model"
    parent.platform = "cli"
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent.provider_require_parameters = False
    parent.provider_data_collection = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.enabled_toolsets = None
    parent.disabled_toolsets = []
    parent.max_tokens = None
    parent.reasoning_config = None
    parent.request_overrides = {}
    parent.prefill_messages = []
    parent.acp_command = None
    parent.acp_args = []
    parent.fallback_model = None
    parent._credential_pool = None
    parent.openrouter_min_coding_score = None
    parent._subagent_id = None
    parent.session_id = "sess-parent"
    parent._current_turn_id = ""
    # --- LIVE client state (this is what the parent is actually calling) ---
    parent._client_kwargs = {
        "api_key": client_kwargs_api_key,
        "base_url": LIVE_URL,
    }
    parent.client = SimpleNamespace(base_url=LIVE_URL, api_key=client_api_key)
    return parent


def _build_child(parent):
    """Run _build_child_agent against a mocked AIAgent and return the kwargs."""
    with patch("run_agent.AIAgent") as MockAgent:
        MockAgent.return_value = MagicMock()
        _build_child_agent(
            task_index=0,
            goal="repro 401",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            parent_agent=parent,
            task_count=1,
        )
        return MockAgent.call_args.kwargs


class TestChildInheritsLiveApiKey:
    """The child must inherit the LIVE credential pair, not the stale surface.

    URL side is already fixed (25b734845 / a67ddf598); these tests pin the
    api_key side to the same contract.
    """

    def test_child_gets_live_url_but_stale_key(self):
        """Parity with test_build_child_agent_inherits_active_client_endpoint:
        when the live client points elsewhere, the child must inherit the live
        URL *and* the live key. Current code inherits the live URL but keeps
        the stale surface key -> the child calls the live endpoint with the
        wrong key -> 401."""
        parent = _make_stale_parent()
        kwargs = _build_child(parent)

        # URL: live endpoint wins (already fixed by _inherit_parent_base_url).
        assert kwargs["base_url"] == LIVE_URL
        # API key: live key must win over the stale truthy surface key.
        assert kwargs["api_key"] == LIVE_KEY  # BUG: gets STALE_KEY today

    def test_child_never_consults_live_client_api_key(self):
        """Even when BOTH surface attrs are stale but the mounted client holds
        a fresh key (e.g. mid-session OAuth refresh rebuilt the client), the
        child inherits the stale key — the live client's key is never read."""
        parent = _make_stale_parent(
            client_api_key="sk-fresh-rotated-key",
            client_kwargs_api_key="sk-expired-kwargs",
        )
        kwargs = _build_child(parent)

        assert kwargs["base_url"] == LIVE_URL
        # The only place the FRESH key exists is the live client.
        assert kwargs["api_key"] == "sk-fresh-rotated-key"


class TestInheritParentApiKey:
    """Direct unit tests for ``_inherit_parent_api_key`` (mirror of
    ``_inherit_parent_base_url`` on the credential side)."""

    def test_prefers_live_client_key_over_stale_surface(self):
        parent = _make_stale_parent(client_api_key=LIVE_KEY)
        assert _inherit_parent_api_key(parent, parent.api_key) == LIVE_KEY

    def test_prefers_client_kwargs_key_when_client_holds_no_key(self):
        parent = _make_stale_parent()
        parent.client = SimpleNamespace(base_url=LIVE_URL)  # no api_key attr
        assert _inherit_parent_api_key(parent, parent.api_key) == LIVE_KEY

    def test_returns_fallback_when_live_keys_match_surface(self):
        parent = _make_stale_parent()
        parent.api_key = LIVE_KEY
        parent._client_kwargs["api_key"] = LIVE_KEY
        parent.client = SimpleNamespace(base_url=LIVE_URL, api_key=LIVE_KEY)
        assert _inherit_parent_api_key(parent, LIVE_KEY) == LIVE_KEY

    def test_passes_callable_token_provider_through(self):
        def token_provider():
            return "fresh-bearer"

        parent = _make_stale_parent()
        parent.api_key = token_provider
        parent._client_kwargs = {}  # anthropic_messages shape
        parent.client = None
        assert _inherit_parent_api_key(parent, token_provider) is token_provider

    def test_ignores_non_string_live_key(self):
        """Mock-ish / non-str client keys must not override a real surface key."""
        parent = _make_stale_parent(client_kwargs_api_key=None)
        parent._client_kwargs["api_key"] = None  # kwargs carries no key either
        parent.client = MagicMock()  # client.api_key auto-creates a MagicMock
        assert _inherit_parent_api_key(parent, parent.api_key) == parent.api_key  # BUG: gets STALE_KEY today


# ---------------------------------------------------------------------------
# End-to-end 401 trigger: run the exact credential pair _build_child_agent
# produces against a real local mock OpenAI endpoint. The child's inherited
# pair (live URL + stale key) must be rejected with HTTP 401, while the
# parent's live pair (live URL + live key) is accepted.
# ---------------------------------------------------------------------------
class _ChatHandler(BaseHTTPRequestHandler):
    live_key = LIVE_KEY
    status_seen = []

    def do_POST(self):  # noqa: N802 — http.server API
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {self.live_key}":
            self._reply(401, {"error": {
                "message": "Incorrect API key provided",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }})
            return
        self._reply(200, {
            "id": "chatcmpl-repro",
            "object": "chat.completion",
            "created": 0,
            "model": "some-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture()
def mock_openai_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestE2EChildPair401:
    """End-to-end trigger with a REAL OpenAI SDK client against a local mock
    OpenAI endpoint that enforces the live key (returns HTTP 401 otherwise).

    Before the fix this test FAILED: the child's inherited pair was
    (live_url, stale_key) — the mock endpoint rejected it with HTTP 401,
    exactly the sub-agent 401 reported by users. After the fix the child
    inherits (live_url, live_key) and the endpoint accepts it (HTTP 200);
    this test pins that contract.
    """

    def test_child_inherited_pair_is_accepted_by_live_endpoint(self, mock_openai_server):
        import openai

        live_url = mock_openai_server
        parent = _make_stale_parent()
        # Make the parent's "live" endpoint point at the running mock server
        # so the child inherits a real reachable URL.
        parent._client_kwargs["base_url"] = live_url
        parent.client.base_url = live_url

        kwargs = _build_child(parent)
        assert kwargs["base_url"] == live_url

        # Contract: the credential pair the parent is ACTUALLY using must also
        # work from the child. Today the child sends the stale surface key and
        # the live endpoint answers HTTP 401 (openai.AuthenticationError).
        child_client = openai.OpenAI(
            api_key=kwargs["api_key"], base_url=live_url, max_retries=0, timeout=10
        )
        try:
            resp = child_client.chat.completions.create(
                model="some-model",
                messages=[{"role": "user", "content": "hi"}],
            )
        except openai.AuthenticationError as exc:  # HTTP 401
            pytest.fail(
                "sub-agent 401: the child's inherited credential pair "
                f"(base_url={kwargs['base_url']!r}, api_key={kwargs['api_key']!r}) "
                f"was rejected by the main agent's live endpoint with HTTP "
                f"{exc.status_code} — child uses a different api-key than the "
                "main agent"
            )
        assert resp.choices[0].message.content == "ok"
