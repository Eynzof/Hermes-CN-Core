"""Tests for the config-load copy/expansion optimizations.

Covers ``.plans/12-Copy-Deepcopy-Config-Overhead.md``:

* ``load_config()`` copies the (large) config tree with a JSON-specialised
  ``_fast_config_copy`` instead of ``copy.deepcopy`` on every call.
* the returned config is still a fully *independent* deep copy (mutation by one
  caller must never corrupt the cache or another caller) — this is why the
  plan's "use a shallow copy" suggestion is unsafe here and was not taken.
* ``_expand_env_vars`` skips the regex / ``os.environ`` probe for template-free
  strings and returns unchanged containers by identity, and the loader skips the
  whole expansion pass when nothing can contain a ``${VAR}`` template.
* env expansion still reflects the *current* environment on a file change (no
  stale global env cache — another plan suggestion deliberately not taken).
"""

import copy

import pytest

from hermes_cli import config as cfg
from hermes_cli.config import (
    DEFAULT_CONFIG,
    _default_has_env_templates,
    _expand_env_vars,
    _fast_config_copy,
    _normalize_max_turns_config,
    _normalize_root_model_keys,
    _tree_has_env_template,
    get_config_path,
    load_config,
    load_config_readonly,
)


def _clear_caches():
    cfg._LOAD_CONFIG_CACHE.clear()
    cfg._RAW_CONFIG_CACHE.clear()
    cfg._LAST_EXPANDED_CONFIG_BY_PATH.clear()


# ---------------------------------------------------------------------------
# _fast_config_copy — the copy.deepcopy replacement
# ---------------------------------------------------------------------------
class TestFastConfigCopy:
    def test_matches_deepcopy_for_json(self):
        src = {
            "s": "str", "i": 3, "f": 1.5, "b": True, "n": None,
            "list": [1, "two", {"k": "v"}],
            "nested": {"deep": {"x": [0, 1, 2]}},
            "tuple": (1, 2, {"z": "w"}),
        }
        assert _fast_config_copy(src) == copy.deepcopy(src)

    def test_deep_independence(self):
        src = {"a": {"b": [1, 2, {"c": "x"}]}, "d": [{"e": 1}]}
        dup = _fast_config_copy(src)
        assert dup == src
        # every mutable container is a fresh object
        assert dup is not src
        assert dup["a"] is not src["a"]
        assert dup["a"]["b"] is not src["a"]["b"]
        assert dup["a"]["b"][2] is not src["a"]["b"][2]
        assert dup["d"][0] is not src["d"][0]
        # mutating the copy leaves the source fully intact
        dup["a"]["b"][2]["c"] = "MUTATED"
        dup["d"][0]["e"] = 99
        assert src["a"]["b"][2]["c"] == "x"
        assert src["d"][0]["e"] == 1

    def test_immutable_scalars_shared_like_deepcopy(self):
        # copy.deepcopy returns the same object for immutable atomics; so does
        # _fast_config_copy (sharing immutables is safe and faster).
        s = "a-shared-immutable-string"
        assert _fast_config_copy(s) is s
        assert _fast_config_copy(None) is None
        assert _fast_config_copy(True) is True

    def test_falls_back_to_deepcopy_for_non_json(self):
        class Custom:
            def __init__(self, v):
                self.v = v

            def __eq__(self, other):
                return isinstance(other, Custom) and other.v == self.v

        src = {"obj": Custom(5), "plain": [1, 2]}
        dup = _fast_config_copy(src)
        assert dup["obj"] == Custom(5)
        # the fallback (copy.deepcopy) produced a genuinely new instance
        assert dup["obj"] is not src["obj"]
        assert dup["plain"] == [1, 2] and dup["plain"] is not src["plain"]


# ---------------------------------------------------------------------------
# test_config_no_deepcopy — load_config must not pay copy.deepcopy for JSON
# ---------------------------------------------------------------------------
class TestConfigNoDeepcopy:
    def test_config_no_deepcopy_on_cache_hit(self, tmp_path, monkeypatch):
        """The hot path (cache hit) copies with _fast_config_copy, never
        copy.deepcopy."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text(
            "display:\n  compact: true\n", encoding="utf-8"
        )
        _clear_caches()
        load_config()  # warm the cache (miss)

        calls = {"n": 0}
        real = copy.deepcopy

        def _spy(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(cfg.copy, "deepcopy", _spy)
        cfg2 = load_config()  # cache hit
        assert cfg2["display"]["compact"] is True
        assert calls["n"] == 0, (
            f"copy.deepcopy called {calls['n']}x on a plain-JSON config cache hit"
        )

    def test_config_no_deepcopy_on_full_build(self, tmp_path, monkeypatch):
        """A cold build (merge + normalise + cache) also never falls back to
        copy.deepcopy for the plain-JSON config."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text(
            "model:\n  default: m1\nplatforms:\n  telegram:\n    token: abc\n",
            encoding="utf-8",
        )
        _clear_caches()

        calls = {"n": 0}
        real = copy.deepcopy

        def _spy(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(cfg.copy, "deepcopy", _spy)
        built = load_config()
        assert built["model"]["default"] == "m1"
        assert calls["n"] == 0

    def test_returned_config_is_independent_of_cache(self, tmp_path, monkeypatch):
        """Mutating the result of load_config() must not corrupt the cache or a
        later caller — i.e. it is a real deep copy, not a shallow one."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text(
            "model:\n  default: m1\n", encoding="utf-8"
        )
        _clear_caches()

        a = load_config()
        a["model"]["default"] = "MUTATED"
        a.setdefault("brand_new_section", {})["x"] = 1

        b = load_config()
        assert b["model"]["default"] == "m1", "cache corrupted by caller mutation"
        assert "brand_new_section" not in b

    def test_readonly_returns_shared_cached_object(self, tmp_path, monkeypatch):
        """load_config_readonly() skips the per-call copy and hands back the same
        cached object (the documented fast path)."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text(
            "model:\n  default: m1\n", encoding="utf-8"
        )
        _clear_caches()
        load_config()  # populate cache
        r1 = load_config_readonly()
        r2 = load_config_readonly()
        assert r1 is r2


