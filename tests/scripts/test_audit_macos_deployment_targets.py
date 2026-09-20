from scripts.audit_macos_deployment_targets import parse_minimums, version_key


def test_macho_minimums_ignore_sdk_and_dylib_compatibility_versions():
    output = """
Load command 1
          cmd LC_BUILD_VERSION
        minos 14.0
          sdk 26.0
Load command 2
          cmd LC_ID_DYLIB
current version 26.0.0
Load command 3
          cmd LC_VERSION_MIN_MACOSX
      version 10.15
          sdk 15.0
"""
    assert parse_minimums(output) == ["10.15", "14.0"]


def test_equivalent_version_precision_does_not_raise_os_floor():
    assert version_key("14.0") == version_key("14.0.0")
    assert version_key("14.1") > version_key("14.0")
