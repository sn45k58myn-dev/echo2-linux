"""Build the optional room bridge and locked 0.16.8 companion for ARMv7."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "67b164b60b0d9b2792bf5afe9dc76c45baeb1187e69860ddee9f05332bd50812"
TAG = "echo2-linux-bridge-build"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(export, output):
    snapshot = (export / "snapshot.json").read_bytes()
    if digest(snapshot) != SNAPSHOT:
        raise ValueError("Use the verified 0.16.8 export from tools/export-audio-sources.py")
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        staging = Path(temp)
        source, result = staging / "source", staging / "result"
        result.mkdir()
        for filename, record in json.loads(snapshot)["files"].items():
            if not filename.startswith(("ui/src/", "ui/LICENSE")):
                continue
            content = (export / filename).read_bytes()
            if digest(content) != record["sha256"]:
                raise ValueError(f"Unexpected content in {filename}")
            target = source / filename.removeprefix("ui/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        subprocess.run(["docker", "build", "--platform", "linux/arm/v7", "-q", "-t", TAG,
                        str(ROOT / "tools/player-image")], check=True)
        subprocess.run(["docker", "run", "--rm", "--platform", "linux/arm/v7", "--network", "none",
                        "-v", f"{source}:/src:ro", "-v", f"{ROOT / 'services/intercom-bridge'}:/project:ro",
                        "-v", f"{result}:/out", "--entrypoint", "sh", TAG, "/project/build.sh"], check=True)
        if output.is_symlink():
            raise ValueError("Refusing a symlink at the output path")
        if output.exists():
            paths = sorted(p.relative_to(result) for p in result.rglob("*"))
            if (paths != sorted(p.relative_to(output) for p in output.rglob("*")) or
                any((output / p).is_symlink() or (result / p).is_dir() != (output / p).is_dir() or
                    (result / p).is_file() and (result / p).read_bytes() != (output / p).read_bytes() for p in paths)):
                raise ValueError("Output contains a different bundle; select a new --output directory")
        else:
            result.rename(output)
    print((output / "SHA256SUMS").read_text().strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "build/compat-complete")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/intercom-bridge")
    options = parser.parse_args()
    build(options.sources, options.output)
