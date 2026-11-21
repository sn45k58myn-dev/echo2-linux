#!/usr/bin/env python3
"""Run the pinned real Sendspin server against a selected development player."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def probe(args, scratch):
    directory = args.sources / "platform/tools/mt8163-arm32/sendspin"
    pin = json.loads((directory/"SOURCE.lock").read_bytes())["identity"]["server_oracle"]
    with args.archive.open("rb") as src:
        if hashlib.file_digest(src, "sha256").hexdigest() != pin["archive_sha256"]:
            raise ValueError("Unexpected aiosendspin server archive")
    with tarfile.open(args.archive) as tar:
        tar.extractall(scratch, filter="data")
    server = scratch / ("aiosendspin-" + pin["commit"])
    if not (server / "aiosendspin").is_dir():
        raise ValueError("Pinned server source layout changed")
    args_list = [sys.executable, directory / "hotstage/ss_probe_serve.py", args.url,
                 "--seconds", str(args.seconds), "--tone", "720", "--level-db", "-22", "--lead", "2"]
    if args.pairing_token_file:
        args_list += ["--pairing-token-file", args.pairing_token_file]
    started = time.monotonic()
    process = subprocess.run(args_list, env={**os.environ, "PYTHONPATH": str(server)}, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=args.seconds + 90)
    output = process.stdout
    # Report event counts from the pinned sender without exposing peer ids,
    # identities or the credentials in protocol negotiation diagnostics.
    events = {}
    for name in ("disconnected", "backpressure", "timeout", "dropped", "retrying", "error"):
        count = sum(name in line.lower() for line in output.splitlines())
        if count:
            events[name] = count
    match = re.search(r"sent_frames (\d+)", output)
    if process.returncode or "group members 1" not in output or not match or "done" not in output:
        raise RuntimeError(f"Sendspin server probe failed (exit {process.returncode}, "
                           f"activated={'group members 1' in output}, events={events})")
    if int(match[1]) < args.seconds * 48000:
        raise ValueError("Server ended early")
    if args.pairing_token_file and "paired by token" not in output:
        raise ValueError("Server did not confirm pairing")
    print(f"Real pinned Sendspin server connected, activated the player and sent {match[1]} frames.")
    timing = re.search(r"sent_frames \d+ in ([0-9.]+)s, last chunk plays in ([-0-9.]+)s", output)
    if timing:
        print(f"Feed time {timing[1]} s, final chunk lead {timing[2]} s, "
              f"whole connection {time.monotonic()-started:.1f} s.")
    if events:
        print("Sender event counts:", events)
    print("Check the receiver's codec and decoded output counters to confirm rendering.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "build/compat-complete")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--url", default="ws://127.0.0.1:18928/sendspin")
    parser.add_argument("--seconds", type=int, default=6, choices=range(2, 61), metavar="2..60")
    parser.add_argument("--pairing-token-file", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        probe(args, Path(tmp))
