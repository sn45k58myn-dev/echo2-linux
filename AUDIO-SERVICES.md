# Sendspin and iPhone intercom on Echo 2 Linux

The optional audio package runs in the project's Alpine ARMv7 userspace. A generic ARM virtual machine can play a real Sendspin stream into a virtual audio output and accept an authenticated intercom call from an iPhone. Running this test requires no connection to an Echo, Home Assistant or Music Assistant.

## Build

1. Build the base with `make base`. Follow the [Sendspin build instructions](services/sendspin/README.md) to export the pinned source checkouts and build into `dist/sendspin`. The builder also compiles the physical Radar playback engine.
2. Run `python3 tools/build-intercom.py` to generate `dist/intercom`. Docker supplies the pinned Go compiler. The executable includes the mobile page and AudioWorklet. [Intercom endpoints and tests](services/intercom/README.md)
3. Run `python3 tools/build-intercom-bridge.py` with the same source export. This builds the room audio companion and the adapter between its local socket and the intercom's audio pipes. The virtual room uses the probe backend. To test the mixer and capture connections, follow the [room bridge guide](services/intercom-bridge/README.md).
4. Run `make audio-services`. It verifies the binary inventories, hashes and static ARM ELF format, then adds the executables and licence notices to `build/echo2-audio-services.cpio.gz`. To build a read-only feature filesystem on Linux, install squashfs-tools and run `python3 tools/build-audio-feature.py --squashfs`. Both outputs are separate from the board boot image.

## Start the test room

Use the Alpine ARMv7 netboot archive and extracted kernel listed in the [base build guide](README.md). Choose the host's Wi-Fi or Ethernet address for the phone. Set `TEST_KEYS` to a private directory outside source control. Place the trusted TLS certificate chain in `server.crt` and its matching leaf key in `server.key`. The certificate must cover the exact IP address or DNS name used in Safari. Set a temporary room passphrase of 12–256 bytes in `password`. For a fresh test room, this creates a random passphrase file readable only by its owner.

```sh
(umask 077 && set -C && openssl rand -hex 24 > "${TEST_KEYS:?choose a private test directory}/password")
```

```sh
python3 tools/run-audio-bench.py \
  --kernel "$VM_KERNEL" --netboot "$NETBOOT" --private "$TEST_KEYS" \
  --bind "$BENCH_LAN_ADDRESS" --https-port 9443 --seconds 3600
```

On home Wi-Fi, open `https://<bench-address>:9443/intercom` in Safari. Sign in, tap **Call room**, allow the microphone and listen for the low tone. Say a word and pause. Two higher tones mean the guest received the phone's audio and sent its reply back through the call. Tap **End call**. The virtual room has no access to a household microphone. It keeps packet and level measurements in RAM.

