"""Mocked unit tests for the Moss MOSS-VL tool + vision HTTP layer.

No network — ``upload_file`` / ``understand`` / ``extract_vision_text``
are monkeypatched; the real ``_media_content`` / ``validate_public_url``
are exercised directly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.tts.moss import api as moss_api
from plugins.tts.moss import tools as moss_tools
from plugins.tts.moss.api import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    MAX_VIDEO_BYTES,
    MossApiError,
    extract_vision_text,
)


@pytest.fixture
def fake_vision(monkeypatch):
    """Patch the vision seams the tool handler uses."""
    state = {
        "uploads": [],
        "understands": [],
        "extract_text": "照片里有一棵树。",
        "response": {
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": "照片里有一棵树。"}]}],
        },
    }

    def fake_upload(local_path, purpose="audio"):
        state["uploads"].append((local_path, purpose))
        return f"file-{len(state['uploads'])}"

    def fake_understand(instruction, **kw):
        state["understands"].append(kw)
        return state["response"]

    monkeypatch.setattr(moss_tools, "upload_file", fake_upload)
    monkeypatch.setattr(moss_tools, "understand", fake_understand)
    monkeypatch.setattr(moss_tools, "extract_vision_text", lambda d: state["extract_text"])
    return state


def _img(tmp_path: Path, name: str = "a.png") -> str:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return str(p)


def _video(tmp_path: Path, name: str = "v.mp4") -> str:
    p = tmp_path / name
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    return str(p)


def _load(payload: str) -> dict:
    return json.loads(payload)


# ---------------------------------------------------------------------------
# Handler behavior
# ---------------------------------------------------------------------------


class TestVisionHandler:
    def test_instruction_required(self, fake_vision):
        result = _load(moss_tools._handle_moss_vision({"images": ["x.png"]}))
        assert result["success"] is False
        assert "instruction" in result["error"]

    def test_no_media_rejected(self, fake_vision):
        result = _load(moss_tools._handle_moss_vision({"instruction": "描述图片"}))
        assert result["success"] is False
        assert "at least one" in result["error"]

    def test_single_local_image_upload_flow(self, fake_vision, tmp_path):
        img = _img(tmp_path)
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "描述图片", "images": [img],
        }))
        assert result["success"] is True
        assert result["text"] == "照片里有一棵树。"
        assert result["status"] == "completed"
        assert fake_vision["uploads"] == [(img, "image")]
        kw = fake_vision["understands"][0]
        assert kw["image_file_ids"] == ["file-1"]
        assert kw["image_urls"] == []

    def test_multi_image_upload(self, fake_vision, tmp_path):
        imgs = [_img(tmp_path, f"i{n}.png") for n in range(3)]
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "对比图片", "images": imgs,
        }))
        assert result["success"] is True
        assert [p for p, _ in fake_vision["uploads"]] == imgs
        assert fake_vision["understands"][0]["image_file_ids"] == ["file-1", "file-2", "file-3"]

    def test_image_url_passthrough(self, fake_vision):
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "OCR", "image_urls": ["https://example.com/a.png"],
        }))
        assert result["success"] is True
        kw = fake_vision["understands"][0]
        assert kw["image_urls"] == ["https://example.com/a.png"]
        assert kw["image_file_ids"] == []
        assert fake_vision["uploads"] == []

    def test_image_file_id_passthrough(self, fake_vision):
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "OCR", "images": ["file_id:img-1"],
        }))
        assert result["success"] is True
        assert fake_vision["understands"][0]["image_file_ids"] == ["img-1"]
        assert fake_vision["uploads"] == []

    def test_video_only_local(self, fake_vision, tmp_path):
        video = _video(tmp_path)
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "总结视频", "video": video,
        }))
        assert result["success"] is True
        assert fake_vision["uploads"] == [(video, "video")]
        kw = fake_vision["understands"][0]
        assert kw["video_file_id"] == "file-1"
        assert kw["video_url"] is None

    def test_video_url(self, fake_vision):
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "总结视频",
            "video": "https://example.com/v.mp4",
        }))
        assert result["success"] is True
        kw = fake_vision["understands"][0]
        assert kw["video_url"] == "https://example.com/v.mp4"
        assert kw["video_file_id"] is None
        assert fake_vision["uploads"] == []

    def test_mixing_images_and_video_rejected(self, fake_vision, tmp_path):
        img = _img(tmp_path)
        video = _video(tmp_path)
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "images": [img], "video": video,
        }))
        assert result["success"] is False
        assert "mixing" in result["error"]

    def test_mixing_image_url_and_video_rejected(self, fake_vision):
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x",
            "image_urls": ["https://example.com/a.png"],
            "video": "https://example.com/v.mp4",
        }))
        assert result["success"] is False
        assert "mixing" in result["error"]

    def test_too_many_images_rejected(self, fake_vision, tmp_path):
        imgs = [_img(tmp_path, f"i{n}.png") for n in range(MAX_IMAGES + 1)]
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "images": imgs,
        }))
        assert result["success"] is False
        assert "at most 5" in result["error"]

    def test_video_and_video_url_conflict(self, fake_vision, tmp_path):
        video = _video(tmp_path)
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x",
            "video": video,
            "video_url": "https://example.com/v.mp4",
        }))
        assert result["success"] is False
        assert "both" in result["error"]

    def test_missing_image_file(self, fake_vision, tmp_path):
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "images": ["/nonexistent/a.png"],
        }))
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_image_too_large_rejected(self, fake_vision, tmp_path, monkeypatch):
        img = _img(tmp_path)
        # Fake a huge file size without allocating bytes.
        monkeypatch.setattr(
            "plugins.tts.moss.tools.Path.stat",
            lambda self: type("S", (), {"st_size": MAX_IMAGE_BYTES + 1})(),
        )
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "images": [img],
        }))
        assert result["success"] is False
        assert "too large" in result["error"]

    def test_video_too_large_rejected(self, fake_vision, tmp_path, monkeypatch):
        video = _video(tmp_path)
        monkeypatch.setattr(
            "plugins.tts.moss.tools.Path.stat",
            lambda self: type("S", (), {"st_size": MAX_VIDEO_BYTES + 1})(),
        )
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "video": video,
        }))
        assert result["success"] is False
        assert "too large" in result["error"]

    def test_incomplete_max_output_tokens_warning(self, fake_vision):
        fake_vision["response"] = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"content": [{"type": "output_text", "text": "部分内容"}]}],
        }
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "images": ["https://example.com/a.png"],
        }))
        assert result["success"] is True
        assert result["status"] == "incomplete"
        assert "max_output_tokens" in result["warning"]

    def test_understand_error_envelope(self, fake_vision, monkeypatch):
        def boom(instruction, **kw):
            raise MossApiError("HTTP 400 invalid_request_error")

        monkeypatch.setattr(moss_tools, "understand", boom)
        result = _load(moss_tools._handle_moss_vision({
            "instruction": "x", "images": ["https://example.com/a.png"],
        }))
        assert result["success"] is False
        assert "HTTP 400" in result["error"]


# ---------------------------------------------------------------------------
# _media_content (real builder, no network)
# ---------------------------------------------------------------------------


class TestMediaContent:
    def test_images_only(self):
        items = moss_api._media_content(["https://e.com/a.png"], ["file-1"], None, None)
        assert items == [
            {"type": "input_image", "image_url": "https://e.com/a.png"},
            {"type": "input_image", "file_id": "file-1"},
        ]

    def test_video_only(self):
        items = moss_api._media_content([], [], "https://e.com/v.mp4", None)
        assert items == [{"type": "input_video", "video_url": "https://e.com/v.mp4"}]

    def test_video_file_id(self):
        items = moss_api._media_content([], [], None, "file-v")
        assert items == [{"type": "input_video", "file_id": "file-v"}]

    def test_no_media_rejected(self):
        with pytest.raises(MossApiError, match="at least one"):
            moss_api._media_content([], [], None, None)

    def test_mixing_rejected(self):
        with pytest.raises(MossApiError, match="mixing"):
            moss_api._media_content(["https://e.com/a.png"], [], "https://e.com/v.mp4", None)

    def test_too_many_images_rejected(self):
        with pytest.raises(MossApiError, match="at most 5"):
            moss_api._media_content(["https://e.com/a.png"] * (MAX_IMAGES + 1), [], None, None)

    def test_private_url_rejected(self):
        with pytest.raises(MossApiError, match="public"):
            moss_api._media_content(["http://localhost/a.png"], [], None, None)


# ---------------------------------------------------------------------------
# extract_vision_text
# ---------------------------------------------------------------------------


class TestExtractVisionText:
    def test_walks_output_content(self):
        data = {
            "output": [
                {"content": [{"type": "output_text", "text": "第一行"}]},
                {"content": [{"type": "output_text", "text": " 第二行 "}]},
                {"content": [{"type": "output_text", "text": ""}]},
            ]
        }
        assert extract_vision_text(data) == "第一行\n第二行"

    def test_ignores_non_text_content(self):
        data = {
            "output": [
                {"content": [{"type": "input_image", "file_id": "f1"}, {"text": "only this"}]},
                {"content": []},
            ]
        }
        assert extract_vision_text(data) == "only this"

    def test_empty_when_no_text(self):
        assert extract_vision_text({"output": []}) == ""
        assert extract_vision_text({}) == ""
