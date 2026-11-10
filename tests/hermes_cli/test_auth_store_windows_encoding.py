"""Regression tests for auth store encoding on Windows.

``_load_auth_store`` and the Codex/Nous shared-store readers previously called
``Path.read_text()`` with no ``encoding=``, so the bytes were decoded with
``locale.getpreferredencoding()`` — cp1252 on Windows. The store is *written* as
UTF-8 (``_save_auth_store`` uses ``encoding="utf-8"``), so any non-ASCII byte
(e.g. a CJK or emoji credential label) raised ``UnicodeDecodeError`` on read.

Worst case: ``_load_auth_store``'s broad ``except`` then copied the file to
``.json.corrupt`` and returned an *empty* store — silently wiping every
provider credential. These tests pin the round-trip so a non-ASCII label
survives a save→load cycle, and assert the readers pass an explicit UTF-8
encoding (the fix) instead of relying on the locale default.

Regression for issue #180: the reported host is a Simplified-Chinese Windows
where the preferred encoding is GBK/CP936, and the store's ``credential_pool``
keys themselves are Chinese (``custom:智谱-glm-·-api-按量付费``). GBK is the
nastier locale of the two — it is permissive enough to decode a good share of
UTF-8 bytes into *valid* mojibake, so locale-default decoding can corrupt
credential names silently instead of raising. ``TestGbkLocaleAuthStore`` pins
every store read/write/recovery path under a mocked GBK default.
"""

from __future__ import annotations

import builtins
import json
import locale
from pathlib import Path

import pytest

from hermes_cli import auth


# --- helpers ---------------------------------------------------------------

