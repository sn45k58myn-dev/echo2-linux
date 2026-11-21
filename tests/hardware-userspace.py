#!/usr/bin/env python3
"""Run the Alpine userspace and a silent synthetic render on an already connected Radar."""

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "2054061dafea7fe4cd38015701ee77119e780dd0382d0c32fad56986fe31b699"
BASE_FILES = ("bin/busybox", "lib/ld-musl-armhf.so.1", "etc/alpine-release")
PLAYER = "usr/bin/echo2-player"


def test_device(args):
    with args.base.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != BASE_SHA:
            raise ValueError("Expected the pinned Alpine 3.24.4 ARMv7 minirootfs")
    player = args.player.read_bytes()
    if hashlib.sha256(player).hexdigest() != args.player_sha256:
        raise ValueError("Player checksum mismatch")
    files = {PLAYER: player}
    with tarfile.open(args.base, "r:gz") as archive:
        for name in BASE_FILES:
            entry = archive.getmember("./" + name)
            if not entry.isfile():
                raise ValueError(f"Expected a regular file at {name}")
            files[name] = archive.extractfile(entry).read()
    adb = [args.adb, "-s", args.serial]

    def shell(command):
        # This device uses the legacy shell protocol, which can lose the exit status.
        result = subprocess.run(adb + ["shell", "set -e; " + command + "; echo E2-OK"],
                                capture_output=True, timeout=20, check=True)
        lines = result.stdout.decode().replace("\r", "").splitlines()
        if not lines or lines[-1] != "E2-OK":
            raise RuntimeError("Device command failed: " + "\n".join(lines)[-400:])
        return "\n".join(lines[:-1])

    probe = (ROOT / "src/echo2-board").read_bytes().replace(b"\r\n", b"\n")
    blob = base64.b64encode(gzip.compress(probe)).decode()
    board = json.loads(shell("echo " + blob + " | busybox base64 -d | busybox gzip -d | sh"))
    if board.get("board") != "radar_puffin":
        raise ValueError("Connected board is not Radar")
    shell('test "$(id -u)" = 0')
    leaf = shell("umask 077; mktemp -d /tmp/echo2-userspace.XXXXXXXX")
    if not re.fullmatch(r"/tmp/echo2-userspace\.[a-zA-Z0-9]{8}", leaf):
        raise RuntimeError("Unexpected temporary path")
    guard = f'test ! -L {leaf}; test "$(cd {leaf} && pwd -P)" = {leaf}; '
    dirs = ["bin", "etc", "lib", "usr", "usr/bin"]
    try:
        shell(guard + f'test "$(stat -c %a {leaf})" = 700; cd {leaf}; mkdir ' + " ".join(dirs))
        with TemporaryDirectory() as work:
            for name, data in files.items():
                local = Path(work) / Path(name).name
                local.write_bytes(data)
                subprocess.run(adb + ["push", str(local), leaf + "/" + name],
                               capture_output=True, timeout=30, check=True)
                sha = hashlib.sha256(data).hexdigest()
                shell(f"cd {leaf}; echo '{sha}  {name}' | busybox sha256sum -c -")
        shell(f"cd {leaf}; ln -s busybox bin/sh; chmod 755 bin/busybox lib/ld-musl-armhf.so.1 {PLAYER}")
        guest = (
            "set -eu; for dir in proc sys dev data; do test ! -e /$dir; done; "
            "/bin/busybox uname -m; /bin/busybox cat /etc/alpine-release; "
            r"/bin/busybox printf '\003\000' | /usr/bin/echo2-player render speech 100 | "
            "/bin/busybox od -An -t u2"
        )
        lines = shell(f"chroot {leaf} /bin/sh -c {shlex.quote(guest)}").splitlines()
        if len(lines) != 3 or lines[0] not in ("armv7l", "armv8l") or lines[1] != "3.24.4":
            raise RuntimeError(f"Unexpected guest release or architecture: {lines!r}")
        if lines[2].split() != ["3"] * 6:
            raise RuntimeError("Synthetic sample conversion did not match six expected words")
        print(f"Radar confirmed. Alpine {lines[1]}, uname -m {lines[0]}. Rendered six words of value 3.")
    finally:
        cleanup = "rm -f " + " ".join([*files, "bin/sh"]) + "; "
        cleanup += "; ".join(f"if test -d {d}; then rmdir {d}; fi" for d in reversed(dirs))
        shell(guard + f"cd {leaf}; " + cleanup + f"; cd /; rmdir {leaf}; test ! -e {leaf}")
        print("Temporary chroot removed and absence verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True, help="An already connected and authorized ADB device")
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--player", type=Path, default=ROOT / "build/echo2-player")
    parser.add_argument("--player-sha256", required=True, help="SHA-256 from your verified player build")
    test_device(parser.parse_args())
