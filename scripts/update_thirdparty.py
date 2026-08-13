#!/usr/bin/env python3
"""Check and update third-party binary version strings in the project.

This script is the Hermes-CN counterpart of upstream
``kimi-agent/scripts/update_thirdparty.py``.  It knows that Hermes-CN pins
ripgrep and rtk versions in several file formats (Bash, PowerShell, Python,
YAML) and updates all of them consistently.

For each known tool:

1. Fetches the latest release tag from GitHub (API first, then redirect
   fallback, then Chinese mirror fallback when enabled).
2. Compares it to the version currently declared in the source code.
3. If a newer version exists, updates the pinned version strings and the
   CI SHA256 checksum for ripgrep.

Usage:
    python scripts/update_thirdparty.py                  # interactive
    python scripts/update_thirdparty.py --yes            # auto-approve
    python scripts/update_thirdparty.py --check-only     # report only
    python scripts/update_thirdparty.py --china-mirror   # mirror fallback
    python scripts/update_thirdparty.py --skip-shasum    # do not recompute SHA256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

# ── Project root ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Mirror configuration ──────────────────────────────────────────────────────
_DEFAULT_CHINA_MIRRORS = [
    "https://ghproxy.com/",
    "https://ghproxy.net/",
    "https://mirror.ghproxy.com/",
    "https://ghps.cc/",
    "https://gh.ddlc.top/",
]

# ── Data model ────────────────────────────────────────────────────────────────
@dataclass
class FileUpdateRule:
    """A single file+regex where a version string must be kept in sync."""

    path: str
    pattern: str
    group: str = "ver"


@dataclass
class ThirdPartyTool:
    """Describes a third-party tool whose version is pinned in source."""

    name: str
    github_repo: str
    primary_file: str
    primary_pattern: str
    version_var: str
    version_clean: Callable[[str], str] | None = None
    tag_pattern: str | None = None
    extra_files: list[FileUpdateRule] = field(default_factory=list)
    needs_sha256: bool = False
    sha256_file: str | None = None
    sha256_pattern: str | None = None
    sha256_arch: str = "x86_64-unknown-linux-musl"


# ── Tool definitions ──────────────────────────────────────────────────────────
# The primary file is the canonical source of truth for the current version;
# extra_files are kept in sync.  Patterns are anchored to avoid accidental
# matches elsewhere in the file.

TOOLS: list[ThirdPartyTool] = [
    ThirdPartyTool(
        name="ripgrep",
        github_repo="BurntSushi/ripgrep",
        primary_file="scripts/install.sh",
        primary_pattern=r'^RIPGREP_VERSION="(?P<ver>[^"]+)"',
        version_var="RIPGREP_VERSION",
        extra_files=[
            FileUpdateRule(
                "scripts/install.ps1",
                r'^\s*\$rgVersion\s*=\s*"(?P<ver>[^"]+)"',
            ),
            FileUpdateRule(
                ".github/workflows/tests.yml",
                r'^\s*RG_VERSION=(?P<ver>[\d.]+)',
            ),
        ],
        needs_sha256=True,
        sha256_file=".github/workflows/tests.yml",
        sha256_pattern=r'^\s*RG_SHA256=(?P<sha>[a-f0-9]{64})',
        sha256_arch="x86_64-unknown-linux-musl",
    ),
    ThirdPartyTool(
        name="rtk",
        github_repo="rtk-ai/rtk",
        primary_file="tools/rtk_provision.py",
        primary_pattern=r'^RTK_VERSION\s*=\s*"(?P<ver>[^"]+)"',
        version_var="RTK_VERSION",
        version_clean=lambda tag: tag.lstrip("v"),
        extra_files=[
            FileUpdateRule(
                "scripts/install.sh",
                r'^RTK_VERSION="(?P<ver>[^"]+)"',
            ),
            FileUpdateRule(
                "scripts/install.ps1",
                r'^\s*\$rtkVersion\s*=\s*"(?P<ver>[^"]+)"',
            ),
        ],
    ),
]


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _get_github_token() -> str | None:
    """Return a GitHub token from the environment, or ``None``."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _github_api(url: str) -> dict | list | None:
    """Call a GitHub API endpoint and return the parsed JSON response."""
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "hermes-cn-thirdparty-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except Exception as exc:
        print(f"  ⚠️  GitHub API request failed: {exc}")
        return None


