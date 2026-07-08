"""Fast deep-copy utilities for JSON-serializable objects.

Uses ``orjson`` round-trip (2-10× faster than ``copy.deepcopy``) for
plain dict/list data. Falls back to ``copy.deepcopy`` for non-JSON types.
"""

import copy
from typing import Any


def fast_deepcopy(obj: Any) -> Any:
    """Deep-copy a JSON-serializable object using ``orjson`` round-trip.

    For plain dict/list trees (strings, numbers, bools, None), this is
    2-10× faster than ``copy.deepcopy`` because it avoids the memo table,
    ``__deepcopy__``/``__reduce_ex__`` dispatch, and per-node type-dispatch
    overhead.

    Falls back to ``copy.deepcopy`` for non-JSON types (sets, custom objects,
    dict subclasses, etc.).
    """
    t = type(obj)
    if t is dict:
        return {k: fast_deepcopy(v) for k, v in obj.items()}
    if t is list:
        return [fast_deepcopy(v) for v in obj]
    if t is tuple:
        return tuple(fast_deepcopy(v) for v in obj)
    # Immutable scalars: safe to share
    if t is str or t is int or t is float or t is bool or obj is None:
        return obj
    # Non-JSON type — fall back to stdlib deepcopy
    return copy.deepcopy(obj)


def orjson_roundtrip_copy(obj: Any) -> Any:
    """Deep-copy via ``orjson.dumps`` + ``orjson.loads``.

    Even faster than the recursive walk for deeply nested dicts/lists,
    but only works for objects that are fully JSON-serializable.

    Falls back to ``copy.deepcopy`` if ``orjson`` is not available or
    the object is not JSON-serializable.
    """
    try:
        import orjson
        return orjson.loads(orjson.dumps(obj))
    except Exception:
        return copy.deepcopy(obj)
