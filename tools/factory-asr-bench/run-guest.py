"""Run an offline ARM guest and accept synthetic PCM only on verified success."""
import argparse
import base64
import re
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--guest", type=Path, required=True)
    p.add_argument("--kernel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--image", default="echo2-audio-virtual-bench:dev")
    args = p.parse_args()
    binds = []
    for filename, dest in ((args.kernel, "kernel"), (args.guest, "guest")):
        binds.extend(["--mount", f"type=bind,src={filename.resolve(strict=True)},dst=/{dest},readonly"])
    command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--pids-limit", "32", "--memory", "950m",
               *binds, "--entrypoint", "qemu-system-arm", args.image, "-M", "virt",
               "-cpu", "cortex-a15", "-m", "384M", "-nodefaults", "-display", "none",
               "-monitor", "none", "-nic", "none", "-serial", "stdio", "-no-reboot",
               "-kernel", "/kernel", "-initrd", "/guest", "-append",
               "console=ttyAMA0 rdinit=/bench/init panic=1 quiet"]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=215)
    serial = result.stdout.replace(b"\r", b"")
    before = serial.split(b"SYNTHETIC_OUTPUT_BEGIN", 1)[0]
    for line in before.splitlines():
        if line.startswith((b"AFE:", b"BENCH_", b"stock[", b"CANNOT LINK", b"guest binderfs")):
            print(line.decode("utf-8", "replace"), file=sys.stderr)
    blocks = re.search(rb"^AFE: completed (\d+) blocks$", before, re.M)
    data = re.search(rb"^SYNTHETIC_OUTPUT_BEGIN\n([A-Za-z0-9+/=\n]+)SYNTHETIC_OUTPUT_END$", serial, re.M)
    if result.returncode or b"\nBENCH_EXIT=0\n" not in before or not blocks or not data:
        raise SystemExit("Guest did not complete ASR processing; no PCM accepted")
    pcm = base64.b64decode(data.group(1).replace(b"\n", b""), validate=True)
    if int(blocks[1]) == 0 or len(pcm) != 256 * int(blocks[1]):
        raise SystemExit("Unexpected mono PCM output length")
    with args.output.open("xb") as out:
        out.write(pcm)
    print(f"Wrote {len(pcm) // 2} mono 16 kHz signed 16-bit PCM samples")


if __name__ == "__main__":
    main()
