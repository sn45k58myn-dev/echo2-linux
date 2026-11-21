"""Run the room-bridge test inside network-isolated ARM Linux with test-only credentials."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import websockets


ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    identity = runpy.run_path(str(ROOT/"tests/intercom-container.py"))["identity"]
    with tempfile.TemporaryDirectory(dir=ROOT/"build") as temp:
        identity(Path(temp))
        mount = {ROOT/"dist/intercom": "/gateway", ROOT/"dist/intercom-bridge": "/bridge",
                 ROOT/"tests/intercom-bridge.py": "/test.py", Path(temp): "/keys",
                 Path(websockets.__file__).resolve().parent: "/tools/websockets"}
        # Run the timing-sensitive synthetic hardware in the host's native
        # Python. Docker binfmt executes all three real ARMv7 service binaries.
        for program in ("dist/intercom/bin/echo2-intercom", "dist/intercom-bridge/bin/echo2-intercom-bridge",
                        "dist/intercom-bridge/bin/libreecho-intercomd"):
            with (ROOT/program).open("rb") as elf:
                header = elf.read(20)
            if header[:6] != b"\x7fELF\x01\x01" or header[18:20] != b"\x28\x00":
                raise ValueError(f"Expected little-endian 32-bit ARM program: {program}")
        args = ["docker", "run", "--rm", "--network", "none",
                "--cap-drop", "ALL", "--read-only", "--tmpfs", "/tmp", "-e", "PYTHONPATH=/tools"]
        for source, target in mount.items():
            args.extend(["-v", f"{source}:{target}:ro"])
        subprocess.run(args + ["python:3.13-alpine", "python", "/test.py"], check=True)
