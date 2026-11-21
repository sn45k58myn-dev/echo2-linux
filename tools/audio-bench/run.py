#!/usr/bin/env python3
"""Boot the project's Alpine base and service overlay on an isolated ARMv7 board."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("echo_rootfs", HERE.parent / "build-rootfs.py")
fs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fs
spec.loader.exec_module(fs)

KERNEL = "bb3acbde45447780bcec2b09a414816d48da5d17daadb0b028fecd218b52e4c1"
NETBOOT = "047e5609d8bb2d61867eb5309f63e1e3bf0793d73b0fa107da7df008c6d55110"


def verified(path, expected):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"unexpected digest for {path.name}")


def drivers(entries, netboot, scratch):
    verified(netboot, NETBOOT)
    modloop = scratch / "modloop"
    with tarfile.open(netboot, "r:gz") as tar, modloop.open("wb") as out:
        shutil.copyfileobj(tar.extractfile("boot/modloop-lts"), out)
    prefix = "modules/6.18.60-0-lts/"

    def extract(name):
        return subprocess.check_output(["unsquashfs", "-cat", str(modloop), prefix + name])

    deps = {}
    for line in extract("modules.dep").decode().splitlines():
        module, prerequisites = line.split(":", 1)
        deps[module] = prerequisites.split()
    ordered = []

    def add(module):
        if module in ordered:
            return
        for prerequisite in deps[module]:
            add(prerequisite)
        ordered.append(module)
        fs.add_file(entries, "etc/echo2-bench/drivers/" + Path(module).name, extract(module))

    for name in ("virtio_mmio", "virtio_net", "virtio-rng"):
        add(next(path for path in deps if path.endswith("/" + name + ".ko")))
    fs.add_file(entries, "etc/echo2-bench/modules",
                "".join(Path(m).name + "\n" for m in ordered).encode())


def run(args, scratch):
    verified(args.kernel, KERNEL)
    entries = {}
    drivers(entries, args.netboot, scratch)
    fs.add_file(entries, "etc/echo2-bench/start", (HERE / "start.sh").read_bytes(), 0o755)
    fs.add_file(entries, "etc/inittab", b"::once:/etc/echo2-bench/start\n::shutdown:/bin/sync\n")
    if args.network_check:
        fs.add_file(entries, "etc/echo2-bench/network-check", b"")
    else:
        for name in ("server.crt", "server.key", "password"):
            fs.add_file(entries, "etc/echo2-bench/private/" + name,
                        (args.private / name).read_bytes(), 0o600)
    extra = scratch / "extra.gz"
    fs.write_archive(entries, extra)
    initrd = scratch / "initramfs"
    with initrd.open("wb") as out:
        for path in [args.base] + ([args.services] if args.services else []) + [extra]:
            with path.open("rb") as source:
                shutil.copyfileobj(source, out)
    net = "user,id=bench,restrict=on,hostfwd=tcp:0.0.0.0:8443-:8443"
    if not args.network_check:
        net += ",hostfwd=tcp:0.0.0.0:8928-:8928"
    cmd = ["qemu-system-arm", "-M", "virt", "-cpu", "cortex-a15", "-smp", "2", "-m", "512M",
           "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio", "-no-reboot",
           "-kernel", str(args.kernel), "-initrd", str(initrd),
           "-append", "console=ttyAMA0 rdinit=/init panic=1 quiet",
           "-netdev", net, "-device", "virtio-net-device,netdev=bench",
           "-object", "rng-random,filename=/dev/urandom,id=entropy",
           "-device", "virtio-rng-device,rng=entropy"]
    return subprocess.run(cmd, timeout=args.seconds).returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("kernel", "netboot", "base"):
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--services", type=Path)
    parser.add_argument("--private", type=Path)
    parser.add_argument("--network-check", action="store_true")
    parser.add_argument("--seconds", type=int, default=3600)
    args = parser.parse_args()
    if not args.network_check and (args.services is None or not args.private):
        parser.error("supply --services and --private for an audio test")
    os.umask(0o077)
    with tempfile.TemporaryDirectory() as work:
        try:
            sys.exit(run(args, Path(work)))
        except subprocess.TimeoutExpired:
            print("Bench test window finished.", flush=True)
