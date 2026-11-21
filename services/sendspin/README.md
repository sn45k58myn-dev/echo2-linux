# Sendspin player for Echo 2 Linux

`echo2-sendspind` uses the encrypted Sendspin C++ SDK, FLAC decoder, clock synchronisation and playback adapter from LibreEcho 0.16.8. It submits stereo 48 kHz PCM to a local playback socket. The package includes two backends.

| Executable | Purpose |
| --- | --- |
| `echo2-sendspin-sink` | A clocked virtual DAC for the Linux guest. It reports frame, sample and peak counters. Optional PCM output requires an explicit, new destination file. |
| `echo2-board-audio` | The Radar speaker engine built against pinned TinyALSA. Its default physical output is card 0, device 23. Qualifying its mixer, clock and output level requires a recoverable hardware test. |

## Build

Run the build on Linux with Python 3.12 or later, Git, CMake, Make, a host C/C++20 compiler, and an ARMv7 hard-float GCC/G++ cross toolchain with static glibc and libstdc++. The archive extractor also works with Python 3.11 maintenance releases that support the `data` filter. Export the pinned source checkouts first. Supply the licence notices from the cross toolchain you are actually using.

```sh
python3 tools/export-audio-sources.py --product "$PRODUCT" --ui "$UI" --platform "$PLATFORM" --output build/compat-complete
python3 tools/export-audio-sources.py --product "$PRODUCT" --ui "$UI" --platform "$PLATFORM" --output build/compat-complete --verify
python3 tools/build-sendspin.py --runtime-notices "$TOOLCHAIN_NOTICES" --output dist/sendspin
(cd dist/sendspin && sha256sum -c SHA256SUMS)
```

`TOOLCHAIN_NOTICES` must contain `glibc-copyright` and `gcc-runtime-copyright`. On a Debian cross build, copy the copyright notices shipped by the installed target libc and GCC runtime packages into those files. `--cross` accepts an absolute compiler prefix. `--archives DIR` and `--tinyalsa-archive FILE` enable a fully offline build from the pinned tarballs. Use a new output directory for each build. The builder checks source archive and patch digests and verifies all three ELF files have no runtime interpreter or shared-library dependencies.

## Test a stream

Use a Linux host with ARM binfmt support or native ARMv7, a local test network with one non-loopback network interface, and the Python dependencies declared by the pinned `aiosendspin` server, including its audio codec extras. In a private network namespace the following command exercises pairing, real WebSocket transport, FLAC decoding and timed rendering using the ARM executables.

```sh
python3 tests/sendspin-bench.py --bin dist/sendspin/bin --archive "$SERVER_ARCHIVE"
```

The test requests an ephemeral pairing token from the player's local owner socket. It sends six seconds of quiet synthetic audio, checks for the 720 Hz pilot in the rendered samples and removes temporary audio and pairing material. Rendered frame counts include timing padding. `SERVER_ARCHIVE` is the `aiosendspin` tarball for commit `83209af414e1950dbbd0ebf60a9c0567b2c5f0c8`. The test checks its digest against `SOURCE.lock`.

To address an existing development guest from the host, use the forwarded player URL. This command reports the server's transmitted frames. The guest's codec and sink counters confirm decoding and output. A player using secure pairing also needs `--pairing-token-file FILE`.

```sh
python3 tests/sendspin-stream.py --archive "$SERVER_ARCHIVE" --url ws://127.0.0.1:18928/sendspin
```

The joint VM uses a restricted test listener. A persistent player should use its own protected state file and owner-issued pairing token. The basic test addresses the player directly. Music Assistant discovery, acoustic alignment between physical speakers, and sustained playback on the actual amplifier require separate testing.

The SDK, server, dependency commits, licences and their patch hashes are recorded in `dist/sendspin/licenses/SOURCE.lock`. The Linux adapter adds `data-capacity-reply.patch` to a temporary copy of the release sources. It handles a delayed queue-full reply so playback can resume when space frees up. The build tests this case over a real local socket before compiling the ARM player. The bundle includes that patch, the LibreEcho UI and Platform notices, and the TinyALSA and compiler runtime notices.
