"""Build an ephemeral QEMU guest overlay from an owner's private stock files."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import stat
import struct
import sys


def needed(data):
    if data[:6] != b"\x7fELF\x01\x01":
        raise ValueError("Expected a 32-bit little-endian ELF")
    offset = struct.unpack_from("<I", data, 28)[0]
    stride, count = struct.unpack_from("<HH", data, 42)
    headers = [struct.unpack_from("<8I", data, offset + i * stride) for i in range(count)]
    dyn = next(h for h in headers if h[0] == 2)
    tags = list(struct.iter_unpack("<iI", data[dyn[1]:dyn[1]+dyn[4]]))
    addr = next(value for kind, value in tags if kind == 5)
    load = next(h for h in headers if h[0] == 1 and h[2] <= addr < h[2] + h[4])
    start = load[1] + addr - load[2]
    for kind, value in tags:
        if kind == 1:
            i = start + value
            yield data[i:data.index(b"\0", i)].decode("ascii")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for opt in ("project", "system", "driver", "base", "output"):
        p.add_argument("--" + opt, required=True, type=Path)
    p.add_argument("--mics", type=Path)
    p.add_argument("--reference", type=Path)
    p.add_argument("--log-sink", required=True, type=Path)
    p.add_argument("--binder-node", required=True, type=Path)
    p.add_argument("--start-bus", required=True, type=Path)
    p.add_argument("--loopback", choices=("speaker-input", "speaker-output"),
                   help="synthesize one block of digital return latency in lanes 7/8")
    args = p.parse_args()
    helper = args.project / "tools" / "build-rootfs.py"
    spec = importlib.util.spec_from_file_location("echo2_rootfs", helper)
    fs = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fs
    spec.loader.exec_module(fs)
    entries = {}
    driver = args.driver.read_bytes()
    fs.add_file(entries, "bench/driver", driver, 0o555)
    linker = (args.system / "bin/linker").read_bytes()
    fs.add_file(entries, "system/bin/linker", linker, 0o555)
    queue = list(needed(driver))
    for name in ("servicemanager", "dbus-daemon"):
        executable = (args.system / "bin" / name).read_bytes()
        fs.add_file(entries, "system/bin/" + name, executable, 0o555)
        queue.extend(needed(executable))
    fs.add_file(entries, "system/etc/dbus.conf", (args.system/"etc/dbus.conf").read_bytes())
    seen = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        relative = next((d/name for d in (Path("lib"), Path("vendor/lib"))
                         if (args.system/d/name).is_file()), None)
        if relative is None:
            raise FileNotFoundError(name)
        blob = (args.system / relative).read_bytes()
        fs.add_file(entries, "system/" + relative.as_posix(), blob, 0o555)
        seen.add(name)
        queue.extend(needed(blob))
    for path in sorted((args.system / "vendor/etc/audio-algorithms").glob("*")):
        if path.is_file():
            fs.add_file(entries, "system/" + path.relative_to(args.system).as_posix(), path.read_bytes())
    entries["vendor"] = fs.Entry(stat.S_IFLNK | 0o777, b"/system/vendor")
    if bool(args.mics) != bool(args.reference):
        p.error("Supply both --mics and --reference")
    block_counts = []
    for src, dest, stride in [(args.mics, "mics.pcm", 128*9*4), (args.reference, "ref.pcm", 384*2*2)]:
        data = src.read_bytes() if src else bytes(stride*10)
        if not data or len(data) % stride:
            raise ValueError(f"{dest} must contain complete audio blocks")
        block_counts.append(len(data) // stride)
        fs.add_file(entries, "bench/" + dest, data)
    if block_counts[0] != block_counts[1]:
        raise ValueError("Microphone and playback fixtures must cover the same time interval")
    for helper in (args.log_sink, args.binder_node, args.start_bus):
        fs.add_file(entries, "bench/" + helper.name, helper.read_bytes(), 0o555)
    if args.loopback:
        fs.add_file(entries, "bench/loopback-mode", args.loopback.encode())
    init = """#!/bin/sh
/bin/mount -t proc proc /proc
/bin/mount -t sysfs sysfs /sys
/bin/mount -t devtmpfs devtmpfs /dev
/bin/mount -t tmpfs tmpfs /tmp
/bin/mount --bind /system /system && /bin/mount -o remount,bind,ro /system || exit 1
mkdir -p /dev/socket /dev/__properties__ /tmp/home
chmod 1777 /tmp
if [ ! -e /dev/binder ]; then
    mkdir -p /dev/binderfs
    mount -t binder binder /dev/binderfs && /bench/binder-node
fi
/bench/log-sink &
export ANDROID_ROOT=/system LD_LIBRARY_PATH=/system/lib:/system/vendor/lib
/system/bin/servicemanager &
/bench/start-bus &
for turn in 1 2 3 4 5; do test -S /dev/socket/dbus && break; sleep 0.2; done
echo BENCH_START
cat /proc/uptime
if [ -f /bench/loopback-mode ]; then
    BENCH_LOOPBACK=$(cat /bench/loopback-mode)
    export BENCH_LOOPBACK
fi
timeout -k 1 180 /bench/driver
result=$?
echo BENCH_EXIT=$result
cat /proc/uptime
if [ "$result" = 0 ]; then
    echo SYNTHETIC_OUTPUT_BEGIN
    base64 /tmp/asr.pcm
    echo SYNTHETIC_OUTPUT_END
fi
sync
/sbin/poweroff -f
"""
    fs.add_file(entries, "bench/init", init.encode(), 0o555)
    overlay = args.output.with_suffix(".overlay.gz")
    fs.write_archive(entries, overlay)
    with args.output.open("wb") as dst:
        for src in (args.base, overlay):
            with src.open("rb") as f:
                shutil.copyfileobj(f, dst)
    print(f"Staged {len(seen)} stock runtime dependencies inside the private initramfs")


if __name__ == "__main__":
    main()
