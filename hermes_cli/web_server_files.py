"""Managed-files policy for the dashboard file browser: root resolution, path containment, entry metadata.
"""

import mimetypes
import os
import stat
import urllib.request
from dataclasses import dataclass
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pathlib import Path
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator

# NOTE: the media helpers below import ``hermes_cli.config.get_hermes_home`` per call rather than
# binding it as a module attribute; a module-level alias here duplicates a name the facade
# (``hermes_cli.web_server``) lazily re-exports, and the compat-manifest test requires the facade
# attribute to BE this sibling's bind.


_MANAGED_FILES_ROOT_ENV = "HERMES_DASHBOARD_FILES_ROOT"
_HOSTED_MANAGED_FILES_ROOT = Path("/opt/data")


@dataclass(frozen=True)
class ManagedFilesPolicy:
    default_path: Path
    locked_root: Path | None
    can_change_path: bool


def _fs_path(raw_path: str, *, cwd: str | None = None) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Path is required")
    if "\0" in raw:
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        if raw.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(raw)
            uri_path = parsed.path
            if parsed.netloc and parsed.netloc.lower() != "localhost":
                if os.name != "nt":
                    raise ValueError
                uri_path = f"//{parsed.netloc}{uri_path}"
            raw = urllib.request.url2pathname(uri_path)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            base = Path(cwd).expanduser() if cwd is not None else Path.cwd()
            if not base.is_absolute():
                raise HTTPException(status_code=400, detail="Session working directory is unavailable")
            candidate = base / candidate
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _canonical_path(path: Path, *, require_exists: bool = False) -> Path:
    try:
        return path.expanduser().resolve(strict=require_exists)
    except FileNotFoundError:
        if require_exists:
            raise HTTPException(status_code=404, detail="Path not found")
        raise
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _ensure_managed_root(raw_path: str | Path) -> Path:
    root = Path(raw_path).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"Managed files root is unavailable: {exc}")
    if not resolved.is_dir():
        raise HTTPException(status_code=500, detail="Managed files root is not a directory")
    return resolved


def _path_is_under(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _path_text(raw_path: str | None) -> str:
    text = str(raw_path or "").strip()
    if "\x00" in text:
        raise HTTPException(status_code=400, detail="Invalid path")
    return text


def _default_hermes_root_is_opt_data() -> bool:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if not raw:
        return False
    try:
        from hermes_constants import get_default_hermes_root

        root = get_default_hermes_root().expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        root = Path(raw).expanduser().resolve(strict=False)
    return root == _HOSTED_MANAGED_FILES_ROOT


def _dashboard_local_update_managed_externally() -> bool:
    """True when the dashboard should not offer ``hermes update``.

    Containerized dashboards are updated by the outer launcher/image — except a
    ``git`` install (bind-mounted checkout, e.g. the hermes-webui image), where
    the update button is the correct path. pip stays blocked in containers: its
    apply path mutates the running container filesystem.
    """
    from hermes_cli.web_server import PROJECT_ROOT
    from hermes_cli.config import detect_install_method
    if _default_hermes_root_is_opt_data():
        return True
    try:
        from hermes_constants import is_container

        if not is_container():
            return False
    except Exception:
        return False
    try:
        if detect_install_method(PROJECT_ROOT) == "git":
            return False
    except Exception:
        pass
    return True


def _managed_files_policy(request: Request, *, create_root: bool = True) -> ManagedFilesPolicy:
    raw_forced_root = os.environ.get(_MANAGED_FILES_ROOT_ENV, "").strip()
    if raw_forced_root:
        root = _ensure_managed_root(raw_forced_root) if create_root else _canonical_path(Path(raw_forced_root))
        return ManagedFilesPolicy(default_path=root, locked_root=root, can_change_path=False)

    # Remote/OAuth access does not imply a hosted container (a gated macOS launchd
    # install still browses its home). Lock to /opt/data only when the Hermes
    # root actually IS /opt/data or HERMES_DASHBOARD_FILES_ROOT is set.
    if _default_hermes_root_is_opt_data():
        root = _ensure_managed_root(_HOSTED_MANAGED_FILES_ROOT) if create_root else _HOSTED_MANAGED_FILES_ROOT
        return ManagedFilesPolicy(default_path=root, locked_root=root, can_change_path=False)

    home = _canonical_path(Path.home())
    return ManagedFilesPolicy(default_path=home, locked_root=None, can_change_path=True)


def _resolve_managed_path(
    raw_path: str | None, request: Request, *, for_write: bool = False
) -> tuple[ManagedFilesPolicy, Path, str]:
    policy = _managed_files_policy(request)
    text = _path_text(raw_path)
    root = policy.locked_root

    if root is not None and (not text or text in {".", "/"}):
        candidate = root
    elif not text:
        candidate = policy.default_path
    else:
        candidate = Path(text).expanduser()
        if root is not None and not candidate.is_absolute():
            if any(part == ".." for part in candidate.parts):
                raise HTTPException(status_code=400, detail="Path cannot contain '..'")
            candidate = root / candidate
        elif not candidate.is_absolute():
            raise HTTPException(status_code=400, detail="Path must be absolute")

    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="Path cannot contain '..'")

    if for_write and not candidate.exists():
        parent = _canonical_path(candidate.parent)
        resolved = parent / candidate.name
    else:
        resolved = _canonical_path(candidate, require_exists=not for_write)

    if root is not None and not _path_is_under(root, resolved):
        raise HTTPException(status_code=403, detail="Path outside managed files root")

    return policy, resolved, str(resolved)


