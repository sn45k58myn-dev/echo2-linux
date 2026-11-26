#!/usr/bin/env python3
"""Score a mono 16 kHz speech stream with an openWakeWord ONNX classifier."""

import argparse
import json
import math
import sys
import wave
from pathlib import Path


RATE = 16000
BLOCK = 1280
STARTUP_BLOCKS = 5
CLEAR_BIT_ZERO = bytes(value & 0xFE for value in range(256))


def factory_speech_pcm(pcm):
    """Clear the low bit of each complete little-endian factory speech sample."""
    if len(pcm) % 2:
        raise ValueError("factory PCM ended within a 16-bit sample")
    block = bytearray(pcm)
    block[::2] = block[::2].translate(CLEAR_BIT_ZERO)
    return block


class Classifier:
    """The 80 ms openWakeWord mel, embedding and classifier stages."""

    def __init__(self, feature_dir, model, *, factory_pcm=False):
        import numpy as np
        import onnxruntime as ort

        self.np = np
        self.factory_pcm = factory_pcm
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1

        def graph(path):
            if not path.is_file():
                raise ValueError(f"missing model file: {path}")
            try:
                session = ort.InferenceSession(str(path), sess_options=options,
                                               providers=["CPUExecutionProvider"])
            except Exception as error:
                raise ValueError(f"cannot open {path.name}: {error}") from error
            if len(session.get_inputs()) != 1 or len(session.get_outputs()) != 1:
                raise ValueError(f"expected a single input and output in {path.name}")
            key = session.get_inputs()[0].name

            def evaluate(values):
                try:
                    return session.run(None, {key: values})[0]
                except Exception as error:
                    raise ValueError(f"inference failed in {path.name}: {error}") from error

            return evaluate

        self.mel = graph(feature_dir / "melspectrogram.onnx")
        self.embed = graph(feature_dir / "embedding_model.onnx")
        self.classify = graph(model)
        self.mels = np.ones((76, 32), np.float32)
        self.features = np.zeros((16, 96), np.float32)
        self.overlap = np.empty(0, np.float32)
        # Match the speaker's neutral startup history, including the first
        # block's shorter FFT input after warmup.
        for _ in range(len(self.features)):
            self.score(bytes(2 * BLOCK))
        self.mels.fill(1)
        self.overlap = np.empty(0, np.float32)

    def score(self, pcm):
        np = self.np
        if self.factory_pcm:
            pcm = factory_speech_pcm(pcm)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        window = np.concatenate((self.overlap, samples))[None, :]
        self.overlap = samples[-480:].copy()
        rows = self.mel(window).reshape(-1, 32) / 10 + 2
        self.mels = np.concatenate((self.mels, rows))[-76:].copy()
        embedding = self.embed(self.mels[None, :, :, None]).reshape(96)
        self.features[:-1] = self.features[1:]
        self.features[-1] = embedding
        value = float(self.classify(self.features[None, :, :]).item())
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("classifier returned an invalid probability")
        return value


def analyse(chunks, score, threshold, emit):
    """Emit a wake on crossing the threshold and a summary at EOF."""
    buffer = bytearray()
    blocks = events = 0
    previous = peak = 0.0
    last_wake = -RATE * 2
    for chunk in chunks:
        buffer.extend(chunk)
        offset = 0
        while len(buffer) - offset >= 2 * BLOCK:
            value = score(buffer[offset:offset + 2 * BLOCK])
            offset += 2 * BLOCK
            blocks += 1
            if blocks <= STARTUP_BLOCKS:
                continue
            peak = max(peak, value)
            end_sample = blocks * BLOCK
            if previous < threshold <= value and end_sample - last_wake >= RATE * 1.75:
                emit({"event": "wake", "end_sample": end_sample,
                      "end_ms": end_sample * 1000 // RATE, "score": value})
                last_wake = end_sample
                events += 1
            previous = value
        del buffer[:offset]
    if len(buffer) % 2:
        raise ValueError("stream ends within a 16-bit sample")
    if blocks <= STARTUP_BLOCKS:
        raise ValueError("insufficient audio for a scored window (at least 480 ms required)")
    emit({"event": "summary", "samples": blocks * BLOCK + len(buffer) // 2,
          "scored_windows": blocks - STARTUP_BLOCKS, "tail_samples": len(buffer) // 2,
          "peak_score": peak, "threshold": threshold, "wakes": events})


def wav_chunks(source):
    if (source.getframerate(), source.getnchannels(), source.getsampwidth(),
        source.getcomptype()) != (RATE, 1, 2, "NONE"):
        raise ValueError("expected a mono 16 kHz, 16-bit PCM WAV")
    count = 0
    while chunk := source.readframes(128):
        count += len(chunk)
        yield chunk
    if count != source.getnframes() * 2:
        raise ValueError("truncated WAV data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path, help="openWakeWord feature model directory")
    parser.add_argument("--model", required=True, type=Path, help="16-by-96 ONNX word classifier")
    parser.add_argument("--wav", type=Path, help="read a PCM WAV fixture instead of raw S16LE on stdin")
    parser.add_argument("--factory-pcm", action="store_true",
                        help="clear sample bit 0 before scoring NS6571 factory ASR output")
    parser.add_argument("--threshold", type=float, default=0.5, help="bench decision threshold (default 0.5)")
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        parser.error("threshold must be greater than zero and at most one")
    try:
        model = Classifier(args.features, args.model, factory_pcm=args.factory_pcm)

        def run(chunks):
            analyse(chunks, model.score, args.threshold,
                    lambda result: print(json.dumps(result, allow_nan=False), flush=True))

        if args.wav:
            with wave.open(str(args.wav), "rb") as source:
                run(wav_chunks(source))
        else:
            run(iter(lambda: sys.stdin.buffer.read(256), b""))
    except (ImportError, OSError, EOFError, ValueError, RuntimeError, wave.Error) as error:
        parser.exit(2, f"asr-wake-bench: {error}\n")


if __name__ == "__main__":
    main()
