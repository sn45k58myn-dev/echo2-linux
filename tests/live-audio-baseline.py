"""Measure the existing LibreEcho speaker-to-mic path using stats and short cues."""

import argparse
import json
import math
from pathlib import Path
import socket
import statistics
import struct
import subprocess
import time
from contextlib import ExitStack, contextmanager


@contextmanager
def forwarded(adb, service):
    port = subprocess.check_output(
        adb + ["forward", "tcp:0", "localfilesystem:/run/libreecho/" + service + ".sock"],
        text=True, timeout=8).strip()
    try:
        yield int(port)
    finally:
        subprocess.run(adb + ["forward", "--remove", "tcp:" + port], check=True, timeout=8)


def request(port, command, args=None):
    message = {"v": 1, "id": 1, "cmd": command, "args": args or {}}
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.sendall(json.dumps(message).encode() + b"\n")
        with sock.makefile("rb") as reply:
            result = json.loads(reply.readline(32768))
    if not result.get("ok"):
        raise ValueError(f"{command} rejected")
    return result["data"]


def levels(port, seconds):
    end = time.monotonic() + seconds
    values = []
    last_hop = None
    while time.monotonic() < end:
        sample = request(port, "beams")
        if not sample.get("running") or sample.get("age_ms", 1000) > 300:
            raise ValueError("No recent live microphone level")
        if sample["hops"] != last_hop:
            values.append(sample["peak_db"])
            last_hop = sample["hops"]
        time.sleep(0.035)
    if len(values) < 3:
        raise ValueError("Too few independent microphone readings")
    return values


def check(adb):
    def shell(command):
        return subprocess.check_output(adb + ["shell", command], text=True, timeout=5).strip()

    if "target_id=radar_puffin" not in shell("cat /etc/libreecho/target").splitlines():
        raise ValueError("Select the full-size Echo 2")
    with ExitStack() as stack:
        mic = stack.enter_context(forwarded(adb, "mic"))
        audio = stack.enter_context(forwarded(adb, "audio"))
        for trial in range(1, 3):
            if shell("cat /sys/devices/platform/amz_privacy/privacy_state") != "0":
                raise ValueError("Microphone privacy latch closed or unavailable")
            if json.loads(shell("cat /run/libreecho-audio/status.json")).get("state") != "idle":
                raise ValueError("Playback is active. Wait for an idle speaker.")
            quiet = levels(mic, 0.9)
            cue = request(audio, "cue", {"first_hz": 720, "second_hz": 960, "ms": 600})
            if cue.get("suppressed") or cue.get("throttled") or cue.get("playing") is False:
                raise ValueError("Speaker declined the test cue")
            sound = levels(mic, 0.95)
            if shell("cat /sys/devices/platform/amz_privacy/privacy_state") != "0":
                raise ValueError("Microphone privacy changed during measurement")
            before, during = statistics.median(quiet), max(sound)
            print(f"Trial {trial}: background median {before:.1f} dB, "
                  f"cue window peak {during:.1f} dB, rise {during-before:.1f} dB", flush=True)
            time.sleep(1)


def write_pilot(path):
    # 1.25 s, 720/960 Hz, with a 25 ms ramp at each end. Mono, little-endian S16.
    samples = bytearray()
    for i in range(20000):
        frequency = 720 if i < 10000 else 960
        envelope = min(1, i / 400, (19999 - i) / 400)
        value = round(2500 * envelope * math.sin(2 * math.pi * frequency * i / 16000))
        samples.extend(struct.pack("<h", value))
    with path.open("xb") as output:
        output.write(samples)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--serial")
    action.add_argument("--write-pilot", type=Path, help="generate the fixed synthetic lane-test tone locally")
    parser.add_argument("--adb", default="adb")
    options = parser.parse_args()
    if options.write_pilot:
        write_pilot(options.write_pilot)
    else:
        check([options.adb, "-s", options.serial])