def _managed_response_meta(policy: ManagedFilesPolicy) -> Dict[str, Any]:
    locked_root = str(policy.locked_root) if policy.locked_root is not None else None
    return {"root": locked_root, "locked_root": locked_root, "can_change_path": policy.can_change_path}


def _managed_file_entry(policy: ManagedFilesPolicy, target: Path) -> Dict[str, Any]:
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")
    if policy.locked_root is not None and not _path_is_under(policy.locked_root, resolved):
        raise HTTPException(status_code=403, detail="Path outside managed files root")

    try:
        st = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not stat path: {exc}")

    is_dir = resolved.is_dir()
    mime_type = None if is_dir else (mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
    return {
        "name": target.name or resolved.name or str(resolved),
        "path": str(resolved),
        "is_directory": is_dir,
        "size": None if is_dir else st.st_size,
        "mtime": st.st_mtime,
        "mime_type": mime_type,
    }


# ── GET /api/media + GET /api/media/file ([CN-fork] P-059) ───────────────────
# Media MIME types these endpoints will serve. Extension-allowlisted so an
# authenticated caller can't pull non-media files through them.
_IMAGE_MEDIA_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}
_VIDEO_MEDIA_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".m4v": "video/x-m4v",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".ogv": "video/ogg",
}
_MEDIA_CONTENT_TYPES = {
    **_IMAGE_MEDIA_CONTENT_TYPES,
    **_VIDEO_MEDIA_CONTENT_TYPES,
}
_IMAGE_MEDIA_MAX_BYTES = 25 * 1024 * 1024
# The compatibility endpoint buffers and base64-encodes the entire file across
# Python, Rust/Tauri IPC, and the webview. Keep video fallback payloads small;
# larger videos use the range-capable streaming endpoint instead.
_VIDEO_MEDIA_MAX_BYTES = 25 * 1024 * 1024
_VIDEO_MEDIA_STREAM_MAX_BYTES = 4 * 1024 * 1024 * 1024
_VIDEO_MEDIA_STREAM_CHUNK_BYTES = 64 * 1024


def _media_root_candidates() -> list[Path]:
    """Return configured media roots without following filesystem links."""
    # Imported per call (not module-level, and not a module alias): a module-level bind here puts a
    # second, lazily-resolved ``get_hermes_home`` into this sibling, which the compat-manifest
    # identity test flags as a facade/sibling mismatch, while the per-call import still honours a
    # test's monkeypatch of the owning module.
    from hermes_cli.config import get_hermes_home

    home = get_hermes_home()
    return [home / "images", home / "screenshots", home / "cache"]


def _lexical_media_roots() -> list[Path]:
    """Return absolute media roots without resolving symlinks or junctions."""
    out: list[Path] = []
    for root in _media_root_candidates():
        try:
            out.append(Path(os.path.abspath(os.path.normpath(str(root)))))
        except (OSError, RuntimeError, ValueError):
            continue
    return out


