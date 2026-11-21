"""Run the ARM gateway round-trip test with a temporary loopback-only TLS identity."""
import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import math
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import tempfile
import time
import wave

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


ROOT = Path(__file__).resolve().parents[1]


def identity(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Intercom loopback QA")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(hours=2))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), True).sign(key, hashes.SHA256()))
    (folder / "cert").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
    (folder / "password").write_text(secrets.token_urlsafe(32))
    public = key.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(public).digest()).decode()


def microphone_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(
            struct.pack("<h", round(5000 * math.sin(i*2*math.pi*947/48000)) if i<36000 else 0)
            for i in range(96000)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="also drive Chromium with a synthetic microphone")
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/intercom")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
        d = Path(tmp)
        spki = identity(d)
        container = subprocess.check_output([
            "docker", "run", "-d", "--rm", "--read-only", "--cap-drop", "ALL",
            "--platform", "linux/arm/v7", "-p", "127.0.0.1::8443", "-v", f"{d}:/keys:ro",
            "-v", f"{args.bundle.resolve()}:/bundle:ro", "alpine:3.24.4",
            "/bundle/bin/echo2-intercom", "--listen", ":8443", "--allow-calls", "--backend", "probe",
            "--cert", "/keys/cert", "--key", "/keys/key", "--password-file", "/keys/password"], text=True).strip()
        try:
            port = subprocess.check_output(["docker", "port", container, "8443/tcp"], text=True).strip().rsplit(":", 1)[1]
            time.sleep(1)
            subprocess.run([sys.executable, str(ROOT / "tests/intercom-roundtrip.py"),
                            "--origin", f"https://127.0.0.1:{port}", "--ca", str(d / "cert"),
                            "--password-file", str(d / "password")], check=True)
            if args.browser:
                wav = d / "synthetic.wav"
                microphone_wav(wav)
                subprocess.run(["node", str(ROOT / "tests/intercom-browser.cjs"), f"https://127.0.0.1:{port}",
                                str(d / "password"), str(wav), spki], check=True, timeout=60)
        finally:
            subprocess.run(["docker", "stop", container], stdout=subprocess.DEVNULL, check=True)