@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a tmp dir so we never touch the real auth store.

    Required because ``_auth_file_path()`` has a seat belt that refuses to
    resolve to the real user's ~/.hermes/auth.json under pytest.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _write_utf8(path: Path, payload: dict) -> None:
    """Write JSON as UTF-8 with non-ASCII chars as real UTF-8 bytes.

    Uses ``ensure_ascii=False`` so the file actually contains non-ASCII bytes
    (the trigger for the Windows cp1252 bug). The default ``ensure_ascii=True``
    would escape them to ``\\uXXXX`` ASCII, hiding the bug.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def windows_default_encoding(monkeypatch):
    """Simulate the Windows locale default for ``Path.read_text()``.

    On Windows, ``locale.getpreferredencoding()`` is ``cp1252``, so a bare
    ``read_text()`` decodes UTF-8 bytes as cp1252 and raises
    ``UnicodeDecodeError`` on any non-ASCII byte. POSIX test runners default to
    UTF-8, so the bug is invisible there — this fixture makes a no-encoding
    ``read_text()`` behave like Windows (cp1252), while leaving calls that pass
    an explicit encoding untouched. That lets the regression tests actually
    catch the bug on any platform.
    """
    real_read_text = Path.read_text

    def _windows_read_text(self, *args, **kwargs):
        if "encoding" not in kwargs and not args:
            # No explicit encoding → force the Windows default.
            kwargs["encoding"] = "cp1252"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _windows_read_text)




# --- the bug: a non-ASCII label must survive save → load -------------------

class TestAuthStoreEncodingRoundTrip:
    def test_load_reads_utf8_with_non_ascii_label(self, hermes_home, windows_default_encoding):
        """A UTF-8 store with a CJK/emoji label loads intact (not wiped).

        Under the Windows-default-encoding fixture, a no-encoding read_text()
        raises UnicodeDecodeError on the non-ASCII bytes and the broad except
        wipes the store — so this test catches the bug on any platform.
        """
        store = {
            "version": auth.AUTH_STORE_VERSION,
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "label": "工作账号 🔥",  # non-ASCII: CJK + emoji
                    "tokens": {"access_token": "a", "refresh_token": "r"},
                }
            },
        }
        auth_path = hermes_home / "auth.json"
        _write_utf8(auth_path, store)

        loaded = auth._load_auth_store(auth_path)

        # The label round-trips exactly — the provider is NOT lost.
        assert "openai-codex" in loaded["providers"]
        assert loaded["providers"]["openai-codex"]["label"] == "工作账号 🔥"

    def test_load_does_not_corrupt_store_on_non_ascii(self, hermes_home, windows_default_encoding):
        """The pre-fix bug wiped the store to empty on a UnicodeDecodeError.

        After the fix, loading a valid UTF-8 store must never produce the empty
        fallback, and must never write a .json.corrupt sidecar.
        """
        store = {
            "version": auth.AUTH_STORE_VERSION,
            "providers": {"x": {"label": "José's key"}},
        }
        auth_path = hermes_home / "auth.json"
        _write_utf8(auth_path, store)

        auth._load_auth_store(auth_path)

        assert not (hermes_home / "auth.json.corrupt").exists()
        # original file untouched — read it back and compare structurally
        # (json.dumps may escape non-ASCII, so compare parsed values, not text)
        on_disk = json.loads(auth_path.read_text(encoding="utf-8"))
        assert on_disk["providers"]["x"]["label"] == "José's key"

    def test_load_handles_utf8_with_bom(self, hermes_home):
        """A BOM (e.g. from Notepad editing) must not break the read."""
        store = {"version": auth.AUTH_STORE_VERSION, "providers": {"x": {"label": "café"}}}
        auth_path = hermes_home / "auth.json"
        payload = json.dumps(store)
        # write with utf-8-sig to prepend the BOM
        auth_path.write_text(payload, encoding="utf-8-sig")

        loaded = auth._load_auth_store(auth_path)
        assert loaded["providers"]["x"]["label"] == "café"


# --- the fix: readers pass an explicit encoding ---------------------------



# --- sibling readers of the same ~/.hermes/auth.json in other modules -------
#
# _load_auth_store lives in hermes_cli/auth.py, but several other modules read
# the same ~/.hermes/auth.json directly. They had the same UTF-8-vs-cp1252
# asymmetry on Windows — these tests pin the sibling reads too.

class TestAuthJsonSiblingReaders:
    def test_has_xai_credentials_reads_non_ascii_store(self, hermes_home, windows_default_encoding, monkeypatch):
        """tools/xai_http.has_xai_credentials must read a non-ASCII auth.json.

        Pre-fix, a UTF-8 store with a non-ASCII label raised UnicodeDecodeError
        here (cp1252 default on Windows), the broad except swallowed it, and
        xAI OAuth silently looked absent.
        """
        # No XAI_API_KEY env → force the auth.json code path.
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        store = {
            "version": auth.AUTH_STORE_VERSION,
            "providers": {
                "xai-oauth": {
                    # CJK label → UTF-8 bytes (e.g. 0xE5..) that cp1252 cannot
                    # decode, so a no-encoding read raises UnicodeDecodeError.
                    "label": "工作账号",
                    "tokens": {"access_token": "tok"},
                }
            },
        }
        _write_utf8(hermes_home / "auth.json", store)

        from tools.xai_http import has_xai_credentials

        assert has_xai_credentials() is True

    def test_auxiliary_nous_provider_reads_non_ascii_store(self, hermes_home, windows_default_encoding, monkeypatch):
        """agent/auxiliary_client's Nous-provider lookup reads the same store.

        The lookup returns None on any read failure, silently disabling Nous as
        the auxiliary (vision/summarization) provider. A non-ASCII label must
        not trigger that path.
        """
        store = {
            "version": auth.AUTH_STORE_VERSION,
            "active_provider": "nous",
            "providers": {"nous": {"agent_key": "k", "label": "工作账号"}},
        }
        _write_utf8(hermes_home / "auth.json", store)

        import agent.auxiliary_client as aux

        # _AUTH_JSON_PATH is resolved at module import time, so the
        # HERMES_HOME env from the fixture doesn't reach it — point it at
        # the tmp store explicitly.
        monkeypatch.setattr(aux, "_AUTH_JSON_PATH", hermes_home / "auth.json")

        # _read_nous_auth consults the credential pool FIRST and returns early
        # when a pool entry exists, never reaching the auth.json read. Force the
        # pool-absent path so the auth.json code under test actually runs.
        monkeypatch.setattr(aux, "_select_pool_entry", lambda _provider: (False, None))

        provider = aux._read_nous_auth()
        assert provider is not None
        assert provider.get("agent_key") == "k"

    def test_read_shared_nous_state_reads_non_ascii_store(
        self, tmp_path, monkeypatch, windows_default_encoding
    ):
        """hermes_cli.auth._read_shared_nous_state must read a non-ASCII store.

        The shared Nous store (``nous_auth.json``) is written as UTF-8. A
        non-ASCII field (e.g. an accented display name) must not cause the
        read to raise under the Windows-default-encoding fixture and be
        silently swallowed — which would drop the user's shared OAuth
        credentials and force a device-code re-login.
        """
        # The shared-store path has a seat belt that refuses to resolve to the
        # real user's store under pytest; pin it to a tmp dir explicitly.
        shared_dir = tmp_path / "shared"
        monkeypatch.setenv("HERMES_SHARED_AUTH_DIR", str(shared_dir))

        payload = {
            "refresh_token": "rt",
            "access_token": "at",
            # Non-ASCII display name → UTF-8 bytes cp1252 cannot decode.
            "display_name": "Réne — Noël",
        }
        _write_utf8(shared_dir / "nous_auth.json", payload)

        provider = auth._read_shared_nous_state()
        assert provider is not None
        assert provider["access_token"] == "at"
        assert provider["refresh_token"] == "rt"
        # The non-ASCII field round-trips intact.
        assert provider["display_name"] == "Réne — Noël"

    def test_has_any_provider_configured_reads_non_ascii_auth_store(
        self, hermes_home, monkeypatch, windows_default_encoding
    ):
        """hermes_cli.main._has_any_provider_configured reads auth.json.

        When the active provider's auth.json store contains a non-ASCII
        label, the read must not raise under the Windows-default-encoding
        fixture (which would be swallowed and make a configured provider
        look absent, wrongly re-triggering the setup wizard).
        """
        store = {
            "version": auth.AUTH_STORE_VERSION,
            "active_provider": "openai-codex",
            "providers": {
                "openai-codex": {
                    "auth_mode": "chatgpt",
                    "label": "工作账号",  # non-ASCII CJK
                    "tokens": {"access_token": "a", "refresh_token": "r"},
                }
            },
        }
        _write_utf8(hermes_home / "auth.json", store)

        import hermes_cli.auth as auth_mod
        import hermes_cli.main as main_mod

        # No provider env vars, no .env, and PROVIDER_REGISTRY lookups report
        # not-logged-in — so the function reaches the auth.json branch and the
        # result is driven by reading the (non-ASCII) store + the active
        # provider's status. The active provider IS logged in, so the UTF-8
        # read must succeed and surface True.
        def fake_status(provider_id=None):
            if provider_id == "openai-codex":
                return {"logged_in": True}
            return {"logged_in": False}

        monkeypatch.setattr(auth_mod, "get_auth_status", fake_status)
        # _has_any_provider_configured imports get_auth_status lazily from
        # hermes_cli.auth; patch it on the source module so the local import
        # sees the fake.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        assert main_mod._has_any_provider_configured() is True

    def test_managed_tool_gateway_reads_non_ascii_nous_state(
        self, hermes_home, windows_default_encoding
    ):
        """tools.managed_tool_gateway._read_nous_provider_state reads auth.json.

        The Nous provider entry can carry a non-ASCII label. Under the
        Windows-default-encoding fixture a no-encoding read raises and the
        broad except swallows it, returning None — so the gateway treats
        Nous as unconfigured.
        """
        store = {
            "version": auth.AUTH_STORE_VERSION,
            "providers": {
                "nous": {
                    "agent_key": "k",
                    # Non-ASCII label → UTF-8 bytes cp1252 cannot decode.
                    "label": "工作账号",
                }
            },
        }
        _write_utf8(hermes_home / "auth.json", store)

        from tools.managed_tool_gateway import _read_nous_provider_state

        nous = _read_nous_provider_state()
        assert nous is not None
        assert nous.get("agent_key") == "k"
        # The non-ASCII label round-trips intact.
        assert nous.get("label") == "工作账号"


# --- issue #180: GBK (Simplified-Chinese Windows) locale default -----------
#
# The report: on a zh-CN Windows host the preferred encoding is GBK/CP936, the
# frozen runtime read the UTF-8 store with no ``encoding=``, the decode raised,
# and ``_load_auth_store`` copied the file to ``auth.json.corrupt`` and returned
# an EMPTY store — so the next save rebuilt auth.json from scratch and every
# provider whose pool key held Chinese was gone. The key in the report is a
# custom provider (``custom:智谱-glm-·-api-按量付费``), i.e. the mojibake risk sits
# on the *key*, which is what identifies both the pool slice and the provider.

# The exact provider id from the report: Chinese, a middle dot and a full
# Chinese suffix — the mixed bytes are what a locale decode trips over.
CHINESE_PROVIDER = "custom:智谱-glm-·-api-按量付费"


@pytest.fixture
def gbk_default_encoding(monkeypatch):
    """Make every default-encoding entry point behave like a zh-CN Windows host.

    The cp1252 fixture above only patches ``Path.read_text`` because that was the
    one reader with the bug. This fixture simulates the whole default-open
    surface the report names — ``Path.open``/``read_text``/``write_text`` and
    ``builtins.open`` — plus ``locale.getpreferredencoding()``, while leaving any
    call that passes an explicit ``encoding=`` untouched. A store operation that
    relies on the locale then raises (or, for byte runs GBK happily decodes,
    silently returns mojibake) exactly as it does on the reporter's machine.
    """
    real_open = builtins.open
    real_path_open = Path.open
    real_read_text = Path.read_text
    real_write_text = Path.write_text

    def _locale_encode(kwargs, args):
        # Only fill in the default: an explicit encoding= (or a positional
        # encoding) is what the fix does and must keep working unchanged.
        if "encoding" not in kwargs and not args:
            kwargs["encoding"] = "gbk"

    def _gbk_open(file, mode="r", *args, **kwargs):
        if "b" not in str(mode):
            _locale_encode(kwargs, args)
        return real_open(file, mode, *args, **kwargs)

    def _gbk_path_open(self, mode="r", *args, **kwargs):
        if "b" not in str(mode):
            _locale_encode(kwargs, args)
        return real_path_open(self, mode, *args, **kwargs)

    def _gbk_read_text(self, *args, **kwargs):
        _locale_encode(kwargs, args)
        return real_read_text(self, *args, **kwargs)

    def _gbk_write_text(self, data, *args, **kwargs):
        _locale_encode(kwargs, args)
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _gbk_open)
    monkeypatch.setattr(Path, "open", _gbk_path_open)
    monkeypatch.setattr(Path, "read_text", _gbk_read_text)
    monkeypatch.setattr(Path, "write_text", _gbk_write_text)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "gbk")
    if hasattr(locale, "getencoding"):
        monkeypatch.setattr(locale, "getencoding", lambda: "gbk")


def _chinese_store() -> dict:
    """A store shaped like the report's: Chinese provider keys in both sections."""
    return {
        "version": auth.AUTH_STORE_VERSION,
        "active_provider": CHINESE_PROVIDER,
        "providers": {
            CHINESE_PROVIDER: {
                "auth_mode": "api_key",
                "api_key": "sk-中文密钥",
                "label": "智谱 GLM 按量付费",
            }
        },
        "credential_pool": {
            CHINESE_PROVIDER: [
                {
                    "id": "cred-中文-1",
                    "label": "按量付费凭据",
                    "source": "manual:api_key",
                    "auth_type": "api_key",
                    "api_key": "sk-中文密钥",
                }
            ]
        },
    }


