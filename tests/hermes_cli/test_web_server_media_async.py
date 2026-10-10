"""Dashboard media reads must not run on the event-loop thread."""

import threading
from pathlib import Path

import pytest
from starlette.requests import Request

from hermes_cli.web_routers import files


def _dashboard_request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/media",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
    })


@pytest.mark.asyncio
async def test_get_media_reads_and_encodes_off_event_loop(monkeypatch, tmp_path):
    """The bounded media payload is opened and read from a worker thread."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image = image_dir / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage-data")

    # Production reads the home late (``hermes_cli.config.get_hermes_home``) from
    # the media-roots resolver, so that is the seam to pin.
    monkeypatch.setattr("hermes_cli.config.get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(files, "get_hermes_home", lambda: tmp_path)
    # The fork's policy path opens the file, binds every check to that handle and
    # reads through it (never ``Path.read_bytes``), so the seam that proves the
    # payload work left the loop is the open that feeds the read.
    original_open = Path.open
    open_threads = []

    def _tracking_open(path, *args, **kwargs):
        if Path(path) == image:
            open_threads.append(threading.current_thread())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _tracking_open)
    event_loop_thread = threading.current_thread()

    response = await files.get_media(_dashboard_request(), str(image))

    assert response["data_url"].startswith("data:image/png;base64,")
    assert open_threads
    assert all(thread is not event_loop_thread for thread in open_threads)
