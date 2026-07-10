"""Compatibility shim: use ``regex`` when available, fall back to stdlib ``re``.

Usage:
    from agent.re_compat import re
    match = re.search(pattern, text)
    replaced = re.sub(pattern, repl, text)
"""

import os as _os

# The third-party ``regex`` engine is NOT a perfect drop-in for stdlib ``re``:
# e.g. the gateway provider-error shape pattern (^\s*(\W*\s*)?(...)) matches
# "⚠️ Provider authentication failed..." under stdlib re but NOT under
# ``regex`` — which silently disabled the chat-surface error sanitizer and let
# partially-redacted provider errors leak to users. Correctness first: default
# to stdlib ``re``; the accelerated engine is opt-in for benchmarking via
# HERMES_ENABLE_REGEX_REPLACEMENT=1.
_enable = _os.environ.get("HERMES_ENABLE_REGEX_REPLACEMENT")

if _enable:
    try:
        import regex as _re_impl
    except ImportError:
        import re as _re_impl
else:
    import re as _re_impl

# Expose the module so ``from agent.re_compat import re`` gives callers
# the accelerated (or fallback) re module.
re = _re_impl
