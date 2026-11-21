#!/usr/bin/env python3
"""Pair two ARM players, stream one marked pilot and compare virtual DAC writes.

Use the same image, sources, binaries and verified server archive as sendspin-bench.py.
Run on a Docker --internal bridge without published ports. The ARM network provider
waits for a non-loopback interface, even though both connections use localhost.
"""
import argparse
from array import array
import asyncio
from bisect import bisect_right
from contextlib import ExitStack
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import runpy
import socket
import struct
import sys
import subprocess
import tarfile
import tempfile
from threading import Event, Thread
import time

ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / "tests/sendspin-bench.py"))
RATE = 48000
CHUNK = 960
SECONDS = 6
# Uneven intervals identify each marker independently of initial silence or trimming.
MARKERS = [round(t * RATE) for t in (0.8, 1.69, 2.95, 4.11, 5.27)]


def start_player(stack, path, binary, port):
    path.mkdir()
    pcm, sink, ctl = path / "output.pcm", path / "dac.sock", path / "ctl.sock"
    for name, args, wait_for in [
        ("sink", [binary / "echo2-sendspin-sink", sink, pcm], sink),
        ("receiver", [binary / "echo2-sendspind", "--run", "--name", path.name,
                      "--port", str(port), "--socket", sink, "--control-socket", ctl,
                      "--state-file", path / "identity"], ctl),
    ]:
        log = stack.enter_context((path / (name + ".log")).open("wb"))
        process = subprocess.Popen(args, stdout=log, stderr=log)
        stack.callback(BENCH["stop"], process)
        BENCH["ready"](wait_for, process)
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3)
        s.connect(str(ctl))
        s.sendall(b'{"v":1,"id":1,"cmd":"pairing_token","args":{}}\n')
        result = json.loads(s.makefile("rb").readline(4096))
    if not result.get("ok"):
        raise RuntimeError("Pairing token unavailable")
    return result["data"]["token"]


def pilot():
    data = array("h")
    for n in range(RATE * SECONDS):
        value = int(1100 * math.sin(2 * math.pi * n * 720 / RATE))
        # A brief higher-amplitude bipolar click drives both virtual DACs identically.
        if any(start <= n < start + 240 for start in MARKERS):
            value = 2400 if n % 48 < 24 else -2400
        data.extend((value, value))
    if sys.byteorder != "little":
        data.byteswap()
    return data.tobytes()


async def stream(peers, programme):
    from aiosendspin.models.types import PairMethod
    from aiosendspin.noise.keys import Identity
    from aiosendspin.noise.pairing import PairingAttempt
    from aiosendspin.noise.pairing_token import decode_psk_token
    from aiosendspin.noise.trust_store import InMemoryServerPairingStore
    from aiosendspin.server import ClientAddedEvent, SendspinServer
    from aiosendspin.server.audio import AudioFormat

    server = SendspinServer(asyncio.get_running_loop(), Identity.generate(), "Group lab",
                            pairing_store=InMemoryServerPairingStore())
    added = asyncio.Queue()
    def event(_server, update):
        if isinstance(update, ClientAddedEvent):
            added.put_nowait(update.client_id)
    server.add_event_listener(event)
    clients = []
    for port, encoded in peers:
        token = decode_psk_token(encoded)
        attempt = PairingAttempt(method=PairMethod.PAIRING_PSK,
                                 client_id=token.client_id, pairing_psk=token.pairing_psk)
        server.connect_to_client(f"ws://127.0.0.1:{port}/sendspin", pairing_attempt=attempt,
                                 retry_initial_connection=True)
        try:
            client_id = await asyncio.wait_for(added.get(), 20)
        except TimeoutError as exc:
            raise RuntimeError(f"Player {len(clients) + 1} failed to pair on {port}") from exc
        if client_id != token.client_id:
            raise ValueError("Unexpected paired identity")
        clients.append(server.get_client(client_id))
        print(f"Player {len(clients)} enrolled", flush=True)
    await asyncio.sleep(2)
    group = clients[0].group
    await group.add_client(clients[1])
    if len(group.clients) != 2:
        raise ValueError("The two independent players did not form one group")
    fmt = AudioFormat(sample_rate=RATE, bit_depth=16, channels=2)
    audio = group.start_stream()
    try:
        for offset in range(0, len(programme), CHUNK * 4):
            audio.prepare_audio(programme[offset:offset + CHUNK * 4], fmt)
            play_us = await audio.commit_audio()
            lead_s = (play_us - server.clock.now_us()) / 1e6
            if lead_s > 2:
                await asyncio.sleep(lead_s - 2)
        await asyncio.sleep(max(0, (play_us - server.clock.now_us()) / 1e6) + 0.75)
    finally:
        await group.stop()
    await asyncio.sleep(0.3)