def _media_serve_roots() -> list[Path]:
    """Directories ``GET /api/media`` trusts without further validation.

    Confined to where the agent and attach pipeline actually write media on the
    gateway host — its images dir and cache subtree. Paths outside these roots
    must pass the shared gateway media-delivery policy before they are served.
    """
    try:
        from hermes_cli.config import get_hermes_home

        canonical_home = get_hermes_home().expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    # Do not resolve the child roots here. A cache/images junction must be
    # rejected component-by-component before anything follows its target.
    return [canonical_home / root.name for root in _media_root_candidates()]


def _unsafe_media_path_syntax(path: str) -> bool:
    """Reject Windows network/device namespaces before filesystem access."""
    windows_path = path.replace("/", "\\")
    if windows_path.startswith("\\\\") or windows_path.startswith("\\??\\"):
        return True
    if os.name == "nt":
        # Disallow alternate data streams while preserving a normal drive
        # prefix such as C:\\.
        if len(windows_path) >= 2 and windows_path[1] == ":":
            return not windows_path[0].isalpha() or ":" in windows_path[2:]
        return ":" in windows_path
    return False


def _path_within_media_roots(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _reject_media_link_components(path: Path, base: Path) -> None:
    """Reject links/reparse points below ``base`` before resolving ``path``."""
    try:
        relative = path.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    current = base
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in relative.parts:
        current /= component
        try:
            component_stat = current.lstat()
        except FileNotFoundError:
            # Strict resolution below produces the endpoint's normal 404.
            return
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid path")

        is_reparse_point = bool(
            reparse_flag
            and getattr(component_stat, "st_file_attributes", 0) & reparse_flag
        )
        if stat.S_ISLNK(component_stat.st_mode) or is_reparse_point:
            raise HTTPException(status_code=403, detail="Media links are not allowed")


def _local_dashboard_request(request: Request) -> bool:
    if getattr(request.app.state, "auth_required", False):
        return False
    # A reverse proxy commonly makes the TCP peer look loopback-local. External
    # media expansion is a desktop-only privilege, so never grant it to a
    # forwarded request even when both the rewritten Host and peer are local.
    forwarded_headers = ("forwarded", "x-forwarded-for", "x-real-ip")
    headers = getattr(request, "headers", {})
    if any(headers.get(name, "").strip() for name in forwarded_headers):
        return False
    host = (request.url.hostname or "").lower()
    client_host = (request.client.host if request.client else "").lower()
    if not host or not client_host:
        return False
    local_hosts = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}
    return host in local_hosts and client_host in local_hosts


def _resolve_media_path(
    path: str,
    request: Request,
    *,
    video_only: bool = False,
) -> tuple[Path, str, bool]:
    """Resolve a supported media path and enforce the shared egress policy."""
    candidate = _path_text(path)
    if not candidate or _unsafe_media_path_syntax(candidate):
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        candidate_path = Path(candidate).expanduser()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not candidate_path.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid path")

    content_types = _VIDEO_MEDIA_CONTENT_TYPES if video_only else _MEDIA_CONTENT_TYPES
    if candidate_path.suffix.lower() not in content_types:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    try:
        lexical_target = Path(os.path.abspath(os.path.normpath(str(candidate_path))))
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")

    lexical_roots = _lexical_media_roots()
    canonical_roots = _media_serve_roots()
    root_match: tuple[Path, Path] | None = None
    for lexical_root, canonical_root in zip(lexical_roots, canonical_roots):
        if _path_within_media_roots(lexical_target, [lexical_root]):
            root_match = (lexical_root.parent, canonical_root)
            break
        if _path_within_media_roots(lexical_target, [canonical_root]):
            root_match = (canonical_root.parent, canonical_root)
            break

    external_allowed = (
        os.getenv("HERMES_DESKTOP") == "1" and _local_dashboard_request(request)
    )
    if root_match is None and not external_allowed:
        # Fail before resolve/stat so a remote caller cannot use response codes
        # as a file-existence oracle or trigger UNC/device access.
        raise HTTPException(status_code=403, detail="Media path is not allowed")

    if root_match is not None:
        trusted_home, canonical_root = root_match
        # HERMES_HOME itself may be a deliberate link, but no component below
        # it may redirect resolution outside the configured media tree.
        _reject_media_link_components(lexical_target, trusted_home)
    else:
        if not lexical_target.anchor:
            raise HTTPException(status_code=400, detail="Invalid path")
        _reject_media_link_components(
            lexical_target,
            Path(lexical_target.anchor),
        )

    try:
        target = candidate_path.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")

    if root_match is not None:
        if not _path_within_media_roots(target, [canonical_root]):
            raise HTTPException(status_code=403, detail="Media path is not allowed")
    else:
        # MEDIA: delivery already has the denylist/strict-mode policy needed
        # for user-selected paths such as Desktop files. Reuse it here so the
        # desktop relay and messaging gateways make the same trust decision.
        from gateway.platforms.base import validate_media_delivery_path

        validated = validate_media_delivery_path(str(target))
        if validated is None or Path(validated) != target:
            raise HTTPException(status_code=403, detail="Media path is not allowed")

    suffix = target.suffix.lower()
    content_type = content_types.get(suffix)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Unsupported media type")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return target, content_type, suffix in _VIDEO_MEDIA_CONTENT_TYPES


