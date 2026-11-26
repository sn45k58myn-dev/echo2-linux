import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "tools/factory-asr-bench/score-live-window.py"
spec = importlib.util.spec_from_file_location("factory_trial_score", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TrialScoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.model = Path(tmp.name) / "model.onnx"
        self.model.write_bytes(b"fake model for timing tests")
        self.scorer = module.PairScorer(tmp.name, self.model,
                       expected_model_sha256=hashlib.sha256(self.model.read_bytes()).hexdigest())

    def test_timed_targets_edges_and_controls(self):
        first = 1234
        ticks = (10, 35, 62, 92)
        size = 100 * module.wake.BLOCK
        fake_pcm = bytearray(2 * size)
        for block in ticks:
            fake_pcm[(block - 1) * 2 * module.wake.BLOCK] = 1
        def detector(_features, _model, *, factory_pcm):
            class Score:
                def score(self, block):
                    return 0.9 if block[0] and not factory_pcm else 0.05
            return Score()
        windows = [
            dict(label="first", kind="wake", start_sample=first + 9*1280, end_sample=first + 12*1280),
            dict(label="edge", kind="wake", start_sample=first + 36*1280, end_sample=first + 38*1280,
                 possible_start_sample=first + 34*1280),
            dict(label="control", kind="control", start_sample=first + 60*1280, end_sample=first + 68*1280),
        ]
        with patch.object(module.wake, "Classifier", side_effect=detector):
            result = self.scorer.score_capture(fake_pcm, fake_pcm, windows, start_sample=first)
        self.assertEqual([r["verdict"] for r in result["baseline"]["trials"]],
                         ["hit", "timing_uncertain", "false_wake"])
        self.assertEqual([r["verdict"] for r in result["factory"]["trials"]], ["miss", "miss", "clear"])
        self.assertEqual(result["baseline"]["outside_wake_samples"], [first + 92*1280])
        self.assertEqual(result["baseline"]["trials"][1]["edge_wake_samples"], [first + 35*1280])
        self.assertAlmostEqual(result["baseline"]["input_peak_dbfs"], -90.308999, places=5)
        self.assertEqual(result["baseline"]["input_clipped_samples"], 0)

    def test_monotonic_bracket(self):
        times = module.laptop_window("test", "wake", 1_000_000_000, 1_010_000_000,
                                     1_800_000_000, 100, 600, start_sample=64)
        self.assertEqual([times[k]-64 for k in ("possible_start_sample","start_sample",
                                                 "end_sample","possible_end_sample")],
                         [14240, 14400, 22240, 22400])

    def test_mismatched_streams_and_clock_fail_closed(self):
        target = dict(label="cue", kind="wake", start_sample=100, end_sample=3000)
        with self.assertRaisesRegex(ValueError, "same whole-sample"):
            self.scorer.score_capture(bytes(18000), bytes(17998), [target])
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.scorer.score_capture(bytes(18000), bytes(18000), [target, dict(target, label="overlap")])
        self.model.write_bytes(b"unexpected model")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.scorer.score_capture(bytes(18000), bytes(18000), [target])


if __name__ == "__main__":
    unittest.main()
