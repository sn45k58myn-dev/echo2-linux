"""Feed a bounded, already captured window into the Echo's native factory worker."""
from concurrent.futures import ThreadPoolExecutor
import re
import secrets
import shlex
import socket
import struct
import subprocess
import time


FRAME = struct.Struct("<QQII")


def exact(sock, count):
    data = bytearray(count)
    view = memoryview(data)
    pos = 0
    while pos < count:
        n = sock.recv_into(view[pos:])
        if not n:
            raise EOFError("Native factory replay stopped early")
        pos += n
    return data


def run_window(mic, stereo, start_sample, start_ns, *, adb, stage, collect_pcm=True):
    """Return (mono-or-None, scalars). No PCM ever enters ADB shell text or a file."""
    if not re.fullmatch(r"/run/echo2-native-asr-bench-[a-zA-Z0-9_-]{1,32}", stage):
        raise ValueError("Use a validated private /run stage")
    mic = memoryview(mic).cast("B")
    stereo = memoryview(stereo).cast("B")
    blocks, partial = divmod(len(mic), 3456)
    if partial or not 1 <= blocks <= 7500 or len(stereo) != blocks*1536:
        raise ValueError("Replay requires 1 to 7500 aligned, complete 8 ms blocks")
    name = "replay-" + secrets.token_hex(8)
    target = stage + "/" + name
    output = bytearray() if collect_pcm else None
    start = time.monotonic()
    worker = subprocess.Popen([*adb, "shell", "cd " + shlex.quote(stage) +
                               " && ./socket-once " + name + "; echo E2_EXIT=$?"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    port = None
    try:
        # ADB shell has varied CRLF handling; audio uses the separate Unix stream.
        for _ in range(40):
            check = subprocess.check_output([*adb, "shell", "if test -S " + shlex.quote(target) +
                                             "; then echo E2_READY; fi"], timeout=3)
            if check.strip() == b"E2_READY":
                break
            if worker.poll() is not None:
                raise RuntimeError("Native listener failed to start")
            time.sleep(.05)
        else:
            raise TimeoutError("Native listener did not become ready")
        number = subprocess.check_output([*adb, "forward", "tcp:0", "localfilesystem:"+target],
                                         timeout=5).strip()
        if not number.isdigit():
            raise RuntimeError("Unexpected ADB forwarding response")
        port = int(number)
        locked = 0
        spans = []
        with socket.create_connection(("127.0.0.1", port), timeout=10) as channel:
            channel.settimeout(75)
            if exact(channel, 8) != b"E2ASRDY\n":
                raise RuntimeError("Native factory worker did not initialize")

            def pump():
                try:
                    for first in range(0, blocks, 16):
                        batch = bytearray()
                        for i in range(first, min(first+16, blocks)):
                            batch += FRAME.pack(start_sample+i*128, start_ns+(i+1)*8000000, 2, 128)
                            batch += mic[i*3456:(i+1)*3456]
                            batch += stereo[i*1536:(i+1)*1536]
                        channel.sendall(batch)
                    # The legacy ADB forward closes both directions when it sees TCP FIN.
                    # A bounded end record lets the server finish its reply before FIN.
                    channel.sendall(FRAME.pack(start_sample+blocks*128, 0, 0, 0))
                except BaseException:
                    channel.shutdown(socket.SHUT_RDWR)
                    raise

            with ThreadPoolExecutor(max_workers=1) as executor:
                sent = executor.submit(pump)
                try:
                    for i in range(blocks):
                        data = exact(channel, 280)
                        sample, end_ns, flags, n = FRAME.unpack_from(data)
                        if (sample, end_ns, n) != (start_sample+i*128, start_ns+(i+1)*8000000, 128) or flags not in (2,3):
                            raise RuntimeError("Factory replay changed frame timing or flags")
                        if flags & 1:
                            locked += 1
                            if spans and spans[-1][1] == sample:
                                spans[-1][1] += 128
                            else:
                                spans.append([sample, sample+128])
                        if collect_pcm:
                            output.extend(data[24:])
                    if channel.recv(1):
                        raise ValueError("Extra factory replay output")
                    sent.result()
                except BaseException:
                    channel.shutdown(socket.SHUT_RDWR)
                    raise
        log, _ = worker.communicate(timeout=8)
        timing = re.search(rb"factory blocks=(\d+) locked_events=(\d+) lost_events=(\d+) process_cpu_ms=(\d+)", log)
        if not timing or int(timing[1]) != blocks or b"E2_EXIT=0" not in log:
            raise RuntimeError("Native worker completion was not verified")
        metrics = {"blocks": blocks, "reference_supplied": True, "locked_blocks": locked,
                   "observed_reference_lock": bool(locked), "lock_intervals_samples": spans,
                   "lock_events": int(timing[2]), "loss_events": int(timing[3]),
                   "process_cpu_ms": int(timing[4]), "replay_wall_seconds": round(time.monotonic()-start, 3)}
        return output, metrics
    finally:
        if port is not None:
            subprocess.run([*adb, "forward", "--remove", "tcp:"+str(port)], capture_output=True, timeout=5)
        if worker.poll() is None:
            try:
                worker.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.communicate(timeout=3)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify the worker with one second of synthetic silence.")
    parser.add_argument("--serial", required=True, help="Previously authorized ADB device")
    parser.add_argument("--stage", required=True, help="Exclusive ramfs test directory")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    _, metrics = run_window(bytes(128 * 3456), bytes(128 * 1536), 0, 1_000_000_000,
                            adb=[args.adb, "-s", args.serial], stage=args.stage, collect_pcm=False)
    print(f"Verified {metrics['blocks']} timestamped blocks of synthetic input.")
