"""Package an owner's stock DSP into a private, isolated ARMv7 userspace."""
import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import runpy
import tarfile
import tempfile


@contextmanager
def new_archive(output):
    """Publish a complete private archive or leave the destination untouched."""
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        candidate = Path(scratch) / "bundle"
        with candidate.open("xb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz", compresslevel=3) as tar:
                yield tar
        candidate.chmod(0o600)
        os.link(candidate, output)


def device_calibration_profile(contents):
    profile = json.loads(contents)
    mics = profile["Hardware Definition"]["Mics"]
    calibration = mics["Calibration"]
    if (mics["Num"] != 7 or calibration["Enable"] is not True
            or calibration["Path"] != "/proc/idme/miccal."):
        raise ValueError("Unexpected factory mic calibration profile")
    calibration["Path"] = "/bench/calibration/miccal."
    return (json.dumps(profile, indent=2) + "\n").encode()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("assets", type=Path, help="private directory containing system, driver and helpers")
    p.add_argument("output", type=Path)
    p.add_argument("--fixture", type=Path, help="optional prefix of a synthetic .mic.i32 / .ref.s16 pair")
    p.add_argument("--rendered-config", type=Path, help="generated asp.cfg for post-DSP playback input")
    p.add_argument("--device-mic-calibration", action="store_true",
                   help="Read the seven calibration values from the Echo running the test")
    a = p.parse_args()
    root = a.assets
    system = root / "system"
    needed = runpy.run_path(str(Path(__file__).with_name("make-guest.py")))["needed"]
    profile_dir = system / "vendor/etc/audio-algorithms"
    if not (profile_dir / "asp.cfg").is_file():
        p.error("Owner-supplied system tree is missing its asp.cfg profile")
    with new_archive(a.output) as tar:
        def add(name, source, mode=0o555):
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.size = len(source)
            tar.addfile(info, io.BytesIO(source))
        def directory(name, mode=0o755):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = mode
            tar.addfile(info)
        def link(name, target):
            info = tarfile.TarInfo(name)
            info.type, info.linkname, info.mode = tarfile.SYMTYPE, target, 0o777
            tar.addfile(info)
        for d in ("jail", "jail/bench", "jail/dev", "jail/dev/__properties__", "jail/system",
                  "jail/system/bin", "jail/system/lib", "jail/system/etc", "jail/system/vendor",
                  "jail/system/vendor/lib", "jail/system/vendor/etc", "jail/system/vendor/etc/audio-algorithms"):
            directory(d)
        for d in ("jail/tmp", "jail/dev/socket"):
            directory(d, 0o1777)
        if a.device_mic_calibration:
            directory("jail/bench/calibration")
            add("stage-mic-calibration.sh", Path(__file__).with_name("stage-mic-calibration.sh").read_bytes())
        link("jail/vendor", "/system/vendor")
        add("run-private", (root/"private-exec").read_bytes())
        add("socket-once", (root/"native-socket-once").read_bytes())
        add("session.sh", Path(__file__).with_name("native-fixture.sh").read_bytes())
        add("jail/bench/start-bus", (root/"start-bus").read_bytes())
        add("jail/bench/log-sink", (root/"log-sink").read_bytes())
        seeds = [(root/"driver", "jail/bench/driver"),
                 (root/"native-stream", "jail/bench/native-stream"),
                 (system/"bin/dbus-daemon", "jail/system/bin/dbus-daemon")]
        add("jail/system/bin/linker", (system/"bin/linker").read_bytes())
        add("jail/system/etc/dbus.conf", (system/"etc/dbus.conf").read_bytes(), 0o444)
        for file in sorted((system/"vendor/etc/audio-algorithms").glob("*")):
            if file.is_file():
                source = a.rendered_config if file.name == "asp.cfg" and a.rendered_config else file
                contents = source.read_bytes()
                if file.name == "asp.cfg" and a.device_mic_calibration:
                    contents = device_calibration_profile(contents)
                add("jail/system/vendor/etc/audio-algorithms/"+file.name, contents, 0o444)
        seen = set()
        while seeds:
            file, destination = seeds.pop()
            if destination in seen:
                continue
            seen.add(destination)
            contents = file.read_bytes()
            add(destination, contents)
            for name in needed(contents):
                rel = next(d/name for d in [Path("lib"), Path("vendor/lib")] if (system/d/name).is_file())
                path = root/"native-shm.so" if name == "libshm.so" else system/rel
                seeds.append((path, "jail/system/"+rel.as_posix()))
        if a.fixture:
            for ext, dest in [(".mic.i32", "mics.pcm"), (".ref.s16", "ref.pcm")]:
                add("jail/bench/"+dest, a.fixture.with_suffix(ext).read_bytes(), 0o444)


if __name__ == "__main__":
    main()
