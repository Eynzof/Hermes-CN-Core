"""Critical payload patterns remain blocking around narrowly reviewed transport occurrences."""

import difflib
import hashlib

import pytest

from scripts.ci.scan_supply_chain import scan


def _encoded_call(value="eA==", decoder="b64decode", execute="exec"):
    # Construct adversarial input as data; none of these programs are executed.
    decoded = f"base64.{decoder}('{value}')"
    return f"{execute}({decoded})"


def _diff(path, before, after):
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                      fromfile=f"a/{path}", tofile=f"b/{path}", n=0))


def _review(path, source, line):
    lines = source.splitlines()
    fingerprint = lambda text: hashlib.sha256(text.encode()).hexdigest()
    return {"path": path, "rule": "base64-exec", "line_sha256": fingerprint(lines[line - 1]),
            "expected_occurrences": 1,
            "context": {"before": line - 1, "after": len(lines) - line,
                        "sha256": fingerprint("\n".join(lines))}}


@pytest.mark.parametrize("path", ["runtime/launcher.py", "tests/test_launcher.py"])
def test_reviewed_occurrence_is_allowed_without_exempting_its_file(path):
    source = "source = 'visible launcher'\n" + _encoded_call() + "\n"
    record = _review(path, source, 2)
    findings, approved = scan(_diff(path, "", source), lambda _: source, [record])
    assert findings == []
    assert len(approved) == 1

    extended = source + _encoded_call("hidden payload") + "\n"
    findings, approved = scan(_diff(path, source, extended), lambda _: extended, [record])
    assert len(findings) == 1
    assert len(approved) == 1


@pytest.mark.parametrize("change", ["payload", "path", "duplicate", "source-context"])
def test_modified_or_copied_reviewed_occurrences_require_review(change):
    path = "runtime/launcher.py"
    source = "source = 'visible launcher'\n" + _encoded_call() + "\n"
    record = _review(path, source, 2)
    changed, before = source, ""
    if change == "payload":
        changed = source.replace("eA==", "changed payload")
    elif change == "path":
        path = "tests/test_unreviewed.py"
    elif change == "duplicate":
        before, changed = source, source + _encoded_call() + "\n"
    else:
        # The execution line is unchanged: changing its source still revokes review.
        before, changed = source, source.replace("visible launcher", "foreign source")
    findings, approved = scan(_diff(path, before, changed), lambda _: changed, [record])
    assert findings
    assert not approved


@pytest.mark.parametrize("kind, argument", [
    ("exec", "b64decode"), ("eval", "decodebytes"), ("EXEC", "urlsafe_b64decode"),
    ("subprocess", "base64_payload"), ("subprocess", "chr(65)"), ("subprocess", r"'\x41'"),
])
def test_unreviewed_payloads_in_tests_still_block(kind, argument):
    source = (f"subprocess.run({argument})" if kind == "subprocess"
              else _encoded_call(decoder=argument, execute=kind)) + "\n"
    path = "tests/test_unreviewed.py"
    findings, approved = scan(_diff(path, "", source), lambda _: source, [])
    assert len(findings) == 1
    assert not approved
