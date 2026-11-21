import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "tools/build-audio-feature.py"
spec = importlib.util.spec_from_file_location("audio_feature", SOURCE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class FeatureBundle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bundle = Path(temp.name)
        (self.bundle / "bin").mkdir()
        (self.bundle / "licenses").mkdir()
        (self.bundle / "licenses/NOTICE").write_bytes(b"fixture licence")
        # One executable segment in a little-endian ARM32 EABI5 ELF.
        sample = bytearray(84)
        sample[:7] = bytes.fromhex("7f454c46010101")
        struct.pack_into("<HH", sample, 16, 2, 40)
        struct.pack_into("<I", sample, 28, 52)
        struct.pack_into("<I", sample, 36, 5 << 24)
        struct.pack_into("<HH", sample, 42, 32, 1)
        struct.pack_into("<8I", sample, 52, 1, 0, 0, 0, 84, 84, 5, 4)
        self.program = self.bundle / "bin/player"
        self.program.write_bytes(sample)
        self.receipt = self.bundle / "SHA256SUMS"
        self.receipt.write_text(hashlib.sha256(sample).hexdigest() + "  bin/player\n")

    def package(self):
        entries = {}
        builder.add_bundle(entries, self.bundle, "example", ("player",))
        return entries

    def test_receipt_and_licence(self):
        entries = self.package()
        self.assertIn("usr/bin/player", entries)
        self.assertIn("usr/share/doc/echo2-example/licenses/NOTICE", entries)
        self.assertIn("usr/share/doc/echo2-example/SHA256SUMS", entries)

    def test_rejects_modified_executable(self):
        with self.program.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "differs from its build receipt"):
            self.package()

    def test_rejects_extra_inventory(self):
        with self.receipt.open("a") as stream:
            stream.write("0" * 64 + "  bin/unexpected\n")
        with self.assertRaisesRegex(ValueError, "unexpected example executable inventory"):
            self.package()


if __name__ == "__main__":
    unittest.main()
