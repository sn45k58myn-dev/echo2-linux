#!/usr/bin/env python3
"""Build the pinned Linux Sendspin player and sink from the 0.16.8 audio export."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CROSS = "arm-linux-gnueabihf-"
TINYALSA_COMMIT = "a7cd26a17fe2add2ff38d2f281ed0b919187a068"
TINYALSA_SHA = "9a5850a05395978c9eb1a1c8b180f4501c5c3b13534c97921a677d2810a9c91b"


def run(*cmd):
    subprocess.run([str(arg) for arg in cmd], check=True)


def obtain_archives(raw, dest):
    dest.mkdir(parents=True, exist_ok=True)
    entries = [*raw["identity"].values(), *raw["dependencies"]]
    for dep in raw["dependencies"]:
        entries.extend(dep.get("submodules", []))
    for entry in entries:
        name = dest / (entry["commit"] + ".tar.gz")
        if name.exists():
            continue
        with tempfile.NamedTemporaryFile(dir=dest, delete=False) as f:
            temp = Path(f.name)
        try:
            with urlopen(entry["archive_url"], timeout=90) as response, temp.open("wb") as file:
                shutil.copyfileobj(response, file)
            with temp.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != entry["archive_sha256"]:
                    raise ValueError("Invalid archive: " + name.name)
            os.replace(temp, name)
        finally:
            temp.unlink(missing_ok=True)


def board_engine(cc, airplay, tmp, archive, output):
    if archive is None:
        archive = tmp / "tinyalsa.tar.gz"
        with urlopen("https://codeload.github.com/tinyalsa/tinyalsa/tar.gz/" + TINYALSA_COMMIT,
                     timeout=90) as src, archive.open("wb") as out:
            shutil.copyfileobj(src, out)
    with archive.open("rb") as fd:
        if hashlib.file_digest(fd, "sha256").hexdigest() != TINYALSA_SHA:
            raise ValueError("TinyALSA source archive checksum mismatch")
    with tarfile.open(archive) as tar:
        tar.extractall(tmp, filter="data")
    tiny = tmp / ("tinyalsa-" + TINYALSA_COMMIT)
    tiny_sources = ["pcm", "pcm_hw", "mixer", "mixer_hw", "snd_card_plugin"]
    engine_sources = ["audio_engine", "audio_sink", "audio_timing", "audio_visualizer",
                      "aec_reference", "playback_status"]
    run(cc, "-O2", "-std=gnu11", "-D_GNU_SOURCE", "-static", "-I", tiny/"include", "-I", airplay,
        *(airplay/(name+".c") for name in engine_sources),
        *(tiny/"src"/(name+".c") for name in tiny_sources), "-lm", "-o", output)


def build(args, tmp):
    src = args.sources.resolve()
    tools = src / "platform/tools/mt8163-arm32/sendspin"
    airplay = tools.parent / "airplay"
    raw = json.loads((tools / "SOURCE.lock").read_bytes())
    archives = args.archives or ROOT / "build/downloads/sendspin"
    if not args.archives:
        obtain_archives(raw, archives)
    spec = importlib.util.spec_from_file_location("sourceverify", tools / "verify_sendspin_sources.py")
    verify = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = verify
    spec.loader.exec_module(verify)
    lock = verify.load_lock(tools / "SOURCE.lock")
    patches = verify.load_patch_inventory(lock, tools / "SOURCE.lock", tools / "patches")
    stage, receipt = tmp / "src", tmp / "receipt.json"
    verify.stage(lock, archives, stage, receipt, patches)
    verify.verify_receipt(lock, stage, receipt, archives, patch_map=patches)
    names = {"identity::sdk": "SDK", "ArduinoJson": "ARDUINOJSON", "micro-flac": "MICRO_FLAC",
             "IXWebSocket": "IXWEBSOCKET", "noise-c": "NOISE_C"}
    cc = args.cross + "gcc"
    # Apply Linux integration changes only to a disposable copy. Preserve
    # the original, verifiable 0.16.8 snapshot for consumers and auditing.
    ui = tmp / "ui"
    shutil.copytree(src / "ui", ui)
    delta = ROOT / "services/sendspin/data-capacity-reply.patch"
    subprocess.run(["git", "apply", "--no-index", "--whitespace=error", str(delta)],
                   cwd=ui, check=True)

    # Run a real socket regression on the build host. Its queue fills, refuses a
    # DATA request after its deadline, and has to accept more music after draining.
    run(os.environ.get("HOSTCC", "cc"), "-O2", "-c", airplay / "audio_sink.c", "-o", tmp / "sink.o")
    adapter = ui / "src/adapter/sendspin"
    run(os.environ.get("HOSTCXX", "c++"), "-O2", "-pthread", "-std=c++20", "-I", adapter,
        "-I", airplay, "-I", ui / "tests", adapter / "engine_sink.cpp",
        ROOT / "tests/sendspin-backpressure.cpp", tmp / "sink.o", "-o", tmp / "backpressure-test")
    subprocess.run([tmp / "backpressure-test"], cwd=tmp, check=True)
    b = tmp / "cmake"
    run("cmake", "-S", adapter, "-B", b, "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_SYSTEM_NAME=Linux", "-DCMAKE_SYSTEM_PROCESSOR=arm",
        "-DCMAKE_C_COMPILER=" + cc, "-DCMAKE_CXX_COMPILER=" + args.cross + "g++",
        "-DCMAKE_EXE_LINKER_FLAGS=-static",
        *(f"-DSENDSPIN_{key}_DIR={stage / lock[name]['path']}" for name, key in names.items()))
    run("cmake", "--build", b, "--target", "sendspind", "-j", args.jobs)
    output = tmp / "bundle"
    bins = output / "bin"
    bins.mkdir(parents=True)
    run(args.cross + "strip", "-o", bins / "echo2-sendspind", b / "sendspind")
    run(cc, "-std=c11", "-O2", "-static", "-Wall", "-Wextra", "-I", airplay,
        ROOT / "services/sendspin/sink.c", airplay / "audio_sink.c", "-o", bins / "echo2-sendspin-sink")
    run(args.cross + "strip", bins / "echo2-sendspin-sink")
    board_engine(cc, airplay, tmp, args.tinyalsa_archive, bins / "echo2-board-audio")
    run(args.cross + "strip", bins / "echo2-board-audio")
    for binary in bins.iterdir():
        hdr = subprocess.check_output([args.cross + "readelf", "-hW", binary], text=True)
        layout = subprocess.check_output([args.cross + "readelf", "-lWd", binary], text=True)
        if "ARM" not in hdr or "ELF32" not in hdr or "INTERP" in layout or "(NEEDED)" in layout:
            raise ValueError("Expected static ARM32 output: " + binary.name)
    licenses = output / "licenses"
    licenses.mkdir()
    shutil.copyfile(src / "ui/LICENSE", licenses / "LibreEcho-UI-LICENSE")
    shutil.copyfile(src / "platform/COPYING", licenses / "LibreEcho-Platform-COPYING")
    shutil.copyfile(src / "ui/THIRD_PARTY_NOTICES.md", licenses / "LibreEcho-UI-NOTICES")
    for name, dep in lock.items():
        directory = stage / dep["path"]
        libname = name.split("::")[-1].replace("/", "-")
        for filename in ["LICENSE", "LICENSE.txt", "COPYING", "license.txt", "LICENSE.md"]:
            if (directory / filename).is_file():
                shutil.copyfile(directory / filename, licenses / f"{libname}-{filename}")
    sysroot_notices = args.runtime_notices
    for filename in ["glibc-copyright", "gcc-runtime-copyright"]:
        shutil.copyfile(sysroot_notices / filename, licenses / filename)
    shutil.copyfile(tools / "SOURCE.lock", licenses / "SOURCE.lock")
    shutil.copyfile(delta, licenses / delta.name)
    shutil.copyfile(ROOT / "licenses/tinyalsa.txt", licenses / "TinyALSA-NOTICE")
    sums = [(hashlib.sha256(f.read_bytes()).hexdigest(), f.name) for f in sorted(bins.iterdir())]
    (output / "SHA256SUMS").write_text("".join(f"{sha}  bin/{name}\n" for sha, name in sums))
    with tempfile.TemporaryDirectory(dir=args.output.parent) as publish:
        candidate = Path(publish) / "bundle"
        shutil.copytree(output, candidate)
        candidate.rename(args.output)
    print((args.output / "SHA256SUMS").read_text(), end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "build/compat-complete")
    parser.add_argument("--archives", type=Path, help="offline archives named <pinned commit>.tar.gz")
    parser.add_argument("--cross", default=DEFAULT_CROSS)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--runtime-notices", type=Path, required=True,
                        help="directory containing the compiler's GCC runtime and glibc copyright files")
    parser.add_argument("--tinyalsa-archive", type=Path, help="offline pinned TinyALSA source archive")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/sendspin")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.output.exists() or args.jobs < 1:
        parser.error("select a new output directory and positive job count")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        build(args, Path(work))