def _mirror_url(url: str, mirror: str) -> str:
    """Return *url* rewritten to go through *mirror*.

    Mirrors are expected to be prefix proxies that accept
    ``https://mirror/https://github.com/...`` style URLs.
    """
    if not mirror.endswith("/"):
        mirror = mirror + "/"
    return f"{mirror}{url}"


def _iter_mirrors(
    *,
    include_direct: bool = True,
    custom_mirror: str | None = None,
    include_china_defaults: bool = True,
) -> Iterator[str]:
    """Yield download/API URL prefixes to try, in order.

    Direct GitHub is tried first, then an explicit custom mirror, then the
    built-in China mirror list. The built-in list is opt-in at the CLI layer
    (``--china-mirror``): those are third-party proxies, so we do not route a
    maintainer's requests through them unless they asked for it.
    """
    if include_direct:
        yield ""
    if custom_mirror:
        yield custom_mirror
    env = os.environ.get("HERMES_THIRDPARTY_MIRROR", "")
    if env and env != custom_mirror:
        yield env
    if not include_china_defaults:
        return
    for m in _DEFAULT_CHINA_MIRRORS:
        if m != custom_mirror and m != env:
            yield m


def _apply_tag_pattern(tag: str, pattern: str | None) -> str | None:
    """Optionally filter/extract a version from a raw tag."""
    if not pattern:
        return tag
    m = re.match(pattern, tag)
    if m:
        return m.group(1)
    return None


def _get_latest_tag_from_api(
    repo: str,
    pattern: str | None,
    mirrors: list[str],
) -> str | None:
    """Try GitHub's ``/releases/latest`` and ``/releases`` API endpoints."""
    endpoints = [
        f"https://api.github.com/repos/{repo}/releases/latest",
        f"https://api.github.com/repos/{repo}/releases?per_page=10",
    ]

    # Try direct + each mirror prefix against the API.  Most China mirrors
    # proxy github.com but not api.github.com reliably, so this is best-effort.
    for mirror in mirrors:
        for endpoint in endpoints:
            url = _mirror_url(endpoint, mirror) if mirror else endpoint
            data = _github_api(url)
            if isinstance(data, dict) and "tag_name" in data:
                tag = data.get("tag_name", "")
                if tag:
                    result = _apply_tag_pattern(tag, pattern)
                    return result if result is not None else tag
            elif isinstance(data, list):
                for release in data:
                    tag = release.get("tag_name", "")
                    if tag and not release.get("prerelease", False) and not release.get("draft", False):
                        result = _apply_tag_pattern(tag, pattern)
                        if result is not None:
                            return result
                        if not pattern:
                            return tag
    return None


def _looks_like_version_tag(tag: str) -> bool:
    """Return True if *tag* looks like a release version tag."""
    return bool(re.match(r"^v?\d+\.\d+(\.\d+)?", tag))


def _get_fallback_tag_via_redirect(
    repo: str,
    pattern: str | None,
    mirrors: list[str],
) -> str | None:
    """Resolve ``/releases/latest`` redirect to discover the latest tag.

    This works through ordinary github.com prefix mirrors, so it is the
    fallback of choice when the API is rate-limited.
    """
    base = f"https://github.com/{repo}/releases/latest"
    for mirror in mirrors:
        url = _mirror_url(base, mirror) if mirror else base
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "hermes-cn-thirdparty-updater"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                final_url = resp.url
            tag = final_url.rstrip("/").rsplit("/", 1)[-1]
            result = _apply_tag_pattern(tag, pattern)
            if result is not None:
                return result
            if _looks_like_version_tag(tag):
                return tag
            # If the mirror returned a landing page or proxy domain, skip it.
            prefix = f"[{mirror}] " if mirror else ""
            print(f"  ⚠️  {prefix}Redirect fallback returned non-tag '{tag}', skipping.")
        except Exception as exc:
            prefix = f"[{mirror}] " if mirror else ""
            print(f"  ⚠️  {prefix}Redirect fallback failed: {exc}")
    return None


def _get_latest_tag(
    repo: str,
    pattern: str | None,
    mirrors: list[str],
) -> str | None:
    """Best-effort latest release tag discovery."""
    latest = _get_latest_tag_from_api(repo, pattern, mirrors)
    if latest:
        return latest
    return _get_fallback_tag_via_redirect(repo, pattern, mirrors)


# ── File helpers ──────────────────────────────────────────────────────────────

