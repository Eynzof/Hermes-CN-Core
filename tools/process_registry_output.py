"""Export the process registry's captured output to a requested file."""

import logging
from pathlib import Path

logger = logging.getLogger("tools.process_registry")


def export_output(
    session, text: str, output_path: str
) -> str | None:
    """Write ``text`` to ``output_path`` (or a session temp file for "auto").

    Returns the resolved path, or None on failure (never raises — log
    retrieval must keep working even when disk export fails).
    """
    try:
        if not output_path or output_path.lower() in ("auto", ""):
            from tools.terminal_post_process import _get_session_temp_dir

            tmp_dir = _get_session_temp_dir()
            out_path = tmp_dir / f"{session.id}_output.txt"
        else:
            out_path = Path(output_path)
            if out_path.is_dir():
                out_path = out_path / f"{session.id}_output.txt"
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        return str(out_path)
    except OSError as e:
        logger.debug("Output export failed for %s: %s", session.id, e)
        return None