To exercise music on the same running guest, use the Python test environment and `SERVER_ARCHIVE` from the [Sendspin setup](services/sendspin/README.md#test-a-stream). Run the following from the Docker host. The virtual output reports decoded sample counts and peaks in the container log.

```sh
python3 tests/sendspin-stream.py --archive "$SERVER_ARCHIVE" --url ws://127.0.0.1:18928/sendspin
docker logs echo2-linux-audio-bench
```

The guest expires after the selected test window. To end a session early, run `docker stop echo2-linux-audio-bench`. An automated [two-way intercom test](tests/intercom-roundtrip.py) is available when the test room is idle.

For a group test, use `python3 tests/sendspin-group.py --archive "$SERVER_ARCHIVE"` in the isolated ARM-capable Linux environment described in the [Sendspin test guide](services/sendspin/README.md). It pairs two receivers, identifies five markers in their decoded output and compares the virtual audio write times.

## Hardware qualification

### Wake word on the speech output

The [factory audio bench](tools/factory-asr-bench/README.md) connects the original Echo 2 speech processor to the independent detector on a development computer. Supply the stock files from a private backup.

The host-side wake bench accepts the mono speech signal at the output of a microphone front end. The full-size Echo 2 factory ASR configuration examined produces signed 16-bit PCM at 16 kHz. Supply it to the bench as headerless little-endian samples. A 128-sample frame is 256 bytes. The bench combines ten frames per 80 ms inference window and accepts fragmented reads. A WAV fixture with the same audio format can also be used. The command reads audio from the supplied stream or file and emits JSON lines containing timing and scores.

Install NumPy and ONNX Runtime in a Python environment on the test host. Supply the openWakeWord `melspectrogram.onnx` and `embedding_model.onnx` from the matching feature model set, plus a binary classifier with a 16-by-96 feature input. These model files and test recordings are supplied locally. Run from the project root.

```sh
python -m pip install numpy onnxruntime
python tools/asr-wake-bench.py --features "$WAKE_FEATURE_DIR" --model "$WAKE_MODEL" < speech.s16le
python tools/asr-wake-bench.py --features "$WAKE_FEATURE_DIR" --model "$WAKE_MODEL" --wav phrase.wav
python tools/asr-wake-bench.py --features "$WAKE_FEATURE_DIR" --model "$WAKE_MODEL" --factory-pcm < factory-asr.s16le
```

For NS6571 factory ASR output, pass `--factory-pcm` with either stdin or `--wav`. The factory reports an enabled metadata encoder. Two independent guest runs using identical microphone input differed only in bit 0 of their mono output samples. This mode clears that bit before extracting speech features. Use the default mode for standard WAV recordings and raw microphone lanes.

The first 400 ms establish the live feature history. A `wake` line marks the end of the first qualifying 80 ms window. The default decision threshold is 0.5 and distinct events have a 1.75 s minimum spacing. The end-of-stream summary includes the peak score, count of wakes and any unscored final partial block. Supply some real trailing audio in a phrase fixture so the recognizer can see the end of the word. The bench makes no network calls and saves no PCM. It does not enable a room microphone or start a voice assistant.

To repeat the model checks, set `WAKE_FEATURE_DIR` and `WAKE_MODEL`, point `WAKE_PHRASE_WAV` at a mono 16 kHz fixture containing one expected wake and `WAKE_CONTROL_WAV` at unrelated speech, then run `python tests/test_asr_wake_bench.py -v`. That test compares normal, raw and irregularly fragmented input and checks silence.

In a local run using the “Okay Groot” classifier, the clean transmitted phrase “Okay Groot, stop music” scored 0.97019 and triggered once at 2.08 s. “Stop music” alone scored 0.00084 and silence scored 0.00070. All three peak scores matched the independent native reference within 0.000001.

A separate ARM guest ran the factory NS6571 speech front end on synthetic multichannel microphone input. The same classifier and 0.5 threshold were used on a raw microphone lane and on the processed speech output. Each detected the word once in clean speech and once during simulated music. Neither detected a wake on music alone or on the unrelated command with music. The factory-output detection arrived one 80 ms inference window later in clean speech and two windows later during music.

Lowering only the speech in that same mix by 12 dB still triggered both paths. With speech lowered by 24 dB, the raw lane triggered at 0.920. The reference reader initially had no digital loopback. In a follow-up run, channels 7 and 8 carried stereo samples from the factory speaker output, downsampled to 16 kHz and delayed by 8 ms. The factory reader reported synchronization on the mixed, quietest and music-only runs.

Passing the untouched factory output straight to the classifier missed the quietest synchronized trial at 0.359. With `--factory-pcm`, two independent runs on the same fixture gave identical speech samples and detected the wake at a peak of 0.528, just above the fixed threshold. The raw microphone lane gave the stronger score in this case.

A second synthesized voice (Microsoft Zira) was then tested with the same music, microphone geometry, classifier and threshold. In quiet its raw score was 0.107 and its factory score was 0.480 before removing metadata. After removing metadata the factory score was 0.945, which triggered a wake. At the initial music level both the raw and corrected factory paths triggered (0.633 and 0.966). With the second voice lowered by 24 dB, both paths missed. These measurements cover two synthesized voices in one simulated scene. See the [calibrated microphone trial](VALIDATION.md#calibrated-microphone-trial) for the first physical comparison during playback.

### Board audio

The synthetic room and virtual audio output make transport failures distinguishable from microphone, amplifier and acoustic problems. The physical speaker still needs its board audio routing, capture and mute inputs validated under the project's kernel. An acoustic call and a measured two-player test are needed to qualify echo cancellation and multi-room sync. [Validation record](VALIDATION.md)
