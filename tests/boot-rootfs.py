#!/usr/bin/env python3
"""Boot an initramfs on an isolated ARMv7 VM and exercise its local console."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time


# Extracted from Alpine's official alpine-netboot-3.24.4-armv7.tar.gz.
KERNEL_SHA = "bb3acbde45447780bcec2b09a414816d48da5d17daadb0b028fecd218b52e4c1"
NETBOOT_SHA = "047e5609d8bb2d61867eb5309f63e1e3bf0793d73b0fa107da7df008c6d55110"


def add_vm_modules(args, work):
    """Append a QEMU-only initramfs with drivers from the matching generic kernel."""
    with args.netboot.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != NETBOOT_SHA:
            raise ValueError("Expected Alpine 3.24.4 armv7 netboot archive")
    modloop = work / "modloop"
    with tarfile.open(args.netboot, "r:gz") as source, modloop.open("wb") as out:
        shutil.copyfileobj(source.extractfile("boot/modloop-lts"), out)
    path = Path(__file__).resolve().parents[1] / "tools/build-rootfs.py"
    spec = importlib.util.spec_from_file_location("echo2_rootfs", path)
    builder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = builder
    spec.loader.exec_module(builder)
    entries = {}
    for module in ("drivers/virtio/virtio", "drivers/virtio/virtio_ring", "drivers/virtio/virtio_mmio",
                   "drivers/block/virtio_blk", "fs/squashfs/squashfs"):
        ko = "modules/6.18.60-0-lts/kernel/" + module + ".ko"
        data = subprocess.check_output(["unsquashfs", "-cat", str(modloop), ko])
        builder.add_file(entries, "etc/echo2-vm-test/" + Path(ko).name, data)
    extra = work / "modules.cpio.gz"
    builder.write_archive(entries, extra)
    initrd = work / "vm-initramfs"
    with initrd.open("wb") as out:
        for member in (args.image, extra):
            with member.open("rb") as source:
                shutil.copyfileobj(source, out)
    return initrd


def boot(args, work):
    with args.kernel.open("rb") as kernel:
        if hashlib.file_digest(kernel, "sha256").hexdigest() != KERNEL_SHA:
            raise ValueError("Expected Alpine 3.24.4 armv7 netboot's boot/vmlinuz-lts kernel")
    initrd = add_vm_modules(args, work) if args.feature else args.image
    command = [args.qemu, "-M", "virt", "-cpu", "cortex-a15", "-smp", "2", "-m", "256M",
               "-nodefaults", "-display", "none", "-monitor", "none", "-nic", "none",
               "-serial", "stdio", "-no-reboot", "-kernel", str(args.kernel),
               "-initrd", str(initrd), "-append",
               "console=ttyAMA0 rdinit=/init panic=1 echo2.console=1 quiet"]
    if args.feature:
        file_node = {"driver": "file", "node-name": "feature-file",
                     "filename": str(args.feature.resolve()), "read-only": True}
        raw_node = {"driver": "raw", "node-name": "feature", "file": "feature-file", "read-only": True}
        command += ["-blockdev", json.dumps(file_node), "-blockdev", json.dumps(raw_node),
                    "-device", "virtio-blk-device,drive=feature"]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT) as proc:
        chunks = queue.Queue()

        def reader():
            while data := proc.stdout.read1(4096):
                chunks.put(data)
            chunks.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        data = bytearray()
        deadline = time.monotonic() + 90
        stage = "activate"
        try:
            while True:
                try:
                    chunk = chunks.get(timeout=max(0, deadline - time.monotonic()))
                except queue.Empty:
                    raise ValueError("QEMU console timed out: " + data[-1200:].decode(errors="replace"))
                if chunk is None:
                    break
                data.extend(chunk)
                if stage == "activate" and b"press Enter to activate this console" in data:
                    proc.stdin.write(b"\n")
                    proc.stdin.flush()
                    stage = "prompt"
                if stage == "prompt" and b"# " in data:
                    commands = ["stty -echo", "echo E2-BEGIN", "uname -m", "cat /etc/alpine-release",
                                "readlink /proc/1/exe", "echo2-board"]
                    if args.expect_player:
                        commands.append(r"""set -- $(printf '\003\000' | echo2-player render speech 100 | od -An -td2); [ "$*" = '3 3 3 3 3 3' ] && echo E2-PLAYER-OK""")
                    if args.expect_voice:
                        commands.append("techo5-echod --version && echo E2-VOICE-OK")
                    if args.feature:
                        with args.feature.open("rb") as feature:
                            feature_sha = hashlib.file_digest(feature, "sha256").hexdigest()
                        commands.extend([
                            "for m in virtio_ring virtio virtio_mmio virtio_blk squashfs; do "
                            "insmod /etc/echo2-vm-test/$m.ko || break; done",
                            "for i in 1 2 3 4; do [ -b /dev/vda ] && break; sleep 1; done",
                            f"echo '{feature_sha}  /dev/vda' | sha256sum -c - && "
                            "mkdir -p /run/voice && mount -t squashfs -o ro /dev/vda /run/voice && "
                            "grep -q ' /run/voice squashfs ro,' /proc/mounts && "
                            "test -x /run/voice/usr/bin/techo5-echod && "
                            "test -f /run/voice/usr/share/doc/techo5-echod/BUILDINFO && "
                            "umount /run/voice && echo E2-FEATURE-OK",
                        ])
                    commands.extend(["echo E2-END", "poweroff -f"])
                    proc.stdin.write(("; ".join(commands) + "\n").encode())
                    proc.stdin.flush()
                    stage = "poweroff"
            proc.wait(timeout=5)
            thread.join(timeout=3)
            while not chunks.empty():
                data.extend(chunks.get_nowait() or b"")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    lines = data.decode(errors="replace").replace("\r", "").splitlines()
    required = ["E2-BEGIN", "armv7l", "3.24.4", "/bin/busybox", "E2-END"]
    if args.expect_voice:
        required.append("E2-VOICE-OK")
    if args.expect_player:
        required.append("E2-PLAYER-OK")
    if args.feature:
        required.append("E2-FEATURE-OK")
    if proc.returncode or any(line not in lines for line in required):
        raise ValueError("Unexpected ARM boot result: " + "\n".join(lines[-30:]))
    probe = next((json.loads(line) for line in lines if line.startswith('{"schema":')), {})
    if probe.get("board") != "other" or probe.get("schema") != 1:
        raise ValueError("Board probe should recognise QEMU as a non-Echo board: "
                         + "\n".join(lines[-18:]))
    print("ARMv7 QEMU boot passed (Alpine 3.24.4, BusyBox init, board probe, clean poweroff).")
    if args.expect_player or args.expect_voice or args.feature:
        print("Guest command checks:", " ".join(line for line in required if line.endswith("-OK")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--qemu", default="qemu-system-arm")
    parser.add_argument("--expect-player", action="store_true")
    parser.add_argument("--expect-voice", action="store_true")
    parser.add_argument("--feature", type=Path, help="attach a squashfs as a read-only virtual disk")
    parser.add_argument("--netboot", type=Path, help="Alpine 3.24.4 ARMv7 netboot tarball for VM-only drivers")
    args = parser.parse_args()
    if bool(args.feature) != bool(args.netboot):
        parser.error("use --feature and --netboot together")
    try:
        with tempfile.TemporaryDirectory() as work:
            boot(args, Path(work))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
