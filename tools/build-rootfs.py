#!/usr/bin/env python3
"""Package a board-independent ARMv7 Alpine initramfs using only Python 3."""

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ALPINE = "alpine-minirootfs-3.24.4-armv7.tar.gz"
URL = "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/" + ALPINE
SHA256 = "2054061dafea7fe4cd38015701ee77119e780dd0382d0c32fad56986fe31b699"


@dataclass
class Entry:
    mode: int
    data: bytes = b""
    uid: int = 0
    gid: int = 0
    major: int = 0
    minor: int = 0


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fetch(cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / ALPINE
    if target.exists():
        return target
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
        part = Path(stream.name)
    try:
        with urllib.request.urlopen(URL, timeout=45) as source, part.open("wb") as out:
            shutil.copyfileobj(source, out)
        if digest(part) != SHA256:
            raise ValueError("Alpine download failed its pinned SHA-256 check")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


def load_alpine(archive):
    if digest(archive) != SHA256:
        raise ValueError("Alpine archive does not match the official pinned SHA-256")
    entries = {}
    with tarfile.open(archive, "r:gz") as source:
        for item in source:
            path = PurePosixPath(item.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unexpected tar path {item.name!r}")
            if not path.parts:
                continue
            if item.isdir():
                mode, data = stat.S_IFDIR, b""
            elif item.isfile():
                mode, data = stat.S_IFREG, source.extractfile(item).read()
            elif item.issym():
                mode, data = stat.S_IFLNK, item.linkname.encode()
            else:
                raise ValueError(f"unsupported member {item.name!r}")
            entries[path.as_posix()] = Entry(mode | item.mode, data, item.uid, item.gid)
    return entries


def static_arm(path):
    data = path.read_bytes()
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError(f"{path.name} must be a little-endian ARM32 ELF executable")
    kind, machine = struct.unpack_from("<HH", data, 16)
    phoff = struct.unpack_from("<I", data, 28)[0]
    flags = struct.unpack_from("<I", data, 36)[0]
    stride, count = struct.unpack_from("<HH", data, 42)
    if (machine != 40 or kind not in (2, 3) or flags >> 24 != 5
            or stride < 32 or count == 0 or phoff + stride * count > len(data)):
        raise ValueError(f"{path.name} must target ARM EABI5")
    headers = [struct.unpack_from("<8I", data, phoff + i * stride) for i in range(count)]
    if not any(h[0] == 1 and h[6] & 1 for h in headers):
        raise ValueError(f"{path.name} has no executable load segment")
    if any(h[0] == 3 for h in headers):
        raise ValueError(f"{path.name} requires an ELF interpreter; provide a static executable")
    # A static PIE may have PT_DYNAMIC, but must have no DT_NEEDED libraries.
    for tag, offset, _, _, length, _, _, _ in headers:
        if offset + length > len(data):
            raise ValueError(f"{path.name} has a truncated program segment")
        if tag == 2 and (length % 8 or any(
            kind == 1 for kind, _ in struct.iter_unpack("<iI", data[offset:offset + length])
        )):
            raise ValueError(f"{path.name} depends on a shared library or has an invalid dynamic table")
    return data


def add_file(entries, name, data, mode=0o644):
    for parent in PurePosixPath(name).parents:
        if parent.parts:
            entries.setdefault(parent.as_posix(), Entry(stat.S_IFDIR | 0o755))
    entries[name] = Entry(stat.S_IFREG | mode, data)


def add_project(entries, player):
    for path in sorted((ROOT / "rootfs").rglob("*")):
        name = path.relative_to(ROOT / "rootfs").as_posix()
        if path.is_dir():
            entries.setdefault(name, Entry(stat.S_IFDIR | 0o755))
        else:
            # Development happens on both Windows and Linux. Stage shell files with LF.
            data = path.read_text(encoding="utf-8").encode()
            mode = 0o755 if data.startswith(b"#!") else 0o644
            entries[name] = Entry(stat.S_IFREG | mode, data)
    probe = (ROOT / "src/echo2-board").read_text(encoding="utf-8").encode()
    add_file(entries, "usr/bin/echo2-board", probe, 0o755)
    if player:
        add_file(entries, "usr/bin/echo2-player", static_arm(player), 0o755)
        add_file(entries, "usr/share/licenses/echo2-player/TinyALSA-NOTICE",
                 (ROOT / "licenses/tinyalsa.txt").read_bytes())
    entries["dev/console"] = Entry(stat.S_IFCHR | 0o600, major=5, minor=1)
    entries["dev/null"] = Entry(stat.S_IFCHR | 0o666, major=1, minor=3)


def voice_entries(voice_dir):
    entries = {}
    licenses = voice_dir / "licenses"
    files = sorted(licenses.rglob("*"))
    if licenses.is_symlink() or not any(p.is_file() for p in files):
        raise ValueError("voice bundle must include its licenses directory")
    for path in files:
        if path.is_symlink():
            raise ValueError(f"symlink in voice licenses: {path}")
        if path.is_file():
            name = "usr/share/doc/techo5-echod/licenses/" + path.relative_to(licenses).as_posix()
            add_file(entries, name, path.read_bytes())
    voice = static_arm(voice_dir / "techo5-echod")
    expected = (voice_dir / "SHA256SUMS").read_text(encoding="ascii").split()
    if expected != [hashlib.sha256(voice).hexdigest(), "techo5-echod"]:
        raise ValueError("voice executable does not match its bundle's SHA256SUMS")
    add_file(entries, "usr/bin/techo5-echod", voice, 0o755)
    add_file(entries, "usr/share/doc/techo5-echod/BUILDINFO", (voice_dir / "BUILDINFO").read_bytes())
    return entries


def write_feature(entries, output):
    # The resulting feature is a separate read-only filesystem. /init never mounts it.
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "source"
        source.mkdir()
        source.chmod(0o755)
        for name, entry in sorted(entries.items()):
            path = source / name
            if stat.S_ISDIR(entry.mode):
                path.mkdir()
            else:
                path.write_bytes(entry.data)
            path.chmod(stat.S_IMODE(entry.mode))
        subprocess.run(["mksquashfs", str(source), str(output), "-noappend", "-comp", "gzip",
                        "-mkfs-time", "0", "-all-time", "0", "-all-root", "-no-xattrs", "-no-exports",
                        "-no-progress", "-processors", "1"], check=True, stdout=subprocess.DEVNULL,
                       env={k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"})
        extracted = Path(work) / "check"
        subprocess.run(["unsquashfs", "-no-progress", "-processors", "1", "-d", str(extracted),
                        str(output)], check=True, stdout=subprocess.DEVNULL)
        actual = {p.relative_to(extracted).as_posix() for p in extracted.rglob("*")}
        if actual != set(entries):
            raise ValueError("squashfs file inventory mismatch")
        for name, entry in entries.items():
            path = extracted / name
            if path.stat().st_mode != entry.mode or (path.is_file() and path.read_bytes() != entry.data):
                raise ValueError(f"squashfs round-trip verification failed for {name}")


def write_archive(entries, target):
    with target.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb",
                                                 compresslevel=9, mtime=0) as out:
        # newc headers have thirteen 8-character hex fields after the magic.
        for ino, (name, e) in enumerate([*sorted(entries.items()), ("TRAILER!!!", Entry(0))], 1):
            filename = name.encode() + b"\0"
            fields = (ino, e.mode, e.uid, e.gid, 2 if stat.S_ISDIR(e.mode) else 1,
                      0, len(e.data), 0, 0, e.major, e.minor, len(filename), 0)
            out.write(b"070701" + "".join(f"{n:08x}" for n in fields).encode() + filename)
            out.write(b"\0" * (-(110 + len(filename)) % 4))
            out.write(e.data)
            out.write(b"\0" * (-len(e.data) % 4))
        out.write(b"\0" * (-out.tell() % 512))


def check_destination(output, inputs, replace):
    dest = output.resolve()
    if (replace and output.exists() and dest.is_relative_to(ROOT)
            and not any(dest.is_relative_to(ROOT / d) for d in ("build", "dist"))):
        raise ValueError("choose an output outside the source tree or under build/ or dist/")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("the output must be a regular file")
    for path in inputs:
        if path is None:
            continue
        if dest == path.resolve() or (path.is_dir() and dest.is_relative_to(path.resolve())):
            raise ValueError("the output overlaps a build input")
        if output.exists() and path.is_file() and output.samefile(path):
            raise ValueError("the output aliases a build input")


def publish(entries, output, writer, replace, inputs):
    check_destination(output, inputs, replace)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        part = Path(stream.name)
    try:
        writer(entries, part)
        sha = digest(part)
        try:
            os.link(part, output)
        except FileExistsError:
            check_destination(output, inputs, replace)
            if digest(output) != sha:
                if not replace:
                    raise ValueError(f"{output} differs; select a new output path or pass --replace")
                os.replace(part, output)
        print(f"{sha}  {output.name}")
    finally:
        part.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="use a previously downloaded official minirootfs")
    dest = parser.add_mutually_exclusive_group()
    dest.add_argument("--output", type=Path, help="initramfs destination")
    dest.add_argument("--feature-output", type=Path, help="build only the squashfs voice feature (Linux squashfs-tools)")
    parser.add_argument("--player", type=Path, help="static ARM EABI5 echo2-player")
    parser.add_argument("--voice-dir", type=Path, help="directory with static techo5-echod and its licenses/")
    parser.add_argument("--replace", action="store_true", help="atomically replace a different regular output file")
    args = parser.parse_args()
    if args.feature_output:
        if not args.voice_dir or args.player or args.archive:
            parser.error("--feature-output requires --voice-dir as its sole input")
        publish(voice_entries(args.voice_dir), args.feature_output, write_feature, args.replace, [args.voice_dir])
        return
    source = args.archive or fetch(ROOT / "build/downloads")
    entries = load_alpine(source)
    add_project(entries, args.player)
    if args.voice_dir:
        entries.update(voice_entries(args.voice_dir))
    publish(entries, args.output or ROOT / "build/echo2-linux-initramfs.cpio.gz", write_archive,
            args.replace, [source, args.player, args.voice_dir])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from error
