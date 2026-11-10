"""Reject newly introduced nonstandard JSON constants before a file write."""
from __future__ import annotations

import json
from typing import Optional

from tools.file_operations_common import WriteResult, _strip_bom

def _json_nonstandard_constant(text: str) -> Optional[str]:
    """First NaN/Infinity/-Infinity in ``text`` when it is otherwise valid JSON,
    else None. ``json.loads`` accepts these JavaScript extensions by default."""
    if "NaN" not in text and "Infinity" not in text:
        return None
    found: list[str] = []

    def note_constant(value: str) -> float:
        found.append(value)
        return float("nan")

    try:
        json.loads(_strip_bom(text)[0], parse_constant=note_constant)
    except (ValueError, RecursionError):  # unparseable text has no constant to report
        return None
    return found[0] if found else None



def _refuse_introduced_json_constant(path: str, content: str,
                                     pre_content: Optional[str]) -> Optional[WriteResult]:
    """Refuse a JSON write that INTRODUCES a nonstandard constant (strict JSON
    consumers reject them). A file that already holds one keeps accepting
    unrelated edits, which is why the lenient syntax gate can't do this check."""
    constant = _json_nonstandard_constant(content)
    if constant is None:
        return None
    if pre_content is not None and _json_nonstandard_constant(pre_content) is not None:
        return None
    return WriteResult(error=(
        f"Refusing to write '{path}': candidate content uses {constant}, which is "
        "not valid JSON. The file was NOT created or modified. Use null or a "
        "string instead and retry."))
