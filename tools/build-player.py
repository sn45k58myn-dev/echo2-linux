"""Build the independent PCM player from verified TinyALSA source for ARMv7."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TINYALSA = "a7cd26a17fe2add2ff38d2f281ed0b919187a068"
DIGEST = "9a5850a05395978c9eb1a1c8b180f4501c5c3b13534c97921a677d2810a9c91b"


def build(archive, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp).resolve()
        if archive is None:
            archive = stage / "tinyalsa.tar.gz"
            with urlopen(f"https://codeload.github.com/tinyalsa/tinyalsa/tar.gz/{TINYALSA}",
                         timeout=60) as source, archive.open("wb") as dest:
                shutil.copyfileobj(source, dest)
        with archive.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != DIGEST:
                raise SystemExit("TinyALSA source archive checksum mismatch")
        iid = stage / "image-id"
        subprocess.run(["docker", "build", "--platform", "linux/arm/v7", "--iidfile", str(iid),
                        str(ROOT / "tools/player-image")], check=True)
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--platform", "linux/arm/v7",
                        "--mount", f"type=bind,src={archive.resolve()},dst=/input/tinyalsa.tar.gz,readonly",
                        "--mount", f"type=bind,src={ROOT / 'src/player.c'},dst=/input/player.c,readonly",
                        "--mount", f"type=bind,src={stage},dst=/out", iid.read_text().strip()], check=True)
        os.replace(stage / "echo2-player", output)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an existing pinned TinyALSA source archive")
    parser.add_argument("--output", type=Path, default=ROOT / "build/echo2-player")
    args = parser.parse_args()
    build(args.archive, args.output)
