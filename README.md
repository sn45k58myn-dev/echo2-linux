# Echo 2 Linux

An independent Linux system under development for the **full-size Amazon Echo (2nd generation, 2017)**, board `radar_puffin`, MediaTek MT8163. This repository builds an Alpine boot filesystem, a board kernel and device tree, a PCM audio player and optional services for voice, Sendspin and iPhone intercom. The base builds directly from source and Alpine packages.

The base has booted in an emulated ARM machine. Under the existing 0.16.8 kernel and mixer, a physical Echo played a test signal from the project renderer. The project meter detected it on all seven mic channels and blocked capture while the hardware mute was engaged. A physical boot of the independent kernel and validation of its own audio routing, Wi-Fi and voice services are still open. [Hardware results](VALIDATION.md)

## What we reuse

| Component | Source and purpose |
| --- | --- |
| Board kernel and device tree | The credited LibreEcho Linux 6.1 port supplies Radar/Puffin support, including its ASoC audio driver. This project compiles the kernel directly from a pinned source tree. [Kernel source](https://github.com/aslater3/LibreEcho-Linux/tree/5bc811d3cd7091e8d70f9faa0bc12c5cf4e77ae0) |
| Linux userspace | Official Alpine ARMv7 minirootfs, musl and BusyBox. The project supplies its own init, board detection, packaging and test programs. [Alpine releases](https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/) |
| Audio rendering | The project player uses TinyALSA for PCM output. It accepts 48 kHz stereo music or 16 kHz mono speech, applies the selected gain and renders 48 kHz stereo. Its `render` mode exercises the same conversion without opening an audio device. [TinyALSA](https://github.com/tinyalsa/tinyalsa/tree/a7cd26a17fe2add2ff38d2f281ed0b919187a068) |
| Optional voice service | TECHO5's `echod` is built from source with its Dot/Radar build tag. Its original voice-daemon lineage includes Yuri Gelfand's EchoLocal. The package includes its upstream notices and Go dependency licences. [Voice component notes](licenses/voice/README.md) |
| Sendspin and iPhone intercom | The optional ARMv7 audio package includes the native Sendspin receiver and a Linux intercom service using the 0.16.8 Safari client. Both run together in the project's Alpine guest. [Build and test the audio services](AUDIO-SERVICES.md) |

TECHO5's full-size Echo 2 instructions are in its companion `techo5-dot` repository. The team documents both a vendor Linux kernel and an optional 6.1 port credited to LibreEcho. The TECHO5 credits separately describe the EchoLocal contribution to its voice daemon. Those records establish reuse at the component level. [TECHO5 Radar hardware notes](https://github.com/HuskerMinion/techo5-dot/blob/4841b75c80b36d7e3221860be1175804ef77e579/docs/hardware-radar.md) · [TECHO5 credits](https://github.com/HuskerMinion/techo5/blob/3907d43be77f94c075c0ff72826cfc52ac1b0f94/NOTICE)

## Build the system

Run the following from this directory on Linux with Python 3.11+, GNU Make and Docker with Linux ARMv7 support. Initial builds download pinned source, packages and container images. The individual Python builders can also be invoked directly. The feature filesystem requires `mksquashfs` from squashfs-tools.

```sh
make base
make feature
```

The base is `build/echo2-linux-base.cpio.gz`, and the voice service lives in the separate read-only `build/echo2-voice.squashfs`. The latter is optional. The base starts BusyBox init and mounts volatile and kernel filesystems. A development shell is available when the VM kernel command line includes `echo2.console=1`. Voice activation, microphone capture and mounting on-device storage are separate integration steps.

To build the Radar kernel, install a 32-bit ARM hard-float cross-toolchain (`arm-linux-gnueabihf-`), native build tools and the usual kernel build dependencies including flex, bison, bc and OpenSSL development headers. Fetch the exact board source and build into a separate directory.

```sh
git init build/kernel-src
git -C build/kernel-src fetch --depth=1 https://github.com/aslater3/LibreEcho-Linux.git 5bc811d3cd7091e8d70f9faa0bc12c5cf4e77ae0
git -C build/kernel-src checkout --detach FETCH_HEAD
make kernel KERNEL_SRC="$PWD/build/kernel-src"
```

The two outputs are `build/linux/arch/arm/boot/zImage` and `build/linux/arch/arm/boot/dts/libreecho-radar-puffin.dtb`. The kernel builder checks the source commit and tree before it runs. Set `JOBS`, `CROSS_COMPILE` or `KERNEL_OUT` to override the host defaults.

## Test without flashing

Install `qemu-system-arm`. Obtain Alpine's official `alpine-netboot-3.24.4-armv7.tar.gz` and extract `boot/vmlinuz-lts` from it. The VM test validates the kernel digest and boots the same project userspace on a generic ARM machine with networking disabled. To test the separate feature, also install `unsquashfs`. [Alpine ARMv7 netboot download](https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/)

```sh
make boot-test VM_KERNEL=/path/to/boot/vmlinuz-lts
make feature-test VM_KERNEL=/path/to/boot/vmlinuz-lts NETBOOT=/path/to/alpine-netboot-3.24.4-armv7.tar.gz
```

The reusable [physical userspace test](tests/hardware-userspace.py) takes an explicitly selected, previously authorized development device. It runs a minimal Alpine chroot in temporary storage, checks the rendered samples and removes the temporary files. It requires an existing shell with chroot privileges. It never opens an audio device. This tests CPU and userspace compatibility independently of a board boot.

To inspect or port the earlier audio work, supply Git checkouts of the three repositories named in the manifest. Each must contain the pinned commit. The export reads committed Git objects and preserves original paths, tests and notices. The verification pass checks every exported file.

```sh
python3 tools/export-audio-sources.py --product /src/LibreEcho --ui /src/LibreEcho-UI --platform /src/LibreEcho-Platform
python3 tools/export-audio-sources.py --verify --product /src/LibreEcho --ui /src/LibreEcho-UI --platform /src/LibreEcho-Platform
```

On Alpine with `gcc`, `musl-dev` and `speexdsp-dev` installed, run `sh tests/compat-audio.sh` after the export. It compiles the published 0.16.8 echo-reference producer, consumer and tests and passes synthetic audio between the two over a real Unix socket. [Audio regression script](tests/compat-audio.sh)

## Development milestones

1. **Boot and storage.** Validate a reversible hardware boot of the small kernel and base. Bring up Wi-Fi with the correct device firmware and test read-only feature storage. Measure the complete boot image against the 16 MiB envelope. Wi-Fi and microphone FPGA firmware need their own provenance and redistribution review.
2. **Physical audio and privacy.** Bring up one playback and capture owner. Test speaker gain, mic channels, ring and the physical mute switch on real hardware. Capture must stop on mute and remain off if privacy state is unavailable.
3. **Home Assistant voice.** Pair the chosen voice service, connect mic capture and TTS, and test the server-side wake word over music. Evaluate on-device wake detection separately using the same distance, volume and phrases.
4. **Synchronized music and calls.** Exercise the guest services and board adapters in isolation. On a development speaker, validate the playback clock, echo reference, call indication, mute and Do Not Disturb. Measure acoustic echo and two-speaker sync across calls.
5. **Release.** Test persistent settings, signed updates, interrupted-update recovery and rollback on a development speaker before offering a daily-use image.

## Credits and hardware use

Credit belongs to the Alpine, BusyBox, Linux, TinyALSA, LibreEcho, TECHO5, EchoLocal and Sendspin contributors and to the authors of the dependencies named in the component notices. Original licence and source-offer terms apply to their respective components. This development work was prepared with AI assistance. Experimental software is provided without a hardware warranty or a promise of repair or recovery. [Build and hardware validation](VALIDATION.md)
