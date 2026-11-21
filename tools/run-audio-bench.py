#!/usr/bin/env python3
"""Start an expiring ARMv7 audio guest. Docker publishes only the requested addresses."""
import argparse
import ipaddress
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "echo2-audio-virtual-bench:dev"


def address(value):
    ip = ipaddress.IPv4Address(value)
    if ip.is_unspecified or not (ip.is_private or ip.is_loopback) or ip.is_multicast:
        raise argparse.ArgumentTypeError("choose a loopback or specific private LAN address")
    return str(ip)


def mount(source, dest):
    path = source.resolve(strict=True)
    return ["--mount", f"type=bind,src={path},dst={dest},readonly"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kernel", required=True, type=Path)
    p.add_argument("--netboot", required=True, type=Path)
    p.add_argument("--private", type=Path, required=True,
                   help="directory containing server.crt, server.key and password")
    p.add_argument("--base", type=Path, default=ROOT / "build/echo2-linux-base.cpio.gz")
    p.add_argument("--services", type=Path, default=ROOT / "build/echo2-audio-services.cpio.gz")
    p.add_argument("--bind", type=address, default="127.0.0.1", help="intercom HTTPS host address")
    p.add_argument("--https-port", type=int, default=9443)
    p.add_argument("--sendspin-port", type=int, default=18928, help="loopback-only test port")
    p.add_argument("--seconds", type=int, default=3600)
    p.add_argument("--name", default="echo2-linux-audio-bench", help="Docker container name")
    args = p.parse_args()
    if not all(1 <= n <= 65535 for n in (args.https_port, args.sendspin_port)):
        p.error("ports must be between 1 and 65535")
    if not 30 <= args.seconds <= 86400:
        p.error("choose a test window between 30 and 86400 seconds")
    if not re.fullmatch(r"echo2-linux-audio-[a-z0-9-]{1,40}", args.name):
        p.error("choose a name beginning with echo2-linux-audio- and a short lowercase suffix")
    cmd = ["docker", "run", "--detach", "--rm", "--name", args.name, "--platform", "linux/amd64",
           "--read-only", "--tmpfs", "/tmp:size=512m,mode=1777", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "-e", "PYTHONDONTWRITEBYTECODE=1",
           "-p", f"{args.bind}:{args.https_port}:8443",
           "-p", f"127.0.0.1:{args.sendspin_port}:8928"]
    cmd += mount(ROOT / "tools", "/bench/tools")
    for path, dest in [(args.kernel, "vmlinuz"), (args.netboot, "netboot"),
                       (args.base, "base.gz"), (args.services, "services.gz")]:
        cmd += mount(path, "/in/" + dest)
    for filename in ("server.crt", "server.key", "password"):
        cmd += mount(args.private / filename, "/private/" + filename)
    cmd += [IMAGE, "python3", "/bench/tools/audio-bench/run.py",
            "--kernel", "/in/vmlinuz", "--netboot", "/in/netboot", "--base", "/in/base.gz",
            "--services", "/in/services.gz", "--private", "/private", "--seconds", str(args.seconds)]
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", IMAGE,
                    str(ROOT / "tools/audio-bench")], check=True)
    subprocess.run(cmd, check=True)
    print(f"Intercom: https://{args.bind}:{args.https_port}/intercom")
    print(f"Sendspin test listener: localhost:{args.sendspin_port}")


if __name__ == "__main__":
    main()
