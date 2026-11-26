import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tarfile
import tempfile
import unittest


BENCH = Path(__file__).resolve().parents[1] / "tools" / "factory-asr-bench"
bundle = runpy.run_path(str(BENCH / "native-bundle.py"))
replay = runpy.run_path(str(BENCH / "native-replay.py"))


class FactoryBenchTests(unittest.TestCase):
    def test_profile_redirects_only_the_calibration_lookup(self):
        mics = {"Num": 7, "Calibration": {"Enable": True, "Path": "/proc/idme/miccal."}}
        profile = {"Hardware Definition": {"Mics": mics}, "Pipelines": {"ASR": {"Example": 123}}}
        result = json.loads(bundle["device_calibration_profile"](json.dumps(profile)))
        mics["Calibration"]["Path"] = "/bench/calibration/miccal."
        self.assertEqual(result, profile)
        with self.assertRaises(ValueError):
            bundle["device_calibration_profile"](json.dumps(result))

    def test_failed_or_repeated_build_preserves_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "private.tgz"
            with self.assertRaisesRegex(ValueError, "missing input"):
                with bundle["new_archive"](dest):
                    raise ValueError("missing input")
            self.assertFalse(dest.exists())
            with bundle["new_archive"](dest) as archive:
                archive.addfile(tarfile.TarInfo("fixture"))
            original = dest.read_bytes()
            with self.assertRaises(FileExistsError):
                with bundle["new_archive"](dest):
                    pass
            self.assertEqual(dest.read_bytes(), original)

    def test_replay_rejects_wrong_stage_before_adb(self):
        with self.assertRaises(ValueError):
            replay["run_window"](b"", b"", 0, 0, adb=[], stage="/run/unrelated")

    @unittest.skipUnless(shutil.which("sh") and os.name == "posix", "POSIX shell")
    def test_board_values_are_read_at_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "out"
            out.mkdir()
            for i in range(7):
                node = root / f"miccal.{i}"
                node.mkdir()
                (node / "value").write_bytes(str(100 + i).encode() + b"\0")
            cmd = ["sh", str(BENCH / "stage-mic-calibration.sh"), str(root), str(out)]
            subprocess.run(cmd, check=True)
            self.assertEqual([p.read_text() for p in sorted(out.iterdir())], [str(100 + i) for i in range(7)])
            for p in out.iterdir():
                p.unlink()
            (root / "miccal.0/value").write_bytes(b"65536\0")
            self.assertNotEqual(subprocess.run(cmd).returncode, 0)
            self.assertFalse(any(out.iterdir()))
