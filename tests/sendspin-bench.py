#!/usr/bin/env python3
"""Local-only encrypted, paired Sendspin stream through the real ARM binaries."""
import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def ready(path, process):
    until = time.monotonic() + 15
    while not path.exists():
        if process.poll() is not None or time.monotonic() > until:
            raise RuntimeError("Service did not create " + path.name)
        time.sleep(0.05)


def stop(proc):
    if proc and proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test(args, work):
    sock, ctl = work / "dac.sock", work / "ctl.sock"
    raw, token = work / "synthetic.pcm", work / "pairing"
    receiver, sink = None, None
    with (work / "receiver.log").open("wb") as log, (work / "dac.log").open("wb") as out:
        try:
            sink = subprocess.Popen([args.bin / "echo2-sendspin-sink", sock, raw], stderr=out)
            ready(sock, sink)
            receiver = subprocess.Popen([args.bin / "echo2-sendspind", "--run", "--name", "Linux lab",
                                         "--socket", sock, "--control-socket", ctl, "--port", "19284",
                                         "--state-file", work / "identity"], stdout=log, stderr=log)
            ready(ctl, receiver)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(5)
                client.connect(str(ctl))
                client.sendall(b'{"v":1,"id":1,"cmd":"pairing_token","args":{}}\n')
                answer = json.loads(client.makefile("rb").readline(4096))
            if not answer.get("ok"):
                raise RuntimeError("No pairing token issued")
            with token.open("x") as file:
                os.chmod(token, 0o600)
                file.write(answer["data"]["token"])
            subprocess.run([sys.executable, ROOT / "tests/sendspin-stream.py", "--sources", args.sources,
                            "--archive", args.archive, "--url", "ws://127.0.0.1:19284/sendspin",
                            "--seconds", str(args.seconds), "--pairing-token-file", token],
                           check=True, timeout=args.seconds + 100)
        finally:
            stop(receiver)
            stop(sink)
    log_text = (work / "receiver.log").read_text(errors="replace")
    counters = (work / "dac.log").read_text(errors="replace")
    result = re.search(r"virtual-dac final frames=(\d+) nonzero=(\d+) peak=(\d+)", counters)
    if not result or not re.search(r"Processed new codec header:\s*FLAC", log_text, re.I):
        raise ValueError("No confirmed decoded FLAC session. DAC counters: " + counters[-300:])
    frames, nonzero, peak = map(int, result.groups())
    data = raw.read_bytes()
    # A few zero crossings and startup trims are expected. A wrongly tagged DAC
    # clock used to drop over 14% of this pilot while still passing a tone check.
    expected_channel_samples = args.seconds * 48000 * 2
    if (len(data) != frames * 4 or frames < (args.seconds-1) * 48000
            or nonzero < expected_channel_samples * 0.90 or peak < 100):
        raise ValueError(f"Incomplete or silent PCM: {frames=} {nonzero=} {peak=}")
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    # Confirm 720 Hz in the centre of the received programme, away from startup and tail.
    x = samples[len(samples)//2:len(samples)//2+4800*2:2]
    def amplitude(freq):
        step = 2 * math.pi * freq / 48000
        return math.hypot(sum(v * math.cos(i*step) for i,v in enumerate(x)),
                          sum(v * math.sin(i*step) for i,v in enumerate(x))) / len(x)
    if amplitude(720) < max(30, 5 * amplitude(1200)):
        raise ValueError("Rendered signal did not contain the sent 720 Hz tone")
    print(f"Paired FLAC stream rendered {frames} frames, {nonzero} nonzero samples, "
          f"peak {peak}, correct 720 Hz pilot. PCM SHA256 {hashlib.sha256(data).hexdigest()}")
    offsets = [abs(int(us)) for us in re.findall(r"Lost sync \((-?\d+)us off\)", log_text)]
    print(f"Player reported {len(offsets)} sync losses, largest {max(offsets, default=0)} us.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=6, choices=range(2, 61), metavar="2..60")
    parser.add_argument("--bin", type=Path, default=ROOT / "dist/sendspin/bin")
    parser.add_argument("--sources", type=Path, default=ROOT / "build/compat-complete")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        test(args, Path(tmp))
