"""shell_compat — the lossless tolerant-IR Git Bash compatibility package.

This package is the implementation behind the stable ``tools/environments/
bash_fix.py`` facade.  ``bash_fix.py`` holds no scanner logic: it re-exports the
historical module-level surface (explicit imports, no ``*``) and applies the
Windows-platform gate, while every rule lives HERE — one module, one
responsibility:

  models / spans      lossless IR node types and source-coordinate primitives
                      (spans, edits, diagnostics); nodes reference the original
                      string, nothing is copied or re-normalized.
  lexer               shared O(n) tokenizer + quote/substitution boundary oracles.
  parser              tolerant command-context IR builder; unrecognized regions
                      stay ``RawNode`` and it never raises on malformed input.
  fallback_data       guarded fallback command bodies / unsupported reasons /
                      runtime delegates and their helper constructors.
  paths               Windows path, null-device (``nul``) and ``cd /d`` rules.
  wrappers            compatibility classification, fallback registration,
                      wrapper-operand, runtime-reparse and shell-unwrap rules.
  cmd_translator      cmd.exe ``@`` / ``if`` / ``for`` translations and the
                      jump refusals.
  heredoc             heredoc trailing-operator repair plus the IR event model.
  translator          the ordered Git Bash compatibility translator: the single
                      ``translate`` orchestration entry, the ``EditPlan`` render
                      contract and the fallback prefix.
  ir_command          the genuine per-command IR decision engine (the dispatcher
                      slots 4-8 byte-equality proof).
  bash_core           BashFix, _BashFixScanner (the compatibility engine reader)
                      and fix_bash_command, plus the remaining Bash constants
                      (the ``_process_unquoted`` emitter is canonical in
                      ``unquoted``; bash_core keeps thin delegates so the facade
                      and intra-module callers resolve unchanged).
  unquoted            ``_process_unquoted`` lexer-driven emitter.

Conservative pass-through policy (the whole package): an already Git Bash
compatible command passes through BYTE-FOR-BYTE — no fallback definitions, no
reordering, no unrelated rewrite, and the unchanged result is the ORIGINAL
string object, not a re-rendered copy.  Only verified compatibility cases
produce an edit or a runtime-guarded (``command -v``) fallback definition;
malformed, adversarial or deeply nested input is passed through for Bash to
diagnose instead of being repaired, and unsupported commands keep their source
and flow through the ``unsupported`` refusal channel.

Consumers import ``tools.environments.bash_fix`` (the stable ``bash_fix`` facade); this
``__init__`` re-exports the public entry points for convenience and is
intentionally NOT the full historical surface.
"""

from __future__ import annotations

from .bash_core import (  # noqa: F401
    BashFix,
    _apply_heredoc_operator_move,
    _BashFixScanner,
    _BashHereDoc,
    _BashWrapper,
    _fix_heredoc_trailing_operators,
    _process_unquoted,
    _read_shell_control_operator,
    bash_compatibility_prelude,
    fix_bash_command,
)
from .fallback_data import (  # noqa: F401
    _FALLBACK_BODIES,
    _FALLBACK_COMMAND_WRAPPERS,
    _FALLBACKS,
    _UNSUPPORTED_BODIES,
    _fallback_definition,
    _single_quote,
    _wrapper_runner,
)
