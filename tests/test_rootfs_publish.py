import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "tools/build-rootfs.py"
spec = importlib.util.spec_from_file_location("echo2_rootfs", SOURCE)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


class RootfsPublish(unittest.TestCase):
    def test_retry_and_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "image.cpio.gz"
            old = {"one": builder.Entry(0o100644, b"a")}
            new = {"one": builder.Entry(0o100644, b"b")}
            with patch("builtins.print"):
                builder.publish(old, out, builder.write_archive, False, [])
                sha = builder.digest(out)
                builder.publish(old, out, builder.write_archive, False, [])
                with self.assertRaisesRegex(ValueError, "differs"):
                    builder.publish(new, out, builder.write_archive, False, [])
                self.assertEqual(sha, builder.digest(out))
                builder.publish(new, out, builder.write_archive, True, [])
                self.assertNotEqual(sha, builder.digest(out))

    def test_replace_protects_sources_inputs_and_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "voice"
            bundle.mkdir()
            inputs = [Path(tmp) / "archive", bundle]
            for path in (SOURCE, inputs[0], bundle / "SHA256SUMS", bundle):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    builder.check_destination(path, inputs, True)


if __name__ == "__main__":
    unittest.main()
