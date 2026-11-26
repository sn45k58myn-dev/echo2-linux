# Echo 2 factory audio front-end bench

This offline engineering bench feeds generated multichannel signals through the factory NS6571 speech front end. The output is signed little-endian mono PCM at 16 kHz. A separate detector can read that stream. All stock executables, libraries, configurations and firmware images must come from the owner's local backup and stay outside this directory.

## What the stock HAL does

The stock primary audio HAL imports `asp_init`, `asp_create_pipeline`, `asp_process`, `asp_destroy_pipeline` and `asp_set_device`. Its capture data client supplies input and output format, channel count and sample rate to create a pipeline. It supplies input and output frame counts by address when processing each block. The speaker path writes to its own pipeline. The ASR reader consumes its speaker reference inside the library.

On the examined stock build, `AudioALSACaptureDataClient` creates its pipeline at relative address `0x596ba` and feeds capture at `0x59c3c`. The playback handler creates the speaker pipeline at `0x5699c` and feeds it at `0x5732a`. These offsets describe the examined HAL, not a runtime dependency for the bench.

| Pipeline | Identifier | Input for 8 ms | Output for 8 ms |
|---|---|---|---|
| Speaker | 3 | 384 stereo PCM16 frames at 48 kHz | 384 stereo PCM16 frames |
| ASR | 0 | 128 nine-channel packed PCM24 frames at 16 kHz | 128 mono PCM16 frames |

The fixture interface accepts nine signed 24-bit samples sign-extended into little-endian 32-bit words per mic frame. The driver packs those into three bytes per lane for the stock function. Its second input contains stereo signed little-endian PCM16 at 48 kHz. Both files must span the same number of complete 8 ms blocks. Preserve the fixture's channel order and separate speaker reference. A common-mode synthetic voice does not reproduce physical mic calibration, loudspeaker distortion or reflections in a room.

In the stock reference matcher the digital loopback starts at byte 21 of the 27-byte capture frame. It compares bytes 22–23 and 25–26, the upper 16 bits of zero-based lanes 7 and 8, against corresponding stereo playback samples. The constructor computes this offset at `0x1ad08`–`0x1ad1e` in `libasp.so`. The PCM24 comparison starts at `0x1b9c0`.

For a simulated digital return, pass `--loopback speaker-output` to `make-guest.py`. The driver fills only lanes 7 and 8 with every third output frame of the stock 48 kHz speaker stage after an 8 ms test delay. On the speech-plus-music, quiet-speech-plus-music and music-only fixtures, the original matcher reported synchronization on slice 0 with no samples removed. Each trial produced 111,360 speech-channel samples, and the logs reported no later loss of lock. The comparison using `--loopback speaker-input` could not match the signal. In these three trials the mono outputs with and without digital lock differed by at most one PCM count. The trials establish the digital connection. They showed no measurable reduction in the returned music attributable to acquiring lock.

The primary HAL examined has SHA-256 `1224f9138d6828643ad11ddb20cb823cae13372c558ea3670a19fa319ddefbaf`. The matching `libasp.so` has SHA-256 `bbf69e375a1303f0c71f0c6b6f7c44a2dfac6dd69ca3c4bf094a85964f9f56ab`.

## Supplying a reference from LibreEcho's speaker output

LibreEcho 0.16.8 already processes each sample before writing the same mono signal to both DAC channels on Radar. Its 48 kHz render tap rounds this result to PCM16. In a private copy of the factory `asp.cfg`, the speaker pipeline can accept that already processed stream with `Pipelines.Speaker.Passthrough` enabled. The ASR pipeline keeps its factory settings. Generate the modified copy with `python rendered_reference.py config /path/to/original/asp.cfg /path/to/private/new-asp.cfg`. The helper checks the original NS6571 configuration hash and requires a new output path.

In an isolated ARM guest, the factory speaker reference writer stayed active with this setting. The synthetic delayed speaker-input control that previously failed acquired stock reference lock and processed 870 complete blocks. The helper’s `synthetic` command recreates those two simulated return channels in the bench format. This fixture tests the factory processor separately from the board wiring.

During short tests on one physical Radar running LibreEcho, the final two three-byte capture slots carried **one mono 24-bit playback return**. In each 27-byte microphone frame, bytes 22 and 23 held the little-endian high word, and byte 26 held the fractional low byte. Bytes 21, 24 and 25 were zero. Rounding the reconstructed signed value to PCM16 reproduced the independent 48 kHz render tap in 99.83% of eligible samples and 100% when excluding exact half-step cases in a follow-up music test. Keeping only the high word reproduced roughly half. Other models and playback formats require their own measurement.

An adapter can preserve microphone slots 0–6 and present two copies of that measured physical mono return to the stock reader. Search all three 48 kHz phases against timestamped, continuous playback and use independent windows to confirm the offset. The capture-to-render index offset changed between sessions. Report physical alignment, the factory reader’s own lock and wake recognition as separate results.

## Building an isolated guest

