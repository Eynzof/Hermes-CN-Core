"""Profile cloning excludes machine-scoped runtime assets."""
from hermes_cli.profiles import _clone_all_copytree_ignore, create_profile
from tests.hermes_cli.test_profiles import profile_env as profile_env  # noqa: PLC0414 -- shared pytest fixture

class TestCloneAllExcludesRuntimeTrees:
    """``--clone-all`` from the default profile must not copy the machine-scoped
    runtime trees the local-models flow puts under ``~/.hermes``: ``models/``
    (GGUF weights, tens of GB), ``runtimes/`` (llama.cpp binaries) and ``node/``
    (managed Node). ``backup.py`` already excludes exactly these; the clone-all
    ignore list had not followed.
    """

    RUNTIME_TREES = ("models", "runtimes", "node")

    def _seed(self, home):
        (home / "models").mkdir(); (home / "models" / "big.gguf").write_bytes(b"\0" * 64)
        (home / "runtimes" / "llamacpp" / "bin").mkdir(parents=True)
        (home / "runtimes" / "llamacpp" / "bin" / "llama-server").write_text("bin", encoding="utf-8")
        (home / "node" / "bin").mkdir(parents=True)
        (home / "node" / "bin" / "node").write_text("bin", encoding="utf-8")
        (home / "skills" / "greet").mkdir(parents=True)
        (home / "skills" / "greet" / "SKILL.md").write_text("# greet\n", encoding="utf-8")
        (home / "config.yaml").write_text("model: test\n", encoding="utf-8")

    def test_ignore_drops_runtime_trees_only_at_the_default_root(self, profile_env):
        default_home = profile_env / ".hermes"
        self._seed(default_home)
        # a skill that happens to carry a nested models/ dir is user data
        (default_home / "skills" / "greet" / "models").mkdir()
        ignore = _clone_all_copytree_ignore(default_home)

        at_root = ignore(str(default_home), ["models", "runtimes", "node", "skills", "config.yaml"])
        nested = ignore(str(default_home / "skills" / "greet"), ["models", "SKILL.md"])

        assert set(at_root) == set(self.RUNTIME_TREES)
        assert not nested

        # Gated on the default profile: a named profile that really has a
        # models/ dir of its own must not have it dropped when used as source.
        source = create_profile("source", no_alias=True)
        for tree in self.RUNTIME_TREES:
            (source / tree).mkdir()
        assert not _clone_all_copytree_ignore(source)(str(source), [*self.RUNTIME_TREES, "SOUL.md"])


    def test_clone_all_from_default_skips_runtime_trees_but_keeps_the_rest(self, profile_env):
        default_home = profile_env / ".hermes"
        self._seed(default_home)

        clone = create_profile("clone", clone_all=True, no_alias=True)

        for name in self.RUNTIME_TREES:
            assert not (clone / name).exists(), name
        assert (clone / "skills" / "greet" / "SKILL.md").is_file()
        assert (clone / "config.yaml").is_file()