# ---------------------------------------------------------------------------
# test_env_var_expansion_cache — expansion correctness with the fast paths
# ---------------------------------------------------------------------------
class TestEnvVarExpansionCache:
    def test_template_free_string_returned_by_identity(self):
        s = "no-template-here"
        assert _expand_env_vars(s) is s  # fast path: no regex, same object

    def test_template_expanded_and_missing_kept_verbatim(self, monkeypatch):
        monkeypatch.setenv("EVX_SET", "resolved")
        monkeypatch.delenv("EVX_UNSET", raising=False)
        assert _expand_env_vars("${EVX_SET}") == "resolved"
        assert _expand_env_vars("${EVX_UNSET}") == "${EVX_UNSET}"
        assert _expand_env_vars("${EVX_SET}/${EVX_UNSET}") == "resolved/${EVX_UNSET}"

    def test_unchanged_containers_returned_by_identity(self):
        d = {"a": "plain", "b": {"c": "also-plain"}, "l": [1, "x", 2]}
        assert _expand_env_vars(d) is d
        assert _expand_env_vars(d["b"]) is d["b"]
        assert _expand_env_vars(d["l"]) is d["l"]

    def test_changed_container_is_fresh_and_source_untouched(self, monkeypatch):
        monkeypatch.setenv("EVX_C", "expanded")
        d = {"keep": "plain", "sub": {"k": "${EVX_C}"}, "list": ["${EVX_C}", "lit"]}
        out = _expand_env_vars(d)
        assert out is not d
        assert out["sub"]["k"] == "expanded"
        assert out["list"] == ["expanded", "lit"]
        # original strings are immutable and must be untouched
        assert d["sub"]["k"] == "${EVX_C}"
        assert d["list"][0] == "${EVX_C}"
        # a template-free sibling subtree keeps its identity through the copy
        assert out["keep"] is d["keep"]

    def test_expansion_reflects_current_env_on_file_change(self, tmp_path, monkeypatch):
        """No stale global env cache: a changed config file re-expands against
        the *current* environment."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _clear_caches()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("model:\n  api_key: ${EVX_ROT}\n", encoding="utf-8")

        monkeypatch.setenv("EVX_ROT", "first")
        assert load_config()["model"]["api_key"] == "first"

        # Rotate the env AND change the file (new size => new cache signature).
        monkeypatch.setenv("EVX_ROT", "second")
        cfg_file.write_text(
            "model:\n  api_key: ${EVX_ROT}\n  extra: 1\n", encoding="utf-8"
        )
        assert load_config()["model"]["api_key"] == "second"


# ---------------------------------------------------------------------------
# Skip-expansion + no-file caching correctness
# ---------------------------------------------------------------------------
class TestSkipExpandAndNoFileCache:
    def test_default_config_templates_flag_matches_scan(self):
        # Behaviour contract (not a snapshot): the memoised flag equals a fresh
        # scan of DEFAULT_CONFIG. Today defaults are literal, so the loader may
        # skip expansion; if a default ever gains a ${VAR} the flag flips and
        # expansion turns back on automatically.
        assert _default_has_env_templates() == _tree_has_env_template(DEFAULT_CONFIG)

    def test_no_file_load_equals_full_expand_of_defaults(self, tmp_path, monkeypatch):
        """The skip-expansion no-file path yields exactly what the full
        normalise+expand pipeline would."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _clear_caches()
        loaded = load_config()
        expected = _expand_env_vars(
            _normalize_root_model_keys(
                _normalize_max_turns_config(_fast_config_copy(DEFAULT_CONFIG))
            )
        )
        assert loaded == expected

    def test_no_file_result_is_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _clear_caches()
        c1 = load_config()
        key = str(get_config_path())
        assert key in cfg._LOAD_CONFIG_CACHE, "no-file result was not cached"
        assert cfg._LOAD_CONFIG_CACHE[key][:4] == cfg._NO_FILE_CACHE_SIG
        c2 = load_config()
        assert c1 == c2 and "model" in c2

    def test_no_file_cache_invalidated_when_config_appears(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _clear_caches()
        first = load_config()  # cached under the no-file sentinel
        assert first is not None
        # Writing a real config must invalidate the sentinel entry on next load.
        (tmp_path / "config.yaml").write_text(
            "display:\n  compact: true\n", encoding="utf-8"
        )
        after = load_config()
        assert after["display"]["compact"] is True