1. Work in a private scratch directory outside the source tree. Supply the stock `system/bin/linker`, `system/bin/servicemanager`, `system/bin/dbus-daemon`, `system/etc/dbus.conf`, the full `system/vendor/etc/audio-algorithms` directory, and the stock `system/lib` and `system/vendor/lib` directories. The guest builder selects ELF dependencies transitively.
2. Build an ARMv7 QEMU `virt` guest kernel using Linux 6.1.178 and the supplied `guest-kernel.config`. Set `ARCH=arm CROSS_COMPILE=<arm-toolchain-prefix>`, run `make O=<private-kernel-build> allnoconfig KCONFIG_ALLCONFIG=<absolute-path-to-guest-kernel.config>`, then `make O=<private-kernel-build> KCFLAGS=-DBINDER_IPC_32BIT -j8 zImage`. This enables the Binder protocol and ARM helper page that this Fire OS userspace expects.
3. With an ARMv7 compiler, build the three guest helpers. Each uses a static Linux C runtime. For example, run `cc -static -Os -o <scratch>/log-sink log-sink.c` for `log-sink`, then repeat for `binder-node` and `start-bus`.
4. Build `driver.c` for the stock Bionic ARM softfp ABI. The following command is suitable for an ARMv7 GCC installation. It uses the original dynamic loader and libraries.

```sh
gcc -marm -mfloat-abi=softfp -Os -fPIE -ffreestanding -nostdlib \
  -fno-stack-protector -fno-builtin -pie \
  -Wl,-e,_start,--dynamic-linker,/system/bin/linker,--allow-shlib-undefined,--hash-style=both \
  -o "$scratch/driver" driver.c -L"$stock_system/lib" -lasp -lc
```

5. Supply the independent Echo 2 Linux project and its Alpine ARMv7 base initramfs. The staging tool reuses that project's archive writer. Supply both fixture paths, or omit both for the ten-block silence check.

```sh
python make-guest.py --project "$project" --base "$base_initramfs" \
  --system "$stock_system" --driver "$scratch/driver" \
  --log-sink "$scratch/log-sink" --binder-node "$scratch/binder-node" \
  --start-bus "$scratch/start-bus" \
  --mics "$scratch/mics.i32" --reference "$scratch/reference.s16" \
  --output "$scratch/guest.cpio.gz"
python run-guest.py --guest "$scratch/guest.cpio.gz" --kernel "$kernel" \
  --image echo2-audio-virtual-bench:dev --output "$scratch/asr.s16"
```

The launcher runs QEMU in a read-only container with all capabilities dropped, no networking, no shared writable directory and no guest network or sound hardware. Synthetic input is baked into the ephemeral guest. The host accepts PCM only after all stock calls, the frame count and the guest exit status have been checked. `--output` requires a new destination file. Diagnostic logs go to stderr.

The guest has no individual speaker's calibration EEPROM. The factory code reads its configured microphone calibration defaults. A low-bit metadata encoder can produce values of 0 or 1 on digital silence. The stock code can log unavailable telemetry and missing persistent preferences during startup. Record the actual voice and negative-control scores for each fixture before drawing conclusions about wake-word accuracy.

## Repeating the wake-word comparison

From this directory, generate the array inputs using two local, synthesized mono 16 kHz PCM16 WAVs. Supply the wake phrase and an unrelated command. Use a private scratch directory for the guest images and fixtures.

```sh
python make-fixtures.py "$wake_phrase_wav" "$other_command_wav" "$scratch/fixtures"
```

The generator models six outer microphones and a centre microphone, with two digital return lanes initially silent. All mixed cases use the same stereo passage and acoustic paths. The `quiet12` and `quiet24` inputs lower only the voice by 12 and 24 dB. These are changes in voice level relative to the first mix.

For each of `clean`, `mixed`, `music`, `command`, `quiet12` and `quiet24`, use its `.mic.i32` and `.ref.s16` files as the guest inputs. Save its mono result as `$scratch/fixtures/<case>.out.s16`. For the synchronized trials repeat `mixed`, `music` and `quiet24` with `--loopback speaker-output`. Save each result as `<case>-loop.out.s16` and redirect the guest launcher's standard error to `$scratch/<case>-loop-case.log`. The optional `mixed-input-loop` control uses `--loopback speaker-input`. The optional `mixed-no-ref` control uses the mixed microphones and the silent reference from the clean case.

Install NumPy and ONNX Runtime in the host Python environment. Score the outputs with the same classifier and feature models across all cases. The report records the raw microphone baseline, processed scores and any reference-matching messages from the guest.

```sh
python score-paired.py "$scratch/fixtures" --logs "$scratch" \
  --features "$wake_feature_dir" --model "$wake_classifier"
```

The file `$scratch/fixtures/wake-scores.json` holds the measurements. Each factory case includes a score with its original bytes and one with metadata removed. The raw microphone baseline uses its original bytes.

## Factory audio metadata