def _read_current_version(tool: ThirdPartyTool) -> str | None:
    """Read the current version string from the primary source file."""
    filepath = PROJECT_ROOT / tool.primary_file
    if not filepath.is_file():
        print(f"  ❌ File not found: {tool.primary_file}")
        return None

    content = filepath.read_text(encoding="utf-8")
    for line in content.splitlines():
        m = re.match(tool.primary_pattern, line)
        if m:
            return m.group("ver")
    print(f"  ⚠️  Could not find {tool.version_var} in {tool.primary_file}.")
    return None


def _update_version_in_file(
    filepath: Path,
    pattern: str,
    new_version: str,
    group: str = "ver",
) -> bool:
    """Replace *every* version string matching *pattern* in *filepath*.

    All occurrences are rewritten, not just the first: a single file can pin
    the same tool more than once (``.github/workflows/tests.yml`` installs
    ripgrep in both the ``test`` and the ``e2e`` job). Patching only the first
    match leaves the file internally inconsistent — exactly the drift this
    script exists to prevent.

    Returns ``True`` if the pattern matched at least once.
    """
    content = filepath.read_text(encoding="utf-8")

    def _replace(m: re.Match) -> str:
        full = m.group(0)
        old = m.group(group)
        return full.replace(old, new_version, 1)

    new_content, count = re.subn(pattern, _replace, content, flags=re.MULTILINE)
    if count == 0:
        print(f"  ❌ Could not find version pattern in {filepath.name}")
        return False

    if new_content != content:
        filepath.write_text(new_content, encoding="utf-8")
    return True


# ── SHA256 helpers ────────────────────────────────────────────────────────────

def _ripgrep_tarball_url(version: str, arch: str) -> str:
    """Return the GitHub release download URL for the ripgrep Linux tarball."""
    tarball = f"ripgrep-{version}-{arch}.tar.gz"
    return (
        f"https://github.com/BurntSushi/ripgrep/releases/download/{version}/{tarball}"
    )


def _download_bytes(url: str) -> bytes | None:
    """Download *url* and return its raw bytes, or ``None`` on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "hermes-cn-thirdparty-updater"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except Exception as exc:
        print(f"  ⚠️  Download failed: {url} — {exc}")
        return None


def _compute_sha256(urls: list[str]) -> str | None:
    """Try each URL in turn and return the SHA256 of the first success."""
    for url in urls:
        data = _download_bytes(url)
        if data is not None:
            return hashlib.sha256(data).hexdigest()
    return None


def _update_sha256(
    tool: ThirdPartyTool,
    new_version: str,
    mirrors: list[str],
    arch: str | None = None,
) -> bool:
    """Download the release asset and update the pinned SHA256 checksum."""
    sha_file = tool.sha256_file
    sha_pattern = tool.sha256_pattern
    if not sha_file or not sha_pattern:
        return True

    target_arch = arch or tool.sha256_arch
    direct_url = _ripgrep_tarball_url(new_version, target_arch)
    urls = [_mirror_url(direct_url, m) if m else direct_url for m in mirrors]

    print(f"  📦 Computing SHA256 for {tool.name} {new_version} ({target_arch})...")
    new_sha = _compute_sha256(urls)
    if new_sha is None:
        print(
            "  ⚠️  Could not compute SHA256. "
            f"Please update {sha_file} manually or retry with --china-mirror."
        )
        return False

    filepath = PROJECT_ROOT / sha_file
    if _update_version_in_file(filepath, sha_pattern, new_sha, group="sha"):
        print(f"  ✅ Updated SHA256 in {sha_file}")
        return True
    return False


# ── User interaction ──────────────────────────────────────────────────────────

def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask the user a yes/no question.

    In non-interactive environments the *default* value is returned.
    """
    if not sys.stdin.isatty():
        return default

    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        answer = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default

    if not answer:
        return default
    return answer in ("y", "yes")


# ── Main logic ────────────────────────────────────────────────────────────────