class TestGbkLocaleAuthStore:
    def test_load_keeps_chinese_credential_pool_keys(self, hermes_home, gbk_default_encoding):
        """A GBK-default read must return the Chinese pool key verbatim.

        Pre-fix this raised ``UnicodeDecodeError`` inside ``_load_auth_store``,
        which quarantined the file as ``auth.json.corrupt`` and handed back an
        empty store — the reported data loss. The store must load intact, the
        file must never be quarantined, and a read must not rewrite it.
        """
        auth_path = hermes_home / "auth.json"
        store = _chinese_store()
        _write_utf8(auth_path, store)
        before = auth_path.read_bytes()

        # Guard: the mocked locale really does reject the store's raw UTF-8
        # bytes, so the assertions below are only meaningful because the loader
        # passes encoding= explicitly. (If the simulation stopped being GBK,
        # this test would pass for the wrong reason.)
        with pytest.raises(UnicodeDecodeError):
            before.decode(locale.getpreferredencoding(False))

        loaded = auth._load_auth_store(auth_path)

        assert set(loaded["providers"]) == {CHINESE_PROVIDER}
        assert set(loaded["credential_pool"]) == {CHINESE_PROVIDER}
        assert loaded["providers"][CHINESE_PROVIDER]["label"] == "智谱 GLM 按量付费"
        assert loaded["credential_pool"][CHINESE_PROVIDER][0]["label"] == "按量付费凭据"
        assert loaded["active_provider"] == CHINESE_PROVIDER
        # No locale-corrupt rename/copy, and the read left the bytes alone.
        assert not (hermes_home / "auth.json.corrupt").exists()
        assert auth_path.read_bytes() == before

    def test_write_credential_pool_roundtrip_keeps_chinese_provider(
        self, hermes_home, gbk_default_encoding
    ):
        """Saving a pool for a Chinese-key provider writes UTF-8 and reads back.

        ``write_credential_pool`` is the read-modify-write path a session takes
        on every credential change; if either half used the locale encoding the
        key would be re-encoded as mojibake or lost on the next load.
        """
        # Fresh profile: nothing to read yet, so this exercises store creation.
        auth.write_credential_pool(
            CHINESE_PROVIDER,
            [
                {
                    "id": "cred-中文-1",
                    "label": "按量付费凭据",
                    "source": "manual:api_key",
                    "auth_type": "api_key",
                    "api_key": "sk-中文密钥",
                }
            ],
        )

        rows = auth.read_credential_pool(CHINESE_PROVIDER)
        assert [row["id"] for row in rows] == ["cred-中文-1"]
        assert rows[0]["label"] == "按量付费凭据"

        # On disk: the key is raw UTF-8 (the same bytes the schema has always
        # written), never a GBK transcode, and the pool section keeps it.
        auth_path = hermes_home / "auth.json"
        assert CHINESE_PROVIDER.encode("utf-8") in auth_path.read_bytes()
        persisted = json.loads(auth_path.read_bytes().decode("utf-8"))
        assert set(persisted["credential_pool"]) == {CHINESE_PROVIDER}
        assert not (hermes_home / "auth.json.corrupt").exists()

    def test_corrupt_quarantine_copies_bytes_and_never_touches_the_store(
        self, hermes_home, gbk_default_encoding
    ):
        """Genuine corruption is quarantined byte-for-byte, not transcoded.

        Recovery (the issue's startup watchdog renames ``.corrupt`` back) is only
        safe if the quarantine is a byte copy of what the user had: a locale
        transcode there would destroy the Unicode credentials it is meant to
        preserve. ``_load_auth_store`` must also leave ``auth.json`` itself in
        place, so an unlucky read alone cannot erase the store.
        """
        auth_path = hermes_home / "auth.json"
        # Invalid UTF-8 (a lone 0xFF) — unreadable under ANY strict codec, so the
        # quarantine branch is what runs.
        bad_bytes = b'{"version": 1, "providers": {"\\xff": {"label": "\xe6\x99"}}}'
        auth_path.write_bytes(bad_bytes)

        loaded = auth._load_auth_store(auth_path)

        assert loaded == auth._empty_auth_store()
        corrupt_path = hermes_home / "auth.json.corrupt"
        assert corrupt_path.read_bytes() == bad_bytes
        # The quarantine is a copy: the user's file is still there to restore,
        # and the bytes were never decoded/re-encoded through the locale.
        assert auth_path.read_bytes() == bad_bytes

    def test_profile_store_write_through_is_utf8(self, hermes_home, gbk_default_encoding):
        """A profile→global write-through (``target_path``) stays UTF-8.

        Profiles are islands: the rotating-grant write-through saves to an
        explicit target instead of ``HERMES_HOME/auth.json``. That second path
        must encode identically or a profile's Chinese provider names would be
        lost the moment they are mirrored to the root store.
        """
        target = hermes_home / "root" / "auth.json"
        store = _chinese_store()

        auth._save_auth_store(store, target_path=target)

        # Parent dir is created/tightened by _save_private_json; the payload is
        # raw UTF-8 and round-trips through the same loader.
        assert CHINESE_PROVIDER.encode("utf-8") in target.read_bytes()
        assert set(auth._load_auth_store(target)["credential_pool"]) == {CHINESE_PROVIDER}
        assert not target.with_suffix(".json.corrupt").exists()