On NS6571 the factory processor reports that audio metadata encoding is enabled. The supplied `libAFEMetadataDecoder.so` confirms the convention. In its ARM Thumb `AudioMetadataDecoder::processAudio` function (offsets `0x7720` through `0x7738`), the decoder extracts sample bit 0 into a metadata register and clears that bit in the audio copy. The project's `--factory-pcm` option applies that single operation to tagged mono samples before computing the wake-word features. It does not need to decode the metadata messages.

The first eight sample bits of each factory block formed the decoder's `0xA5` synchronization marker in all 870 blocks of the clean, command, synchronized music-only and synchronized quietest-speech trials.

Two fresh guest runs of the quietest synchronized fixture gave unprocessed wake scores of 0.359 and 0.416. They differed by at most one PCM count. Removing bit 0 produced byte-identical audio and a score of 0.528 on both runs, crossing the fixed 0.5 detection threshold. The same change preserved the music-only and unrelated-command controls. These are single-phrase bench results. See the [wake-word results and limits](../../AUDIO-SERVICES.md#wake-word-on-the-speech-output).

## Run the paired audio regression from a fresh checkout

Run these commands from the repository root on Linux with Git, Python 3.11 or newer, Docker and ARMv7 execution support. On x86 hosts the full suite uses QEMU binfmt for the ARM player. The tests generate the microphone, playback and detector signals locally.

```sh
python3 tools/build-player.py
python3 -m unittest discover -s tests
docker build -t echo2-paired-check tools/factory-asr-bench/paired
docker run --rm --network none echo2-paired-check
docker run --rm --network none -e ROUTER_TEST_ALIGNMENT_LOSS=1 echo2-paired-check
```

The Python suite requires Git on PATH. The optional real-model test takes the four fixture paths listed in `tests/test_asr_wake_bench.py`. The container exercises mute, intercom handover, lost leases and an unavailable receiver. Its router sends 120 indexed blocks through the Assist FIFO. The second run deliberately corrupts the reference and checks that the router drops and reacquires alignment. All of this works without an Echo, an Amazon backup or a personal wake model.

## Build the owner-local ARMv7 worker

The native worker takes seven microphone channels and a time-aligned playback reference. The rendered reference comes from the signal sent to the DAC. `--device-mic-calibration` redirects the stock profile to seven values read from the **current board** during the session. The cleanup removes those temporary values. They are never inputs to the archive builder.

From the repository root on a POSIX host, set `stock_system` to the absolute path of the owner's extracted NS6571 `system` directory. Use the matching stock profile and libraries. Use Docker with ARMv7 execution support, Python 3, and a private output directory on a filesystem supporting hard links.

```sh
set -eu
umask 077
bench="$PWD/tools/factory-asr-bench"
scratch=$(mktemp -d)
docker build --platform linux/arm/v7 -t echo2-arm-bench tools/player-image
docker run --rm --platform linux/arm/v7 --network none \
  -v "$bench:/src:ro" -v "$scratch:/private" -v "$stock_system:/private/system:ro" \
  -e SRC=/src -e PRIVATE=/private --entrypoint sh echo2-arm-bench /src/build-native.sh
python3 "$bench/rendered_reference.py" config \
  "$stock_system/vendor/etc/audio-algorithms/asp.cfg" "$scratch/rendered.cfg"
docker run --rm --network none \
  -v "$bench:/src:ro" -v "$scratch:/private" -v "$stock_system:/private/system:ro" \
  python:3.13-slim python /src/native-bundle.py /private /private/worker.tgz \
  --rendered-config /private/rendered.cfg --device-mic-calibration
```

The resulting private `worker.tgz` contains vendor code. Keep the scratch directory outside the checkout. On a previously authorized Radar development speaker with this archive unpacked in an exclusive root-owned mode-0700 ramfs directory matching `/run/echo2-native-asr-bench-<id>`, the public replay tool checks 128 timestamped 8 ms blocks using silence. It verifies every reply and the worker exit status, without opening a microphone.

```sh
python3 "$bench/native-replay.py" --serial "$ADB_SERIAL" --stage "$REMOTE_RAM_STAGE"
```

### Live Assist integration

The paired sources preserve the 0.16.8 capture stream for the original subscribers and feed processed mono samples to the identified Home Assistant voice subscriber. The comparison uses the same detector model, time base and room audio for both paths. The privacy latch, intercom lease and controller watchdog gate microphone use. In a disposable copy of the matching 0.16.8 UI sources, apply `paired/prepare-mirror.py <ui-tree>` followed by `paired/prepare-assist.py <ui-tree>`. These edits are tied to the tested source layout and fail on an unexpected anchor. `paired/router.cpp`, `route.h`, `paired_capture.c` and the shadow detector sources implement the measured audio route. The runtime supervisor in `paired/session.sh` pins the known test binaries and restores the installed voice services. It requires a session controller and a matching device build.

The portable entry points above cover the synthetic router and private worker. The live Assist controller used for hardware qualification is a local engineering harness. The Linux image does not yet install a permanent factory microphone service. The vendor worker drops privileges and runs in a chroot. Running it on a networked speaker still requires a separate egress audit before anyone can promise offline-only operation.
