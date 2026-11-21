#!/usr/bin/env python3
"""Cross-build the optional TECHO5 Radar voice daemon from pinned source."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
from urllib.request import urlopen


PROJECT = Path(__file__).resolve().parents[1]
COMMIT = "3907d43be77f94c075c0ff72826cfc52ac1b0f94"
ARCHIVE = "2f61366e8508c6c9a839674b5e804b1dcf2730bb695c36af3624040670c3c6b3"
IMAGE = "golang:1.27.4-bookworm@sha256:fb7cceb528b47141eccd9b60bf3cbd27d5ef5f8cf5b7d338bec866da495b50dc"
EXPECTED_BINARY = "488d2a6bbee47c62a832ee781bb4fab144c0cfb7d173e2d176c18cd79d0c1f14"


def inspect_bundle(bundle):
    """Verify the tested build without running its hardware initializers."""
    binary = bundle / "techo5-echod"
    with binary.open("rb") as handle:
        header = handle.read(20)
        if header[:6] != b"\x7fELF\x01\x01" or len(header) != 20 or struct.unpack_from("<H", header, 18)[0] != 40:
            raise ValueError("Expected a little-endian 32-bit ARM ELF")
        handle.seek(0)
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != EXPECTED_BINARY or (bundle / "SHA256SUMS").read_text().split() != [digest, "techo5-echod"]:
        raise ValueError("Voice executable differs from the tested build")
    info = (bundle / "BUILDINFO").read_text().splitlines()
    if "\tpath\tgithub.com/HuskerMinion/techo5/echod/cmd/echod" not in info or "\tbuild\t-tags=dot" not in info:
        raise ValueError("Expected TECHO5 dot/Radar build metadata")
    if not (bundle / "licenses/TECHO5-NOTICE").is_file():
        raise ValueError("Voice bundle is missing its attribution")
    print(f"TECHO5 Echo 2/Dot candidate, Linux ARMv7, source {COMMIT}, SHA-256 {digest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "dist" / "voice")
    parser.add_argument("--inspect", action="store_true",
                        help="check the existing bundle on this computer without running it")
    parser.add_argument("--source-archive", type=Path, help="existing pinned TECHO5 source tar.gz")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.inspect:
        inspect_bundle(output)
        return
    if output.exists():
        parser.error("select a new output directory; a previous build is already present")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".voice-build-", dir=output.parent) as tmp:
        work = Path(tmp)
        archive = work / "source.tar.gz"
        if args.source_archive:
            shutil.copyfile(args.source_archive, archive)
        else:
            url = f"https://codeload.github.com/HuskerMinion/techo5/tar.gz/{COMMIT}"
            with urlopen(url, timeout=90) as response, archive.open("wb") as target:
                shutil.copyfileobj(response, target)
        with archive.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        if sha != ARCHIVE:
            raise SystemExit("TECHO5 source archive failed SHA-256 verification")
        staged = work / "bundle"
        staged.mkdir()
        script = Path(__file__).with_name("build-voice-container.sh").resolve()
        subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--mount", f"type=bind,src={archive},dst=/src.tar.gz,readonly",
            "--mount", f"type=bind,src={script},dst=/recipe.sh,readonly",
            "--mount", f"type=bind,src={staged},dst=/out",
            "-e", f"OUTPUT_UID={os.getuid() if hasattr(os, 'getuid') else 0}",
            "-e", f"OUTPUT_GID={os.getgid() if hasattr(os, 'getgid') else 0}",
            IMAGE, "bash", "/recipe.sh", COMMIT,
        ], check=True)
        inspect_bundle(staged)
        staged.rename(output)
    print(f"Voice candidate and attribution: {output}")


if __name__ == "__main__":
    main()
