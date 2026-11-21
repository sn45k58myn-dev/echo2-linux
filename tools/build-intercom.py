"""Build the standalone Linux intercom, including its mobile UI and licence notices."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "golang:1.27.4-bookworm@sha256:fb7cceb528b47141eccd9b60bf3cbd27d5ef5f8cf5b7d338bec866da495b50dc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("armv7", "amd64"), default="armv7")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "intercom")
    args = parser.parse_args()
    dest = args.output.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        stage = Path(tmp) / "bundle"
        stage.mkdir()
        subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--mount", f"type=bind,src={ROOT / 'services/intercom'},dst=/src,readonly",
            "--mount", f"type=bind,src={stage},dst=/out", "-w", "/src",
            "-e", "CGO_ENABLED=0", "-e", "GOTOOLCHAIN=local", "-e", "GOWORK=off",
            "-e", f"GOARCH={'arm' if args.arch == 'armv7' else 'amd64'}", "-e", "GOARM=7",
            IMAGE, "sh", "-ec",
            "go mod download && go mod verify && mkdir -p /out/bin /out/licenses && "
            "go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' -o /out/bin/echo2-intercom . && "
            "go version -m /out/bin/echo2-intercom >/out/BUILDINFO && "
            "cp LICENSE.upstream LICENSE.websocket NOTICE /out/licenses/ && "
            "cp /usr/local/go/LICENSE /out/licenses/Go-LICENSE && "
            "cd /out && sha256sum bin/echo2-intercom >SHA256SUMS"
        ], check=True)
        if dest.exists():
            old, new = sorted(p.relative_to(dest) for p in dest.rglob("*") if p.is_file()), sorted(
                p.relative_to(stage) for p in stage.rglob("*") if p.is_file())
            if old != new or any((dest / p).read_bytes() != (stage / p).read_bytes() for p in new):
                raise SystemExit("Output already contains a different build; select another --output")
        else:
            shutil.move(str(stage), str(dest))
    print(dest / "bin/echo2-intercom")


if __name__ == "__main__":
    main()
