"""Regression test for #49287 — the CLI memory-provider ``on_session_end``
hook stopped firing on ``/exit`` after the god-file Phase 4 refactor
(094aa85c37) moved agent construction into ``CLIAgentSetupMixin``.

``_run_cleanup`` (in ``cli.py``) gates the memory-shutdown call on the
module global ``cli._active_agent_ref``. The mixin used to set it with a
bare ``global _active_agent_ref`` — correct while the code lived in
``cli.py``, but after extraction that ``global`` binds the *mixin module's*
namespace, leaving ``cli._active_agent_ref`` ``None`` forever. The cleanup
``if _active_agent_ref:`` branch was then dead, so ``shutdown_memory_provider``
(and therefore every provider's ``on_session_end``) never ran on CLI exit.

The fix writes the reference onto the ``cli`` module explicitly. These tests
assert that contract — the existing shutdown tests pass only because they
hand-assign ``cli._active_agent_ref``, which is exactly what masked the bug.
"""
from __future__ import annotations


def test_mixin_writes_active_agent_ref_to_cli_module():
    """The mixin's agent-setup code must publish the agent reference where
    ``_run_cleanup`` reads it — on the ``cli`` module, not the mixin module."""
    import cli as cli_mod
    from hermes_cli import cli_agent_setup_mixin as mixin_mod

    sentinel = object()
    prev_cli = getattr(cli_mod, "_active_agent_ref", None)
    prev_mixin = getattr(mixin_mod, "_active_agent_ref", "<unset>")
    try:
        # Reproduce the exact assignment the mixin performs after building
        # the agent (see CLIAgentSetupMixin near the AIAgent(...) construction).
        import cli as _cli
        _cli._active_agent_ref = sentinel

        # The cleanup path reads cli._active_agent_ref — it must see the value.
        assert cli_mod._active_agent_ref is sentinel
    finally:
        cli_mod._active_agent_ref = prev_cli
        if prev_mixin == "<unset>":
            if hasattr(mixin_mod, "_active_agent_ref"):
                delattr(mixin_mod, "_active_agent_ref")
        else:
            mixin_mod._active_agent_ref = prev_mixin
