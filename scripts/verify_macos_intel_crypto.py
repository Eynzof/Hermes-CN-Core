"""Reject incompatible crypto libraries before the long Intel ORT build."""
import argparse
import ctypes.util
import json
import platform
import ssl
import subprocess
import sys
from pathlib import Path

import _hashlib
import _ssl
import cryptography
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.bindings import _rust
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from audit_macos_deployment_targets import parse_minimums, version_key


def verify() -> dict:
    assert platform.system() == "Darwin" and platform.machine() == "x86_64"
    assert cryptography.__version__ == "50.0.0"
    assert backend.openssl_version_text().startswith("OpenSSL 3.5.8 ")
    message = b"hermes-runtime-crypto-smoke"
    aes = AESGCM(b"\x01" * 32)
    encrypted = aes.encrypt(b"\x02" * 12, message, None)
    assert aes.decrypt(b"\x02" * 12, encrypted, None) == message
    # ARC4 requires the legacy provider; successful encryption/decryption proves
    # that no-module retained it in the static OpenSSL build.
    legacy = Cipher(ARC4(b"\x03" * 16), mode=None)
    encrypted = legacy.encryptor().update(message)
    assert legacy.decryptor().update(encrypted) == message
    ssl.create_default_context()

    # Standalone Python embeds _ssl/_hashlib in its executable; setup-python's
    # official framework uses separate extensions. Inspect their real binaries.
    module_binaries = {module.__name__: str(Path(sys.executable if module.__spec__.origin == "built-in"
                                               else module.__file__).resolve())
                       for module in (_ssl, _hashlib, _rust)}
    loaded = {Path(path) for path in module_binaries.values()}
    loaded.update(Path(path).resolve() for path in ctypes.util.dllist()
                  if Path(path).name in {"libssl.3.dylib", "libcrypto.3.dylib"})
    libraries = []
    for path in sorted(loaded):
        links = subprocess.check_output(["otool", "-arch", "x86_64", "-L", str(path)], text=True)
        assert "/usr/local/opt/openssl" not in links and "/usr/local/Cellar/openssl" not in links, links
        if path == Path(_rust.__file__).resolve():
            assert "libssl." not in links and "libcrypto." not in links, "cryptography must link OpenSSL statically"
        commands = subprocess.check_output(["otool", "-arch", "x86_64", "-l", str(path)], text=True)
        minimums = parse_minimums(commands)
        assert minimums and all(version_key(v) <= version_key("14.0") for v in minimums), str(path)
        libraries.append({"path": str(path), "intelMinimumMacOS": minimums, "links": links})
    return {"cryptographyVersion": cryptography.__version__, "cryptographyOpenSSL": backend.openssl_version_text(),
            "pythonOpenSSL": ssl.OPENSSL_VERSION, "aesRoundTrip": True, "legacyArc4RoundTrip": True,
            "moduleBinaries": module_binaries, "libraries": libraries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Intel static OpenSSL 3.5.8, AES and legacy provider passed; linked libraries require macOS <= 14")
