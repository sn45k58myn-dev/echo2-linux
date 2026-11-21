"""Exercise the actual ARM gateway, FIFO bridge and room companion with synthetic audio."""
from contextlib import ExitStack
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def eventually(check, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.02)
    raise AssertionError(f"Timeout waiting for {check}")


def main(d, stack):
    status = dict(muted=False, do_not_disturb=False, output_available=True, volume=50, notification_volume=50)
    active = threading.Event()
    active.set()
    observed = dict(subscriptions=0, cues=0, lights=0, stereo_matches=0)
    room = struct.pack("<h", 1234) * 320
    phone = struct.pack("<h", -4000) * 320
    pipes = d / "pipes"
    pipes.mkdir(mode=0o700)
    config = d / "config"
    config.write_text('{"voice_pipeline_mode":"home-assistant"}')
    Path(str(config) + ".intercom").write_text('{"enabled":true}')
    for name in ("privacy", "dnd"):
        (d / name).write_text("0")
    bus = d / "bus"
    os.mkfifo(bus)
    reader = os.open(bus, os.O_RDONLY | os.O_NONBLOCK)
    stack.callback(os.close, reader)

    def maintain():
        while active.is_set():
            if (d / "lease").exists():
                tmp = d / "ack.writing"
                tmp.write_text(str(int(time.monotonic() * 1000) + 2000))
                tmp.chmod(0o600)
                tmp.replace(d / "ack")
            try:
                block = os.read(reader, 16384)
                observed["stereo_matches"] += block.count(struct.pack("<2h", -4000, -4000))
            except BlockingIOError:
                pass
            time.sleep(.003)

    def adapter(listener, kind):
        while active.is_set():
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                with peer, peer.makefile("rb") as stream:
                    peer.settimeout(1)
                    request = json.loads(stream.readline(4096))
                    cmd = request["cmd"]
                    data = status if cmd == "status" else {}
                    if cmd == "wake_chirp":
                        observed["cues"] += 1
                    if kind == "led":
                        observed["lights"] += 1
                    if kind == "wake":
                        assert cmd == "stream_audio"
                        observed["subscriptions"] += 1
                        data = dict(streaming=True, sample_indexed=True, channels=1, sample_rate=16000,
                                    frame_header_bytes=24, format="pcm_s16_le")
                    response = dict(v=1, id=request["id"], ok=True, data=data)
                    peer.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode())
                    if kind == "wake":
                        index = 1
                        while active.is_set():
                            peer.sendall(struct.pack("<4sHHQII", b"LVS1", 1, 0, index, 320, 0) + room)
                            index += 320
                            time.sleep(.02)
            except (OSError, ValueError):
                continue

    workers = []
    for kind in ("audio", "led", "wake"):
        service = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        service.bind(str(d / kind))
        service.listen(4)
        service.settimeout(.15)
        workers.append(threading.Thread(target=adapter, args=(service, kind)))
    workers.append(threading.Thread(target=maintain))
    for worker in workers:
        worker.start()
    def stop_workers():
        active.clear()
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive(), "Synthetic audio worker failed to exit"
    stack.callback(stop_workers)

    def launch(*args):
        proc = subprocess.Popen(list(map(str, args)))
        def stop():
            proc.terminate()
            proc.wait(timeout=4)
        stack.callback(stop)
        return proc

    launch("/bridge/bin/libreecho-intercomd", "--socket", d/"call", "--config", config, "--privacy-state",
           d/"privacy", "--audio-socket", d/"audio", "--led-socket", d/"led", "--wake-socket", d/"wake",
           "--audio-bus", bus, "--lease", d/"lease", "--assist-ack", d/"ack", "--stats-file", d/"stats")
    eventually((d / "call").exists)
    launch("/bridge/bin/echo2-intercom-bridge", pipes, d/"call", d/"privacy", d/"dnd")
    mic, speaker = pipes/"room-mic.pcm", pipes/"room-speaker.pcm"
    eventually(mic.exists)
    launch("/gateway/bin/echo2-intercom", "--listen", "127.0.0.1:8443", "--allow-calls", "--backend", "fifo",
           "--cert", "/keys/cert", "--key", "/keys/key", "--password-file", "/keys/password", "--room-mic",
           mic, "--room-speaker", speaker, "--privacy-state", d/"privacy", "--dnd-state", d/"dnd")

    tls = ssl.create_default_context(cafile="/keys/cert")
    origin = "https://127.0.0.1:8443"
    jar = CookieJar()
    http = urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls), urllib.request.HTTPCookieProcessor(jar))
    def post(path, body=b"{}", content="application/json"):
        req = urllib.request.Request(origin + path, data=body, headers={"Origin": origin, "Content-Type": content})
        with http.open(req, timeout=3) as res:
            return res.read()
    def login():
        try:
            post("/login", urllib.parse.urlencode({"password": Path("/keys/password").read_text()}).encode(),
                 "application/x-www-form-urlencoded")
            return True
        except urllib.error.URLError:
            return False
    eventually(login, 8)
    assert observed["subscriptions"] == 0
    cookie = "; ".join(f"{item.name}={item.value}" for item in jar)
    def gated(name):
        before = observed["subscriptions"]
        (d / name).write_text("1")
        try:
            post("/api/v1/intercom/call")
            raise AssertionError("Gateway accepted call while gated")
        except urllib.error.HTTPError as error:
            assert error.code == 403, error
        assert before == observed["subscriptions"]
        (d / name).write_text("0")
    gated("privacy")
    gated("dnd")

    # The browser gateway and the native room independently require opt-in.
    # A signed-in gateway user alone cannot open the room mic.
    permission = Path(str(config) + ".intercom")
    permission.write_text('{"enabled":false}')
    grant = json.loads(post("/api/v1/intercom/call"))["data"]
    with connect("wss://127.0.0.1:8443" + grant["path"], ssl=tls, origin=origin,
                 additional_headers={"Cookie": cookie}, compression=None, close_timeout=1) as ws:
        ws.send(json.dumps({"ticket": grant["ticket"]}))
        assert ws.recv(timeout=3) == "ready"
        for seq in range(35):
            try:
                ws.send(struct.pack(">I", seq) + phone)
                assert ws.recv(timeout=.6)[4:] == bytes(640)
            except ConnectionClosed:
                break
        else:
            raise AssertionError("Room opt-out failed to end the call")
    assert observed["subscriptions"] == observed["cues"] == 0, observed
    permission.write_text('{"enabled":true}')
    for number in range(2):
        eventually(mic.exists)
        grant = json.loads(post("/api/v1/intercom/call"))["data"]
        seen = 0
        prior = observed["stereo_matches"]
        with connect("wss://127.0.0.1:8443" + grant["path"], ssl=tls, origin=origin,
                     additional_headers={"Cookie": cookie}, compression=None, close_timeout=1) as ws:
            ws.send(json.dumps({"ticket": grant["ticket"]}))
            assert ws.recv(timeout=3) == "ready"
            for seq in range(90):
                ws.send(struct.pack(">I", seq) + phone)
                packet = ws.recv(timeout=2)
                assert len(packet) == 644 and struct.unpack(">I", packet[:4])[0] == seq
                assert packet[4:] in (room, bytes(640)), "Unexpected microphone signal"
                seen += packet[4:] == room
            assert seen >= 15, (seen, observed)
            assert observed["stereo_matches"] > prior + 4800, observed
            if number == 0:
                (d/"privacy").write_text("1")
                deadline = time.monotonic() + 1
                try:
                    while time.monotonic() < deadline:
                        ws.recv(timeout=.5)
                except ConnectionClosed:
                    pass
                else:
                    raise AssertionError("Privacy did not terminate the call")
        eventually(lambda: not (d/"lease").exists())
        (d/"privacy").write_text("0")
        print(f"Call {number+1}: {seen}/90 reverse frames match virtual mic; "
              f"{observed['stereo_matches']-prior} stereo pairs match phone at system mixer.")
    assert observed["subscriptions"] == 2 and observed["cues"] == 2 and observed["lights"] >= 2, observed
    print("Idle, native opt-out, mute, DND, active privacy cutoff, lease teardown and fresh call passed.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(dir="/tmp") as temp, ExitStack() as stack:
        main(Path(temp), stack)
