import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/export-audio-sources.py"
SPEC = importlib.util.spec_from_file_location("audio_snapshot", SCRIPT)
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def test_uses_only_named_git_blobs_and_exact_tree(self):
        src = self.root / "src"
        src.mkdir()
        (src / "audio.c").write_bytes(b"original source\n")
        nested = src / "cache"
        nested.mkdir()
        (nested / "other.c").write_bytes(b"unselected\n")
        git = ["git", "-C", str(self.root)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["-c", "core.autocrlf=false", "add", "src"], check=True)
        subprocess.run(git + ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                              "-c", "commit.gpgSign=false", "commit", "-qm", "fixture"], check=True)
        commit = snapshot.git(self.root, "rev-parse", "HEAD").decode().strip()
        tree = snapshot.git(self.root, "rev-parse", "HEAD^{tree}").decode().strip()
        source = {"commit": commit, "tree": tree, "groups": {"audio": ["src/*.c"]}}
        (src / "audio.c").write_bytes(b"dirty working tree\n")
        entries = snapshot.source_files(self.root, source)
        self.assertEqual(list(entries), ["src/audio.c"])
        self.assertEqual(list(snapshot.read_blobs(self.root, entries).values()), [b"original source\n"])
        with self.assertRaisesRegex(ValueError, "tree differs"):
            snapshot.source_files(self.root, {**source, "tree": "0" * 40})
        with self.assertRaisesRegex(ValueError, "unmatched"):
            snapshot.source_files(self.root, {**source, "groups": {"audio": ["src/missing.c"]}})

    def test_verify_rejects_modified_missing_and_extra_files(self):
        expected = self.root / "expected"
        actual = self.root / "actual"
        for directory in (expected, actual):
            directory.mkdir()
            (directory / "test.c").write_bytes(b"source\n")
        snapshot.compare(expected, actual)
        (actual / "test.c").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "file differs"):
            snapshot.compare(expected, actual)
        (actual / "test.c").unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            snapshot.compare(expected, actual)
        (actual / "test.c").write_bytes(b"source\n")
        (actual / "extra").touch()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            snapshot.compare(expected, actual)


if __name__ == "__main__":
    unittest.main()
