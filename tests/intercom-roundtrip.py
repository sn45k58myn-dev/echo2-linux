"""Exercise the deployed Linux probe with a synthetic client over trusted HTTPS/WSS."""
import argparse
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from websockets.sync.client import connect


def exercise(origin, passphrase, cafile):
    tls = ssl.create_default_context(cafile=cafile)
    cookies = CookieJar()
    client = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=tls), urllib.request.HTTPCookieProcessor(cookies))

    def http(path, method="GET", data=None, headers=None):
        h = dict(headers or {})
        if method != "GET":
            h["Origin"] = origin
        req = urllib.request.Request(origin + path, data=data, headers=h, method=method)
        with client.open(req, timeout=5) as res:
            return res.read()

    try:
        http("/api/v1/intercom")
    except urllib.error.HTTPError as err:
        if err.code != 401:
            raise
    else:
        raise AssertionError("Private room information was public")
    http("/login", "POST", urllib.parse.urlencode({"password": passphrase}).encode())
    state = json.loads(http("/api/v1/intercom"))["data"]
    if state["backend"] != "probe" or not state["enabled"] or state["busy"]:
        raise ValueError("Select an enabled, idle probe endpoint")
    grant = json.loads(http("/api/v1/intercom/call", "POST", b"{}",
                            {"Content-Type": "application/json"}))["data"]
    cookie = "; ".join(f"{item.name}={item.value}" for item in cookies)
    url = "wss" + origin[len("https"):] + grant["path"]
    tones = set()
    try:
        with connect(url, ssl=tls, origin=origin, additional_headers={"Cookie": cookie},
                     compression=None, open_timeout=5, close_timeout=1) as ws:
            ws.send(json.dumps({"ticket": grant["ticket"]}))
            assert ws.recv(timeout=3) == "ready", "Room declined the audio endpoint"
            for seq in range(72):
                # The incoming phone signal differs from every generated return tone.
                samples = [round(6000 * math.sin(2 * math.pi * 1000 * i / 16000))
                           if seq < 20 else 0 for i in range(320)]
                ws.send(struct.pack(">I", seq) + struct.pack("<320h", *samples))
                packet = ws.recv(timeout=2)
                if not isinstance(packet, bytes) or len(packet) != 644 or struct.unpack(">I", packet[:4])[0] != seq:
                    raise AssertionError("Incomplete or out-of-order room audio")
                samples = struct.unpack("<320h", packet[4:])
                for hz in (440, 660, 880):
                    angle = 2 * math.pi * hz / 16000
                    magnitude = abs(sum(x * complex(math.cos(i*angle), math.sin(i*angle))
                                        for i, x in enumerate(samples))) / len(samples)
                    if magnitude > 800:
                        tones.add(hz)
    finally:
        http("/api/v1/intercom/call", "DELETE", json.dumps({"id": grant["id"]}).encode(),
             {"Content-Type": "application/json"})
    deadline = time.monotonic() + 2
    while True:
        state = json.loads(http("/api/v1/intercom"))["data"]
        if not state["busy"] or time.monotonic() >= deadline:
            break
        time.sleep(.05)
    m = state["metrics"]
    assert tones == {440, 660, 880} and not state["busy"], (tones, state["busy"])
    assert m["from_phone"] == 72 and m["to_phone"] >= 72 and m["probe_replies"] == 1, m
    assert m["phone_peak"] == 6000 and 4200 <= m["phone_peak_rms"] <= 4300, m
    http("/logout", "POST", b"")
    print("Authenticated duplex passed. Phone frames:", m["from_phone"],
          "room frames:", m["to_phone"], "phone peak RMS:", m["phone_peak_rms"],
          "room tones (Hz):", sorted(tones))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--password-file", required=True, type=Path)
    parser.add_argument("--ca")
    options = parser.parse_args()
    origin = options.origin.rstrip("/")
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.username or parsed.query or parsed.fragment:
        parser.error("Use a single HTTPS origin without credentials or a path")
    exercise(origin, options.password_file.read_text().rstrip("\r\n"), options.ca)
