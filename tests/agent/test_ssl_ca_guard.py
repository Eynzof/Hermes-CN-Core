"""Tests for the preventive SSL CA bundle guard."""

from pathlib import Path

import certifi
import pytest

from agent.errors import SSLConfigurationError
from agent.ssl_guard import verify_ca_bundle, verify_ca_bundle_with_fallback


@pytest.fixture(autouse=True)
def _clean_ssl_guard_cache():
    """Reset the process-level CA-validation memo around every test.

    ``verify_ca_bundle`` memoises a successful validation (perf: the ~200ms
    context load is otherwise re-paid on every AIAgent init).  Clearing it per
    test keeps each case hermetic regardless of run order.
    """
    from agent.ssl_guard import _reset_ca_bundle_cache

    _reset_ca_bundle_cache()
    yield
    _reset_ca_bundle_cache()


def test_healthy_bundle_passes(monkeypatch):
    """A real, non-empty certifi bundle must verify without raising."""
    for key in ("HERMES_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(key, raising=False)
    bundle = Path(certifi.where())
    assert bundle.exists()
    assert bundle.stat().st_size > 1024
    verify_ca_bundle()


def test_missing_certifi_bundle_raises_ssl_error(monkeypatch, tmp_path):
    """Point certifi.where() at a non-existent path; expect a clear error."""
    fake = tmp_path / "nope.pem"
    monkeypatch.setattr(certifi, "where", lambda: str(fake))
    with pytest.raises(SSLConfigurationError) as exc:
        verify_ca_bundle()
    message = str(exc.value).lower()
    assert "certifi" in message
    assert "missing" in message
    assert "force-reinstall" in message


def test_empty_certifi_bundle_raises_ssl_error(monkeypatch, tmp_path):
    """Empty file is treated as a corrupted bundle."""
    fake = tmp_path / "empty.pem"
    fake.write_bytes(b"")
    monkeypatch.setattr(certifi, "where", lambda: str(fake))
    with pytest.raises(SSLConfigurationError) as exc:
        verify_ca_bundle()
    assert "too small" in str(exc.value).lower()


@pytest.mark.parametrize("env_var", ["HERMES_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_missing_explicit_ca_bundle_env_raises_before_httpx(monkeypatch, tmp_path, env_var):
    """Bad CA-bundle env vars should be reported before OpenAI/httpx init."""
    fake = tmp_path / "missing.pem"
    monkeypatch.setenv(env_var, str(fake))
    with pytest.raises(SSLConfigurationError) as exc:
        verify_ca_bundle()
    message = str(exc.value)
    assert env_var in message
    assert str(fake) in message
    assert "force-reinstall" in message


def test_invalid_explicit_ca_bundle_env_raises(monkeypatch, tmp_path):
    """An existing but invalid explicit bundle should get a user-facing error."""
    fake = tmp_path / "broken.pem"
    fake.write_text("not a cert bundle", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(fake))
    with pytest.raises(SSLConfigurationError) as exc:
        verify_ca_bundle()
    assert "cannot be loaded" in str(exc.value)


def test_verify_ca_bundle_with_fallback_keeps_same_contract(monkeypatch, tmp_path):
    """The compatibility wrapper still rejects broken explicit CA paths."""
    fake = tmp_path / "missing.pem"
    monkeypatch.setenv("SSL_CERT_FILE", str(fake))
    with pytest.raises(SSLConfigurationError):
        verify_ca_bundle_with_fallback()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_skip_env_var_bypasses_guard(monkeypatch, tmp_path, value):
    """HERMES_SKIP_SSL_GUARD is an intentional escape hatch for managed trust stores."""
    fake = tmp_path / "missing.pem"
    monkeypatch.setenv("HERMES_SKIP_SSL_GUARD", value)
    monkeypatch.setenv("SSL_CERT_FILE", str(fake))
    verify_ca_bundle()
    verify_ca_bundle_with_fallback()



# ---------------------------------------------------------------------------
# Process-level memoisation (.plans/15 — SSL init hotspot, ~8.19% self-time)
#
# verify_ca_bundle() built a throwaway ssl.create_default_context() (~200ms on
# Windows) on EVERY AIAgent construction. It now caches the successful verdict
# on a fingerprint of the CA env vars + certifi bundle, so an unchanged CA
# configuration is validated at most once per process while any change still
# re-validates (and re-raises).
# ---------------------------------------------------------------------------


def _clear_ca_env(monkeypatch):
    for key in ("HERMES_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(key, raising=False)


def test_verify_ca_bundle_memoized_on_repeat(monkeypatch):
    """An unchanged CA configuration validates once, then serves cache hits —
    the expensive per-bundle context build is not repeated."""
    import agent.ssl_guard as g

    _clear_ca_env(monkeypatch)
    g._reset_ca_bundle_cache()

    calls = {"n": 0}
    orig = g._validate_bundle_path

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(g, "_validate_bundle_path", counting)

    g.verify_ca_bundle()  # cold: validates the certifi bundle
    cold = calls["n"]
    assert cold >= 1, "cold verify must actually validate the bundle"

    for _ in range(5):
        g.verify_ca_bundle()  # warm: fingerprint unchanged -> cache hits
    assert calls["n"] == cold, "an unchanged CA config must not re-validate"

    # A genuine bump (e.g. certifi reinstall) must re-validate.
    g._reset_ca_bundle_cache()
    g.verify_ca_bundle()
    assert calls["n"] == cold + 1


def test_verify_ca_bundle_reinvalidates_on_env_change(monkeypatch, tmp_path):
    """Changing a CA env var busts the memo and re-runs validation (raises)."""
    import agent.ssl_guard as g

    _clear_ca_env(monkeypatch)
    g._reset_ca_bundle_cache()
    g.verify_ca_bundle()  # caches the good (certifi-only) verdict

    bad = tmp_path / "missing.pem"
    monkeypatch.setenv("SSL_CERT_FILE", str(bad))
    with pytest.raises(SSLConfigurationError):
        g.verify_ca_bundle()  # different fingerprint -> re-validate -> raise


def test_fingerprint_tracks_certifi_identity(monkeypatch, tmp_path):
    """The fingerprint changes when certifi's bundle path changes, so a swapped
    bundle is re-validated rather than served from a stale verdict."""
    import agent.ssl_guard as g

    _clear_ca_env(monkeypatch)
    fp_before = g._ca_bundle_fingerprint()

    fake = tmp_path / "other.pem"
    fake.write_bytes(b"x" * 2048)
    monkeypatch.setattr("certifi.where", lambda: str(fake))
    fp_after = g._ca_bundle_fingerprint()
    assert fp_before != fp_after
