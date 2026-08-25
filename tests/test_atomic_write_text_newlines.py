"""Cross-platform line-ending guarantees for ``atomic_write_text``."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import atomic_write_text


@pytest.mark.parametrize(
    "content",
    [
        "alpha\nbeta\n",
        "alpha\r\nbeta\r\n",
        "alpha\rbeta\r",
        "标题\r\n正文\n结尾",
    ],
)
def test_atomic_write_text_preserves_caller_line_endings(
    tmp_path: Path, content: str
) -> None:
    target = tmp_path / "SOUL.md"

    atomic_write_text(target, content)

    assert target.read_bytes() == content.encode("utf-8")
