"""Compatibility shim: use ``regex`` when available, fall back to stdlib ``re``.

Usage:
    from agent.re_compat import re
    match = re.search(pattern, text)
    replaced = re.sub(pattern, repl, text)
"""

import os as _os

# Allow opt-out via environment variable
_disable = _os.environ.get("HERMES_DISABLE_REGEX_REPLACEMENT")

if not _disable:
    try:
        import regex as _re_impl
    except ImportError:
        import re as _re_impl
else:
    import re as _re_impl

# Expose the module so ``from agent.re_compat import re`` gives callers
# the accelerated (or fallback) re module.
re = _re_impl
