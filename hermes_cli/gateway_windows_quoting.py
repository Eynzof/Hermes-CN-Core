"""Quoting for the distinct cmd.exe and VBScript launcher parsers."""

import re

def _quote_cmd_script_arg(value: str) -> str:
    """Quote one argument INSIDE a .cmd file for cmd.exe: split on spaces/tabs outside double quotes,
    embedded quotes doubled. Line breaks are refused — they'd end the logical command line."""
    if "\r" in value or "\n" in value:
        raise ValueError(f"refusing to quote value containing newline: {value!r}")
    if not value:
        return '""'
    if not re.search(r'[ \t"]', value):
        return value
    return '"' + value.replace('"', '""') + '"'


def _quote_vbs_string(value: str) -> str:
    """VBScript double-quoted literal (embedded quote doubled; newline refused)."""
    if "\r" in value or "\n" in value:
        raise ValueError(f"refusing to quote VBScript value containing newline: {value!r}")
    return '"' + value.replace('"', '""') + '"'
