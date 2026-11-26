"""Build synthetic seven-mic test inputs for the private Radar ASR bench."""
import argparse
import array
import math
import pathlib
import sys
import wave


RATE = 16000
ANGLES = (150, 210, 270, 330, 30, 90)


def speech(path):
    with wave.open(str(path), "rb") as src:
        if (src.getframerate(), src.getsampwidth(), src.getnchannels()) != (RATE, 2, 1):
            raise ValueError("Speech fixture must be mono 16 kHz PCM16")
        samples = array.array("h")
        samples.frombytes(src.readframes(src.getnframes()))
        if sys.byteorder != "little":
            samples.byteswap()
        return samples


def little_endian_bytes(values):
    if sys.byteorder != "little":
        values = values[:]
        values.byteswap()
    return values.tobytes()


def synthetic_bed(t, right):
    # An identical deterministic passage on every run. Different bass and
    # harmonics in the two channels make a downmixed reference distinguishable.
    beat = int(2.5 * t)
    phase = (2.5 * t) % 1
    base = (146.83, 174.61, 196.00, 130.81)[(beat // 2 + right) % 4]
    note = sum(math.sin(math.tau * base * n * t) / n for n in (1, 2, 3))
    bass = math.sin(math.tau * (57 + right * 5) * t) * math.exp(-6 * phase)
    return int(3200 * (0.65 * note + 0.6 * bass))


def at(samples, pos):
    if pos < 0 or pos >= len(samples) - 1:
        return 0.0
    index = int(pos)
    return samples[index] + (samples[index + 1] - samples[index]) * (pos - index)


def build(folder, name, samples, length, ref, play, voice_gain=1):
    first = 2 * RATE
    delays = [0.036 * RATE / 343 * math.cos(math.radians(a - 30)) for a in ANGLES] + [0.]
    mic = array.array("i")
    lane_zero = array.array("h")
    assert mic.itemsize == 4 and lane_zero.itemsize == 2
    for i in range(length):
        for lane, shift in enumerate(delays):
            near = voice_gain * at(samples, i - first + shift)
            echo = 0.
            if play:
                # Simulated acoustic returns from left and right, followed by
                # one reflection. Delay values are fixture assumptions.
                for lag, side, gain in ((28 + lane, 0, .73), (37 - lane, 1, .51), (130, 0, .18)):
                    if i >= lag:
                        echo += gain * ref[(i - lag) * 6 + side]
            sample = max(-32768, min(32767, round(near + echo)))
            mic.append(sample * 256)
            if lane == 0:
                lane_zero.append(sample)
        mic.extend((0, 0))
    (folder / (name + ".mic.i32")).write_bytes(little_endian_bytes(mic))
    (folder / (name + ".lane0.s16")).write_bytes(little_endian_bytes(lane_zero))
    reference = little_endian_bytes(ref) if play else bytes(length * 12)
    (folder / (name + ".ref.s16")).write_bytes(reference)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phrase", type=pathlib.Path)
    parser.add_argument("other_speech", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    phrase, other = speech(args.phrase), speech(args.other_speech)
    length = ((3 * RATE + max(len(phrase), len(other)) + 127) // 128) * 128
    args.output.mkdir(parents=True, exist_ok=True)
    ref = array.array("h", (synthetic_bed(i / (3 * RATE), ch)
                            for i in range(3 * length) for ch in (0, 1)))
    conditions = (("clean", phrase, False, 0), ("mixed", phrase, True, 0),
                  ("music", (), True, 0), ("command", other, True, 0),
                  ("quiet12", phrase, True, -12), ("quiet24", phrase, True, -24))
    for name, speech_samples, play, voice_db in conditions:
        build(args.output, name, speech_samples, length, ref, play, 10 ** (voice_db / 20))
    print(f"Six fixtures ready, {length} mic frames each ({length / RATE:.3f} s)")


if __name__ == "__main__":
    main()
