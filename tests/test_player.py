"""Run with Python in an ARM Linux environment; ECHO2_PLAYER can select the binary."""
import os
from pathlib import Path
import struct
import subprocess
import unittest


BIN = os.getenv("ECHO2_PLAYER", str(Path(__file__).resolve().parents[1] / "build/echo2-player"))


def render(mode, gain, samples):
    return subprocess.run([BIN, "render", mode, str(gain)], input=samples, capture_output=True)


class PlayerTests(unittest.TestCase):
    def test_stereo_passes_through_across_periods(self):
        source = struct.pack("<hhhh", -32768, 32767, -1, 1) * 1601
        result = render("music", 100, source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, source)

    def test_mono_conversion_with_gain_and_final_frame(self):
        result = render("speech", 50, struct.pack("<hhh", -12, 6, 0))
        expected = [-6, -3, 0, 3, 2, 1, 0, 0, 0]
        self.assertEqual(result.stdout, b"".join(struct.pack("<hh", x, x) for x in expected))
        self.assertEqual(result.returncode, 0)

    def test_partial_trailing_frame_is_rejected(self):
        for mode in ("speech", "music"):
            self.assertNotEqual(render(mode, 10, b"\x01").returncode, 0)

    def test_zero_gain_and_empty_stream(self):
        self.assertEqual(render("speech", 0, b"\xff\x7f").stdout, b"\0" * 12)
        result = render("music", 100, b"")
        self.assertEqual((result.stdout, result.returncode), (b"", 0))

    def test_out_of_range_gain(self):
        for gain in ("101", "-1", "3x"):
            self.assertNotEqual(render("music", gain, b"").returncode, 0)


if __name__ == "__main__":
    unittest.main()
