"""Score paired synthetic front-end fixtures and write measurements only."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np


TOOLS = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("asr_wake", TOOLS / "asr-wake-bench.py")
wake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wake)


def measurements(path, model, features, expected_bytes, *, factory_pcm=False):
    if path.stat().st_size != expected_bytes:
        raise ValueError(f"Expected {expected_bytes} bytes of synthetic mono PCM in {path.name}")
    classifier = wake.Classifier(features, model, factory_pcm=factory_pcm)
    digest = hashlib.sha256()
    events = []
    energy = 0.
    absolute_peak = 0.

    def chunks():
        nonlocal energy, absolute_peak
        with path.open("rb") as source:
            while data := source.read(256):
                digest.update(data)
                x = np.frombuffer(data, "<i2").astype(np.float64) / 32768.
                energy += np.dot(x, x)
                absolute_peak = max(absolute_peak, float(np.max(np.abs(x))))
                yield data
    wake.analyse(chunks(), classifier.score, 0.5, events.append)
    n = events[-1]["samples"]
    return {"input_sha256": digest.hexdigest(), "samples": n,
            "rms_dbfs": (10 * np.log10(energy / n) if energy > 0 else None),
            "peak_dbfs": (20 * np.log10(absolute_peak) if absolute_peak > 0 else None),
            "wakes_ms": [e["end_ms"] for e in events[:-1]],
            "peak_wake_score": events[-1]["peak_score"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--logs", type=Path, required=True, help="directory containing the guest case logs")
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    report = {"scope": "Offline synthetic paired fixture. Times are end-of-window.",
              "sample_rate": wake.RATE, "threshold": 0.5,
              "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(), "cases": {}}
    for case in ("clean", "mixed", "music", "command", "quiet12", "quiet24"):
        lane = args.fixtures / (case + ".lane0.s16")
        if case.startswith("quiet") and not lane.exists():
            continue
        size = lane.stat().st_size
        mic_bytes = (args.fixtures / (case + ".mic.i32")).stat().st_size
        if not size or size * 18 != mic_bytes:
            raise ValueError(f"Incomplete raw lane in {case}")
        row = {"lane0": measurements(lane, args.model, args.features, size)}
        processed = args.fixtures / (case + ".out.s16")
        if processed.is_file():
            row["factory_asr"] = measurements(processed, args.model, args.features, size)
            row["factory_asr_metadata_cleared"] = measurements(
                processed, args.model, args.features, size, factory_pcm=True)
        for suffix in ("loop", "input-loop"):
            playback = args.fixtures / f"{case}-{suffix}.out.s16"
            if playback.is_file():
                row[f"factory_asr_{suffix.replace('-', '_')}"] = measurements(
                    playback, args.model, args.features, size)
                if suffix == "loop":
                    row["factory_asr_loop_metadata_cleared"] = measurements(
                        playback, args.model, args.features, size, factory_pcm=True)
                # A connected writer alone does not establish a match. Check
                # the reader's own log, followed by a complete guest exit.
                log = (args.logs / f"{case}-{suffix}-case.log").read_text(
                    encoding="utf-8", errors="replace")
                if f"AFE: completed {size // 256} blocks" not in log or "BENCH_EXIT=0" not in log:
                    raise ValueError(f"Incomplete factory run: {case}-{suffix}")
                status = [s for s in log.splitlines() if "stock[ASP/ER]" in s and
                          any(text in s for text in ("Synchronized.", "No match found", "Lost sync"))]
                report.setdefault("loopback_status", {})[f"{case}-{suffix}"] = {
                    "reader_events": list(dict.fromkeys(status)),
                    "vendor_match_seen": any("Synchronized." in msg for msg in status),
                }
        if case == "mixed":
            control = args.fixtures / "mixed-no-ref.out.s16"
            if control.is_file():
                row["factory_asr_silent_reference"] = measurements(control, args.model, args.features, size)
                if "factory_asr" in row:
                    a = np.fromfile(processed, dtype="<i2").astype(np.float64)
                    b = np.fromfile(control, dtype="<i2").astype(np.float64)
                    delta = (a - b) / 32768
                    changed = int(np.count_nonzero(delta))
                    report["mixed_reference_comparison"] = {
                        "changed_samples": changed,
                        "difference_rms_dbfs": float(10*np.log10(np.mean(delta**2))) if changed else None,
                    }
        report["cases"][case] = row
    target = args.fixtures / "wake-scores.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    temporary.replace(target)
    for name, row in report["cases"].items():
        print(name, " ".join(f"{mode}: {m['peak_wake_score']:.6f} @ {m['wakes_ms']} rms={m['rms_dbfs']}"
                            for mode, m in row.items()))


if __name__ == "__main__":
    main()
