"""Paired RAM-only wake detector scoring for a sample-aligned capture.

The caller supplies both mono PCM streams and owns capture and playback.
Returns scalar measurements. No hardware, network or output files are used.
"""

import hashlib
import importlib.util
import math
from pathlib import Path
import sys
from array import array


SPEC = importlib.util.spec_from_file_location("asr_wake", Path(__file__).resolve().parents[1] / "asr-wake-bench.py")
wake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wake)
NANOSECONDS = 1_000_000_000
THRESHOLD = 0.5


def laptop_window(label, kind, capture_earliest_ns, capture_latest_ns, play_ns,
                  open_ms, close_ms, start_sample=0):
    """Map playback-relative milliseconds to a conservative sample interval.

    The center region contains detector endpoints inside the window for
    every possible time of the first captured sample in the supplied bracket.
    """
    if capture_earliest_ns > capture_latest_ns or open_ms >= close_ms:
        raise ValueError("invalid capture or playback interval")
    for value in (capture_earliest_ns, capture_latest_ns, play_ns):
        if not isinstance(value, int) or value < 0:
            raise ValueError("timestamps must be nonnegative monotonic nanoseconds")
    lo_ns = play_ns + round(open_ms * 1_000_000)
    hi_ns = play_ns + round(close_ms * 1_000_000)

    def sample(timestamp, anchor):
        return start_sample + math.ceil((timestamp - anchor) * wake.RATE / NANOSECONDS)

    return {"label": label, "kind": kind,
            "start_sample": sample(lo_ns, capture_earliest_ns),
            "end_sample": sample(hi_ns, capture_latest_ns),
            "possible_start_sample": sample(lo_ns, capture_latest_ns),
            "possible_end_sample": sample(hi_ns, capture_earliest_ns)}


def score_branch(detector, pcm, start_sample, windows):
    peaks = {item["label"]: None for item in windows}
    detections = []
    scored = start_sample
    energy = clipped = 0
    max_abs = 0

    def score(block):
        nonlocal scored, energy, clipped, max_abs
        probability = detector.score(block)
        levels = array("h")
        levels.frombytes(block)
        if sys.byteorder != "little":
            levels.byteswap()
        energy += sum(sample * sample for sample in levels)
        clipped += levels.count(-32768) + levels.count(32767)
        max_abs = max(max_abs, max(levels), -min(levels))
        scored += wake.BLOCK
        if scored > start_sample + wake.STARTUP_BLOCKS * wake.BLOCK:
            for item in windows:
                if item["start_sample"] <= scored < item["end_sample"]:
                    old = peaks[item["label"]]
                    peaks[item["label"]] = probability if old is None else max(old, probability)
        return probability

    view = memoryview(pcm).cast("B")
    wake.analyse((view[i:i + 2560] for i in range(0, len(view), 2560)),
                 score, THRESHOLD, detections.append)
    summary = detections.pop()
    observed = [start_sample + event["end_sample"] for event in detections]
    outside = []
    for event in observed:
        if not any(w["possible_start_sample"] <= event < w["possible_end_sample"] for w in windows):
            outside.append(event)
    trials = []
    for item in windows:
        start, end = item["start_sample"], item["end_sample"]
        inside = [s for s in observed if start <= s < end]
        edges = [s for s in observed if item["possible_start_sample"] <= s < item["possible_end_sample"]
                 and not start <= s < end]
        peak = peaks[item["label"]]
        if peak is None:
            raise ValueError(f"no scored endpoint in window {item['label']}")
        if item["kind"] == "wake":
            verdict = "hit" if inside else ("timing_uncertain" if edges else "miss")
        else:
            verdict = "false_wake" if inside else ("timing_uncertain" if edges else "clear")
        trials.append({"label": item["label"], "kind": item["kind"], "verdict": verdict,
                       "peak_score": peak, "wake_samples": inside, "edge_wake_samples": edges})
    return {"trials": trials, "outside_wake_samples": outside, "wake_count": len(observed),
            "peak_score": summary["peak_score"], "scored_windows": summary["scored_windows"],
            "tail_samples": summary["tail_samples"],
            "input_rms_dbfs": 10 * math.log10(energy / (scored-start_sample) / 32768**2) if energy else None,
            "input_peak_dbfs": 20 * math.log10(max_abs / 32768) if max_abs else None,
            "input_clipped_samples": clipped}


class PairScorer:
    """Start each independent capture with fresh, identical feature histories."""

    def __init__(self, feature_dir, model, *, expected_model_sha256):
        self.features = Path(feature_dir)
        self.model = Path(model)
        self.expected = expected_model_sha256.lower()
        self.verify()

    def verify(self):
        actual = hashlib.sha256(self.model.read_bytes()).hexdigest()
        if actual != self.expected:
            raise ValueError("wake classifier hash differs from expected model")

    def detector(self, pcm, factory_pcm):
        return wake.Classifier(self.features, self.model, factory_pcm=factory_pcm)

    def score_capture(self, baseline_pcm, factory_pcm, windows, *, start_sample=0):
        baseline = memoryview(baseline_pcm).cast("B")
        factory = memoryview(factory_pcm).cast("B")
        size = len(baseline)
        if len(factory) != size or size % 2 or not size:
            raise ValueError("both mono PCM16 streams must have the same whole-sample length")
        samples = size // 2
        if not isinstance(start_sample, int) or start_sample < 0:
            raise ValueError("invalid first sample index")
        normalized = []
        labels = set()
        for item in windows:
            label, kind = item["label"], item["kind"]
            if not label or label in labels or kind not in {"wake", "control"}:
                raise ValueError("windows require distinct labels and wake/control kinds")
            labels.add(label)
            start, end = item["start_sample"], item["end_sample"]
            left = item.get("possible_start_sample", start)
            right = item.get("possible_end_sample", end)
            if not start_sample <= left <= start < end <= right <= start_sample + samples + 1:
                raise ValueError(f"window {label} exceeds capture or has no certain time interval")
            normalized.append(dict(label=label, kind=kind, start_sample=start, end_sample=end,
                                   possible_start_sample=left, possible_end_sample=right))
        if not normalized:
            raise ValueError("supply a target or control window")
        normalized.sort(key=lambda w: w["possible_start_sample"])
        for first, second in zip(normalized, normalized[1:]):
            if first["possible_end_sample"] > second["possible_start_sample"]:
                raise ValueError("overlapping trial windows")
        self.verify()
        result = {"model_sha256": self.expected, "rate": wake.RATE, "threshold": THRESHOLD,
                  "start_sample": start_sample, "samples": samples, "windows": normalized}
        for name, pcm, strip_bit in (("baseline", baseline, False), ("factory", factory, True)):
            detector = self.detector(pcm, strip_bit)
            result[name] = score_branch(detector, pcm, start_sample, normalized)
        self.verify()
        return result