def _open_media_file(
    path: str,
    request: Request,
    *,
    video_only: bool = False,
) -> tuple[BinaryIO, Path, str, bool, int]:
    """Open a validated media file once and bind checks to that handle."""
    target, content_type, is_video = _resolve_media_path(
        path,
        request,
        video_only=video_only,
    )
    try:
        handle = target.open("rb")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Media path is not allowed")
    except OSError:
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        opened_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened_stat.st_mode):
            raise HTTPException(status_code=404, detail="File not found")
        if opened_stat.st_nlink > 1:
            raise HTTPException(status_code=403, detail="Hard-linked media is not allowed")

        # The canonical path must still identify the same file we opened. This
        # catches a symlink/junction or rename swap between policy validation
        # and open while keeping the response bound to one file descriptor.
        current = target.resolve(strict=True)
        current_stat = current.stat()
        if current != target or not os.path.samestat(opened_stat, current_stat):
            raise HTTPException(status_code=403, detail="Media path changed")
    except FileNotFoundError:
        handle.close()
        raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        handle.close()
        raise
    except (OSError, RuntimeError, ValueError):
        handle.close()
        raise HTTPException(status_code=400, detail="Invalid path")

    return handle, target, content_type, is_video, opened_stat.st_size


def _parse_media_range(range_header: str, size: int) -> tuple[int, int, bool]:
    """Parse one RFC 7233 byte range as ``(start, length, partial)``."""
    if not range_header:
        return 0, size, False

    unit, separator, value = range_header.partition("=")
    value = value.strip()
    if unit.strip().lower() != "bytes" or not separator or not value or "," in value:
        raise HTTPException(
            status_code=416,
            detail="Invalid range",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    start_text, dash, end_text = value.partition("-")
    if (
        not dash
        or (
            start_text
            and (not start_text.isascii() or not start_text.isdecimal())
        )
        or (
            end_text
            and (not end_text.isascii() or not end_text.isdecimal())
        )
        or len(start_text) > 20
        or len(end_text) > 20
    ):
        raise HTTPException(
            status_code=416,
            detail="Invalid range",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    if not start_text:
        suffix_length = int(end_text or "0")
        if suffix_length <= 0 or size <= 0:
            raise HTTPException(
                status_code=416,
                detail="Range not satisfiable",
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        length = min(suffix_length, size)
        return size - length, length, True

    start = int(start_text)
    if start >= size:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    end = size - 1 if not end_text else min(int(end_text), size - 1)
    if end < start:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    return start, end - start + 1, True


def _iter_media_file(handle: BinaryIO, start: int, length: int) -> Iterator[bytes]:
    """Yield exactly the selected bytes and always close the open handle."""
    try:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(_VIDEO_MEDIA_STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


class _ClosingStreamingResponse(StreamingResponse):
    """Close a bound file even when Starlette aborts on client disconnect."""

    def __init__(self, *args: Any, close_handle: BinaryIO, **kwargs: Any) -> None:
        self._close_handle = close_handle
        super().__init__(*args, **kwargs)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._close_handle.close()