def check_and_update(
    tool: ThirdPartyTool,
    *,
    auto_yes: bool,
    check_only: bool,
    skip_shasum: bool,
    mirrors: list[str],
    sha_arch: str | None = None,
) -> int:
    """Check a single tool and optionally update its version strings.

    Returns 0 if up-to-date or successfully updated, 1 if an update is
    available but was declined or failed.
    """
    print(f"\n── {tool.name} ──")

    current = _read_current_version(tool)
    if current is None:
        return 1
    print(f"  Current version: {current}")

    latest = _get_latest_tag(tool.github_repo, tool.tag_pattern, mirrors)
    if latest is None:
        print("  ❌ Could not fetch latest version from GitHub or mirrors.")
        return 1

    if tool.version_clean:
        latest = tool.version_clean(latest)
    if not latest:
        print("  ❌ Could not determine a usable version from the latest tag.")
        return 1

    print(f"  Latest version:  {latest}")

    if current == latest:
        print("  ✅ Up-to-date.")
        return 0

    print(f"  ⬆️  Update available: {current} → {latest}")

    if check_only:
        print("  ⏭️  Check-only mode; skipping update.")
        return 1

    if not auto_yes and not _ask_yes_no(
        f"  Update {tool.name} to {latest}?", default=True
    ):
        print("  ⏭️  Skipped.")
        return 1

    # Update primary file
    primary_path = PROJECT_ROOT / tool.primary_file
    if not _update_version_in_file(primary_path, tool.primary_pattern, latest):
        return 1
    print(f"  ✅ Updated {tool.primary_file}")

    # Update extra files
    for rule in tool.extra_files:
        extra_path = PROJECT_ROOT / rule.path
        if not extra_path.is_file():
            print(f"  ⚠️  Extra file missing: {rule.path}")
            continue
        if _update_version_in_file(extra_path, rule.pattern, latest, group=rule.group):
            print(f"  ✅ Updated {rule.path}")

    # Update SHA256 if applicable
    if tool.needs_sha256 and not skip_shasum:
        if not _update_sha256(tool, latest, mirrors, arch=sha_arch):
            return 1
    elif tool.needs_sha256 and skip_shasum:
        print("  ⏭️  SHA256 update skipped (--skip-shasum).")

    return 0


def _build_mirror_list(args: argparse.Namespace) -> list[str]:
    """Build the ordered list of URL prefixes to try."""
    custom = args.mirror or os.environ.get("HERMES_THIRDPARTY_MIRROR", "")
    # Always try direct GitHub first; mirrors are fallbacks.  An explicit
    # --mirror / HERMES_THIRDPARTY_MIRROR is always honored; the built-in
    # China mirror list is only appended when --china-mirror is passed.
    return list(
        _iter_mirrors(
            include_direct=True,
            custom_mirror=custom,
            include_china_defaults=args.china_mirror,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check and update third-party binary version strings.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        dest="auto_yes",
        help="Auto-approve all updates without prompting.",
    )
    parser.add_argument(
        "--check-only",
        "-c",
        action="store_true",
        help="Only check for newer versions; do not update.",
    )
    parser.add_argument(
        "--china-mirror",
        action="store_true",
        help="Enable Chinese mirror fallback for GitHub API and downloads.",
    )
    parser.add_argument(
        "--mirror",
        metavar="URL",
        default=None,
        help="Custom mirror prefix (also read from HERMES_THIRDPARTY_MIRROR).",
    )
    parser.add_argument(
        "--skip-shasum",
        action="store_true",
        help="Skip recomputing the ripgrep SHA256 in the CI workflow.",
    )
    parser.add_argument(
        "--arch",
        default="x86_64-unknown-linux-musl",
        help="Target triple whose tarball is used for the ripgrep SHA256 "
             "(default: x86_64-unknown-linux-musl).",
    )
    parser.add_argument(
        "tools",
        nargs="*",
        choices=[t.name for t in TOOLS],
        help="Specific tools to check (default: all).",
    )
    args = parser.parse_args()

    mirrors = _build_mirror_list(args)

    if args.tools:
        tools_to_check = [t for t in TOOLS if t.name in args.tools]
    else:
        tools_to_check = list(TOOLS)

    if not tools_to_check:
        print("No matching tools found.")
        return 1

    exit_code = 0
    for tool in tools_to_check:
        rc = check_and_update(
            tool,
            auto_yes=args.auto_yes,
            check_only=args.check_only,
            skip_shasum=args.skip_shasum,
            mirrors=mirrors,
            sha_arch=args.arch,
        )
        if rc != 0:
            exit_code = rc

    print()
    if exit_code == 0:
        print("✅ All checked tools are up-to-date or successfully updated.")
    else:
        if args.check_only:
            print("⚠️  Updates are available. Run without --check-only to apply them.")
        else:
            print("⚠️  Some tools have updates available but were not applied.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
