#!/usr/bin/env python3
"""Package optional ARMv7 Sendspin and intercom services, including their licences."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
spec = importlib.util.spec_from_file_location("echo_rootfs", TOOLS / "build-rootfs.py")
fs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fs
spec.loader.exec_module(fs)


def add_bundle(entries, bundle, name, programs):
    checksums = {}
    for line in (bundle / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, path = line.split()
        if path in checksums:
            raise ValueError(f"duplicate {name} checksum")
        checksums[path] = digest
    if set(checksums) != {"bin/" + prog for prog in programs}:
        raise ValueError(f"unexpected {name} executable inventory")
    for program in programs:
        exe = bundle / "bin" / program
        if exe.is_symlink():
            raise ValueError(f"expected a regular executable: {program}")
        elf = fs.static_arm(exe)
        if hashlib.sha256(elf).hexdigest() != checksums["bin/" + program]:
            raise ValueError(f"{program} differs from its build receipt")
        fs.add_file(entries, "usr/bin/" + program, elf, 0o755)
    licenses = bundle / "licenses"
    if licenses.is_symlink() or not licenses.is_dir():
        raise ValueError(f"{name} requires its licence notices")
    files = sorted(licenses.rglob("*"))
    if not any(p.is_file() for p in files):
        raise ValueError(f"{name} has no licence notices")
    prefix = "usr/share/doc/echo2-" + name + "/"
    for p in files:
        if p.is_symlink():
            raise ValueError(f"unexpected symbolic link in {name} licences")
        if p.is_file():
            fs.add_file(entries, prefix + "licenses/" + p.relative_to(licenses).as_posix(), p.read_bytes())
    for filename in ("BUILDINFO", "SHA256SUMS"):
        if (bundle / filename).is_file():
            fs.add_file(entries, prefix + filename, (bundle / filename).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sendspin", type=Path, default=ROOT / "dist/sendspin")
    parser.add_argument("--intercom", type=Path, default=ROOT / "dist/intercom")
    parser.add_argument("--room-bridge", type=Path, default=ROOT / "dist/intercom-bridge")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--squashfs", action="store_true", help="output a read-only feature filesystem")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    entries = {}
    add_bundle(entries, args.sendspin, "sendspin",
               ("echo2-sendspind", "echo2-sendspin-sink", "echo2-board-audio"))
    add_bundle(entries, args.intercom, "intercom", ("echo2-intercom",))
    add_bundle(entries, args.room_bridge, "intercom-bridge",
               ("echo2-intercom-bridge", "libreecho-intercomd"))
    suffix = ".squashfs" if args.squashfs else ".cpio.gz"
    output = args.output or ROOT / "build" / ("echo2-audio-services" + suffix)
    fs.publish(entries, output, fs.write_feature if args.squashfs else fs.write_archive,
               args.replace, [args.intercom, args.sendspin, args.room_bridge])


if __name__ == "__main__":
    main()
