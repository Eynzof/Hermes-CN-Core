"""Record actual Mach-O deployment requirements without rewriting binaries."""
import argparse
import json
import re
import subprocess
from pathlib import Path

MACHO_MAGIC = {
    b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


def parse_minimums(load_commands: str) -> list[str]:
    versions = set()
    for block in re.split(r"Load command \d+", load_commands):
        if "LC_BUILD_VERSION" in block:
            match = re.search(r"\bminos\s+([\d.]+)", block)
        elif "LC_VERSION_MIN_MACOSX" in block:
            match = re.search(r"\bversion\s+([\d.]+)", block)
        else:
            continue
        if match:
            versions.add(match.group(1))
    return sorted(versions, key=version_key)


def version_key(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in version.split("."))
    return parts + (0,) * (3 - len(parts))


def audit(root: Path) -> dict:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in MACHO_MAGIC:
                continue
        result = subprocess.run(["otool", "-arch", "all", "-l", str(path)], capture_output=True, text=True, check=True)
        minimums = parse_minimums(result.stdout)
        files.append({"path": str(path.relative_to(root)), "minimumMacOSVersions": minimums})
    versions = [version for item in files for version in item["minimumMacOSVersions"]]
    if not versions:
        raise SystemExit(f"No Mach-O deployment targets found under {root}")
    highest = max(versions, key=version_key)
    return {
        "maximumDeclaredMinimumMacOS": highest,
        "requiresOldestSystemAcceptance": True,
        "highestRequirementFiles": [item["path"] for item in files if highest in item["minimumMacOSVersions"]],
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-minimum", help="Fail when a packaged binary needs a newer macOS")
    args = parser.parse_args()
    report = audit(args.runtime)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Mach-O files: {len(report['files'])}; maximum declared minimum macOS: {report['maximumDeclaredMinimumMacOS']}")
    print("This metadata is not proof of execution on the oldest supported system.")
    if args.max_minimum and version_key(report["maximumDeclaredMinimumMacOS"]) > version_key(args.max_minimum):
        raise SystemExit(f"Packaged binary requires macOS {report['maximumDeclaredMinimumMacOS']}, above declared minimum {args.max_minimum}")


if __name__ == "__main__":
    main()
