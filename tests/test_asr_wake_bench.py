import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import wave


SOURCE = Path(__file__).resolve().parents[1] / "tools/asr-wake-bench.py"
spec = importlib.util.spec_from_file_location("asr_wake", SOURCE)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def fragment(data, sizes):
    offset = 0
    i = 0
    while offset < len(data):
        size = sizes[i % len(sizes)]
        yield data[offset:offset + size]
        offset += size
        i += 1


class StreamTest(unittest.TestCase):
    def test_factory_pcm_bit_zero_for_all_signed_values(self):
        values = range(-32768, 32768)
        tag = "<65536h"
        original = struct.pack(tag, *values)
        source = bytearray(original)
        expected = struct.pack(tag, *(value & -2 for value in values))
        self.assertEqual(bench.factory_speech_pcm(source), expected)
        self.assertEqual(source, original)
        with self.assertRaisesRegex(ValueError, "within a 16-bit sample"):
            bench.factory_speech_pcm(b"\0")

    def test_truncated_wav(self):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as fixture:
            fixture.setparams((1, 2, bench.RATE, 0, "NONE", "not compressed"))
            fixture.writeframes(bytes(20000))
        with wave.open(io.BytesIO(stream.getvalue()[:-2]), "rb") as source:
            with self.assertRaisesRegex(ValueError, "truncated WAV"):
                list(bench.wav_chunks(source))

    def test_fragmentation_startup_and_tail(self):
        data = bytes(range(256)) * 80 + bytes(6)
        expected = [data[i:i + 2560] for i in range(0, 20480, 2560)]
        for sizes in ((256,), (17, 1, 4017), (32768,)):
            with self.subTest(sizes=sizes):
                blocks, events = [], []

                def score(block):
                    blocks.append(bytes(block))
                    return 0.8

                bench.analyse(fragment(data, sizes), score, 0.5, events.append)
                self.assertEqual(blocks, expected)
                self.assertEqual([r["end_ms"] for r in events[:-1]], [480])
                self.assertEqual(events[-1]["tail_samples"], 3)
                self.assertEqual(events[-1]["scored_windows"], 3)

    def test_invalid_inputs(self):
        for data, error in ((b"", "insufficient"), (bytes(256), "insufficient"),
                            (bytes(5 * 2560), "insufficient"),
                            (bytes(6 * 2560 + 1), "within a 16-bit sample")):
            with self.subTest(size=len(data)), self.assertRaisesRegex(ValueError, error):
                bench.analyse([data], lambda _: 0.0, 0.5, lambda _: None)

    def test_repeated_wake(self):
        active = {10, 11, 15, 37}
        scores = iter(1.0 if i in active else 0.0 for i in range(40))
        results = []
        bench.analyse([bytes(40 * 2560)], lambda _: next(scores), 0.5, results.append)
        self.assertEqual([r["end_ms"] for r in results[:-1]], [880, 3040])
        self.assertEqual(results[-1]["wakes"], 2)


@unittest.skipUnless(all(os.environ.get(key) for key in
                     ("WAKE_FEATURE_DIR", "WAKE_MODEL", "WAKE_PHRASE_WAV", "WAKE_CONTROL_WAV")),
                     "set the four local model and fixture paths to run ONNX checks")
class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.args = [sys.executable, str(SOURCE), "--features", os.environ["WAKE_FEATURE_DIR"],
                     "--model", os.environ["WAKE_MODEL"]]
        self.phrase = os.environ["WAKE_PHRASE_WAV"]
        self.control = os.environ["WAKE_CONTROL_WAV"]

    def test_detects_phrase_and_rejects_controls(self):
        def invoke(args, data=b""):
            proc = subprocess.run(args, input=data, capture_output=True, check=True, timeout=30)
            return [json.loads(line) for line in proc.stdout.splitlines()]

        positive = invoke(self.args + ["--wav", self.phrase])
        self.assertEqual(positive[-1]["wakes"], 1)
        self.assertEqual(invoke(self.args + ["--wav", self.control])[-1]["wakes"], 0)
        with wave.open(self.phrase) as wav_file:
            pcm = wav_file.readframes(wav_file.getnframes())
        self.assertEqual(positive, invoke(self.args, pcm))
        masked = invoke(self.args + ["--factory-pcm"], pcm)
        self.assertEqual(masked, invoke(self.args + ["--factory-pcm", "--wav", self.phrase]))
        self.assertEqual(masked, invoke(self.args, bench.factory_speech_pcm(pcm)))
        factory = bench.Classifier(Path(os.environ["WAKE_FEATURE_DIR"]), Path(os.environ["WAKE_MODEL"]),
                                   factory_pcm=True)
        split = []
        bench.analyse(fragment(pcm, (1, 5, 351, 2047)), factory.score, 0.5, split.append)
        self.assertEqual(masked, split)
        self.assertEqual(invoke(self.args, bytes(len(pcm)))[-1]["wakes"], 0)
        detector = bench.Classifier(Path(os.environ["WAKE_FEATURE_DIR"]), Path(os.environ["WAKE_MODEL"]))
        irregular = []
        bench.analyse(fragment(pcm, (1, 511, 7, 2999)), detector.score, 0.5, irregular.append)
        self.assertEqual(positive, irregular)


if __name__ == "__main__":
    unittest.main()