def observe(paths, writes, quit_event):
    # Each event brackets the time a group of new frames appeared in a sink file.
    sizes = [0, 0]
    previous = time.monotonic_ns()
    while not quit_event.is_set():
        before_pass = time.monotonic_ns()
        for i, path in enumerate(paths):
            count = path.stat().st_size // 4
            if count > sizes[i]:
                writes[i].append((count, previous, time.monotonic_ns()))
                sizes[i] = count
        previous = before_pass
        quit_event.wait(0.002)


def markers(path, writes):
    data = path.read_bytes()
    left = [s[0] for s in struct.iter_unpack("<hh", data)]
    nonzero = sum(s != 0 for s in left)
    if nonzero < 0.90 * RATE * SECONDS:
        raise ValueError(f"Incomplete group audio ({nonzero} nonzero frames)")
    threshold = 0.77 * max(map(abs, left))
    first = []
    last = -RATE
    for i, value in enumerate(left):
        if abs(value) < threshold:
            continue
        if i - last > RATE // 3:
            first.append(i)
        last = i
    if len(first) != len(MARKERS):
        raise ValueError(f"Expected {len(MARKERS)} marker bursts, found {len(first)}")
    for i in range(1, len(first)):
        if abs((first[i] - first[i-1]) - (MARKERS[i] - MARKERS[i-1])) > RATE // 8:
            raise ValueError("Decoded marker sequence differs from transmitted programme")
    counts = [w[0] for w in writes]
    if not counts or counts[-1] <= first[-1]:
        raise ValueError("The virtual DAC observer missed the final pilot marker")
    intervals = [writes[bisect_right(counts, frame)][1:] for frame in first]
    print(f"{path.parent.name}: {len(left)} rendered frames, {nonzero} nonzero, "
          f"{len(first)} matching pilot markers")
    return intervals


def main(args, tmp):
    lock = args.sources / "platform/tools/mt8163-arm32/sendspin/SOURCE.lock"
    pin = json.loads(lock.read_bytes())["identity"]["server_oracle"]
    with args.archive.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != pin["archive_sha256"]:
            raise ValueError("Wrong pinned server archive")
    with tarfile.open(args.archive) as tar:
        tar.extractall(tmp, filter="data")
    sys.path.insert(0, str(tmp / ("aiosendspin-" + pin["commit"])))
    dirs = [tmp / "linux-a", tmp / "linux-b"]
    files = [d / "output.pcm" for d in dirs]
    writes = [[], []]
    programme = pilot()
    with ExitStack() as stack:
        peers = [(port, start_player(stack, d, args.bin, port))
                 for d, port in zip(dirs, (19294, 19295))]
        quit_event = Event()
        thread = Thread(target=observe, args=(files, writes, quit_event), daemon=True)
        thread.start()
        try:
            asyncio.run(asyncio.wait_for(stream(peers, programme), timeout=65))
        finally:
            quit_event.set()
            thread.join()
    for d in dirs:
        log = (d / "receiver.log").read_text(errors="replace")
        if not re.search(r"Processed new codec header:\s*FLAC", log, re.I):
            codecs = re.findall(r"Processed new codec header:\s*(\w+)", log, re.I)
            raise ValueError(f"{d.name} did not confirm a FLAC decoder (reported codecs {codecs})")
    first, second = [markers(path, times) for path, times in zip(files, writes)]
    midpoints = [abs(sum(a) - sum(b)) / 2e6 for a, b in zip(first, second)]
    upper = [max(abs(a[0] - b[1]), abs(a[1] - b[0])) / 1e6 for a, b in zip(first, second)]
    print(f"Both ARMv7 receivers paired and rendered the same {SECONDS}s FLAC programme.")
    print("Virtual file-write offsets, ms:", " ".join(f"{v:.1f}" for v in midpoints),
          f"(largest observed bracket bound {max(upper):.1f} ms; each virtual DAC writes in 5 ms slices).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--bin", type=Path, default=ROOT / "dist/sendspin/bin")
    p.add_argument("--sources", type=Path, default=ROOT / "build/compat-complete")
    logging.getLogger().setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory() as scratch:
        main(p.parse_args(), Path(scratch))
