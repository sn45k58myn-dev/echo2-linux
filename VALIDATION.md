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

## Independent Linux audio services

The expanded compatibility snapshot contains 203 files. Its receipt SHA-256 is `67b164b60b0d9b2792bf5afe9dc76c45baeb1187e69860ddee9f05332bd50812`. The [audio test room](AUDIO-SERVICES.md) ran the Sendspin receiver and the intercom together in the project's ARMv7 Alpine userspace under QEMU. The guest had no physical audio device or access to the household integrations.

| Test | Observed result |
| --- | --- |
| Real iPhone on Wi-Fi | The caller heard the room's 440 Hz tone, spoke and heard the two higher reply tones. The guest counted 425 packets from the phone, 398 back to the phone and one speech-triggered reply. The caller ended the call and the room returned to idle. |
| Final intercom executable | Only display text changed after the handset candidate. The final ARMv7 build reproduced byte for byte and passed a browser call and a full-guest encrypted round trip. The latter measured 72 packets in each direction, input RMS 4,242 and the expected 440, 660 and 880 Hz tones. |
| Linux room audio interface | Distinct 16 kHz PCM frames travelled in both directions through the FIFO backend. The test included a partial capture read and verified that changing the privacy state closed an active call. |
| Native room companion and mixer | Two ARMv7 calls ran through HTTPS/WSS, the gateway, the bridge and the 0.16.8 Linux companion. The synthetic mic returned 85 and 86 exact frames after startup out of 90 per call. The 48 kHz mixer received 83,517 and 84,477 matching stereo sample pairs. The test also exercised a second call, room opt-out, mute and Do Not Disturb denial, and disconnection when the privacy latch closed. |
| Secure paired Sendspin | A real Sendspin server paired with the ARM receiver and sent six seconds of 48 kHz stereo FLAC. Two runs using the corrected virtual playback clock decoded the 720 Hz pilot at the expected level with no receiver sync losses. |
| Playback queue recovery | Under load a full sink queue could reject a packet after its write deadline. If the timing poll consumed that rejection, the receiver could stay connected with its playback cursor stuck. A socket test reproduced the stall on the original adapter. The Linux patch lets the writer retry once space opens. The test checks both audio content and the exact accepted frame count. |
| Paired playback after the fix | The ARM receiver decoded a 24-second encrypted FLAC programme. It rendered 2,281,348 nonzero channel samples at the correct 720 Hz pitch and peak of 2,602. The receiver reported zero sync losses in this run. |
| Simultaneous music and call on the final system | The server sent a 40-second, 1,920,000-frame stereo FLAC programme. While it rendered, an authenticated intercom call exchanged 72 packets each way and returned all three expected tones. The last virtual output report showed 1,968,016 frames including startup padding, at least 3,746,794 nonzero channel samples and a peak of 2,602. There were no reported sync losses, and the earlier midstream stall did not recur. |
| Two-player Sendspin group | Both independently paired Linux receivers decoded all five markers in a six-second run of the final queue fix. Each rendered 282,852 nonzero frames. The largest estimated difference between virtual output writes was 2.3 ms. The largest polling bracket was 4.7 ms, and both outputs wrote in 5 ms slices on a shared host. Two earlier runs also decoded every marker. |

This validation used an isolated server and synthetic room audio. Physical multi-room alignment and acoustic echo cancellation on the independent board kernel still need measurements.

Both audio package formats contain the same six ARMv7 executables and their licence notices. The image builder unpacked the SquashFS and checked the inventory, modes and file bytes. The combined initramfs was booted with the project base and served an authenticated call.

| Final local artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `echo2-audio-services.cpio.gz` | 4,699,832 | `26ba4390a45745faa7fa52d9f8f4b8e71a8db4946a3566361f8f213c9343afa4` |
| `echo2-audio-services.squashfs` | 4,747,264 | `de2752b72959e2b102d46fc15275bbd0db4091122fba65052a5233b28e997f6e` |

## Calibrated microphone trial

One full-size Echo 2 on LibreEcho 0.16.8 loaded all seven of its own microphone calibration values. During a 56-second RAM-only trial with native Sendspin at 30%, the factory processor supplied microphone audio to Home Assistant. The operator confirmed that the speaker answered one spoken time request. Both local comparison paths crossed the 0.5 wake threshold in the same 80 ms scoring block. The normal path peaked at 0.941 and the factory path at 0.965. One spoken request cannot establish a change in detection rate.

The Assist FIFO accepted 7,000 blocks. It reported zero writes dropped for backpressure and zero unavailable writes. The installed voice services were verified after restoration. The spoken trial retained only timings, scores and counts.

### Gates for a production image

| Evidence still needed | Acceptance check |
| --- | --- |
| Recoverable boot on the independent kernel | Boot a test image on a recovery-connected Radar and demonstrate return to the known-good image. |
| Physical privacy and audio routing | Verify power cycling, physical mute across every capture path, Wi-Fi loss, intercom interruption, Sendspin sync and two-way calls on that kernel. |
| Wake performance over music | Compare multiple people, distances and playback levels against the same baseline. Count missed commands and false wakes during music-only intervals. |
| Offline operation and distribution | Verify network isolation for optional owner-supplied components. Establish redistribution rights or ship an independent replacement. |
| Upgrade and recovery | Verify signed upgrades, interrupted-update recovery and rollback on hardware, then repeat the tests from a clean install. |

[Reproduce the audio tests](tools/factory-asr-bench/README.md#run-the-paired-audio-regression-from-a-fresh-checkout)
