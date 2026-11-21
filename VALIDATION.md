# Echo 2 Linux development build

This record covers source builds, an emulated boot and a limited test on a physical 2017 Echo 2. These are the results for the initial development revision.

## Components built

| Local artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Radar kernel `zImage`, Linux 6.1.178+ | 4,650,712 | `72426b1d7e4ff05f1c370f46ff8bafcee69bd99261b2b5785280643f5f863c0a` |
| Radar device tree `libreecho-radar-puffin.dtb` | 56,726 | `7a65baf7929fd8bd23f98f513ce8e332d5aaee346515441990a459907f8600cf` |
| Small Alpine base `echo2-linux-base.cpio.gz`, Linux zlib build | 3,203,658 | `f0386cbf862e2023b967b1ef30d7c300630f4e48895f67a5ebccc2a3a703b0d9` |
| Voice feature `echo2-voice.squashfs` | 13,856,768 | `d3392ee7ed142c010e9854bfd81bdd3b90962f2694a89bedd50bf34c0b4fe0fb` |
| Player `echo2-player` (included in the base) | 38,716 | `d9d00fef7e90d7904126a8cd2f18d9943c2e5d0e83b3d063d7b9b0f35b996d70` |
| Voice daemon `techo5-echod` (included in the feature) | 32,440,480 | `488d2a6bbee47c62a832ee781bb4fab144c0cfb7d173e2d176c18cd79d0c1f14` |

The raw kernel, device tree and Linux-built small base total 7,911,096 bytes before boot headers, alignment and any other board-specific data. The combined userspace with voice occupied 16,904,471 bytes on its own, exceeding the prior project's 16 MiB boot envelope. The separate voice filesystem addresses this size constraint. The board's feature-storage location, boot packaging and recovery path still need hardware qualification.

Linux zlib and Windows zlib-ng produced different gzip streams for the small base. Both unpacked to the same 5,581,824-byte cpio archive with SHA-256 `6a502e6d8ca87881449eeaf36b5ded3ca208c07386e1fd592cc44f579718370b`.

## What passed

- A clean build generated the Radar kernel and device tree from the pinned board tree. The resulting configuration includes the Radar ASoC driver, gzip ramdisk support and a gzip-capable SquashFS reader.
- QEMU booted the small base with Alpine's generic ARMv7 kernel. The guest reached BusyBox init, classified the virtual machine as a different board, ran the PCM renderer and powered down cleanly. In a second configuration it verified and mounted the separate feature disk read-only. Its contents included the expected voice ELF and build metadata. A combined emulation-only image also ran the voice executable's version check.
- An ARM environment exercised music and speech rendering, gain, period boundaries, malformed input and board-detection fixtures. The component export also passed repeat verification and rejected changed, missing or additional files.
- On a physical full-size Echo 2, the read-only board probe found the speaker and array PCM interfaces, physical button and microphone privacy interfaces, the light-ring interface and wireless controls. Discovery shows that interfaces exist. Physical routing, mute behaviour and Wi-Fi association need functional testing.
- Under the speaker's existing kernel, an isolated temporary chroot ran the verified Alpine BusyBox and musl loader and reported ARMv7. Given a synthetic 16 kHz mono sample of value 3, the project player produced six 16-bit values of 3, as expected for three stereo 48 kHz frames. The test verified cleanup. It played no sound, captured no microphone audio and made no firmware change.
- The 0.16.8 source export reproduced 201 files across three pinned trees. The receipt SHA-256 was `0bcc54ef193e55a1355a1387a547be24c1bc8aca5d6da0ebe0fb8726fd0fced8`. Native Sendspin, Home Assistant interaction and duplex intercom still require end-to-end testing on this new system.
- Five audio compatibility checks ran on Alpine ARMv7 under emulation. The original echo canceller reduced a synthetic 20 ms echo by 36.1 dB in its fixture. In a separate integration check, the playback and capture components exchanged 2,880 stereo frames over a Unix socket, produced the expected 960 mono reference samples and handled a receiver disconnect. These numbers describe software tests. The new system still needs an acoustic iPhone call test on hardware.

See the [build guide](README.md) for the commands and the staged hardware plan.

## Physical speaker, array and mute baseline

The test unit ran confirmed LibreEcho 0.16.8. The operator heard two short chimes. The physical microphone button changed the privacy latch from 0 to 1 and back to 0. While muted, the new meter refused to open the capture device. The original ALSA capture stream still reported running while muted, so this observation establishes the latch and diagnostic behaviour only.

With the latch open, the project player fed a 1.25-second synthetic tone into the resident speaker engine. The ARMv7 project meter read the seven active microphone lanes directly from PCM24, retained energy sums and printed levels. Neither room audio nor a recording was saved. Both two-second windows used the same capture path.

| RMS in signed 16-bit units | Mic 0 | Mic 1 | Mic 2 | Mic 3 | Mic 4 | Mic 5 | Mic 6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Quiet | 35.2 | 32.7 | 32.5 | 31.6 | 31.0 | 31.8 | 31.8 |
| Tone window | 213.1 | 160.3 | 138.5 | 127.2 | 146.0 | 192.9 | 177.7 |
| Rise in dB | 15.6 | 13.8 | 12.6 | 12.1 | 13.5 | 15.7 | 14.9 |

The speaker's boot control reported `fixed-a`: both logical slots select the same physical boot image. The running kernel and separate installed recovery kernel both have `CONFIG_KEXEC` disabled. No verified temporary kernel boot was available over the connected network transport. The boot and recovery images matched their preflight hashes after testing. The voice integration reported ready and connected, and the physical mic was left unmuted.

Use [live-audio-baseline.py](tests/live-audio-baseline.py) for the cue check or with `--write-pilot pilot.pcm` to generate the fixed tone offline. The [lane test](tests/live-array-baseline.sh) takes a temporary device directory containing that tone, the project player as `player` and the [ARM mic meter](tests/radar-mic-check.c) as `meter`. It briefly pauses and restores the active voice services and uses the existing board routing. Runs on the independent kernel still require separate validation.
