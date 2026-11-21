# Boot and verify the development userspace

Run these commands from the project directory on Linux with Python 3.11 or later. Install `qemu-system-arm` and `squashfs-tools` to run the emulator and package the optional voice filesystem. The project Makefile builds the static player. The optional voice builder produces `dist/voice`.

```sh
python3 tools/build-rootfs.py --player build/echo2-player --output build/echo2-linux-base.cpio.gz
python3 tools/build-rootfs.py --voice-dir dist/voice --feature-output build/echo2-voice.squashfs
```

The first command verifies Alpine's pinned ARMv7 minirootfs digest. Both commands print the SHA-256 of the generated file. An identical output can be reused. Use `--replace` to publish an updated image over a previous build. The feature builder verifies the contents and permissions of every file after extraction. To independently compare the packaged executable with the voice bundle, run these commands.

```sh
unsquashfs -cat build/echo2-voice.squashfs usr/bin/techo5-echod | sha256sum
cat dist/voice/SHA256SUMS
```

The CPIO member ordering, file metadata and gzip timestamp are fixed. For identical compressed bytes, use the same compressor implementation and version. The verified Linux build used CPython 3.11.18 with zlib 1.3. Windows CPython 3.14.5 with zlib-ng generated a different compressed stream containing the identical CPIO. To compare the uncompressed filesystem across builders, run `gzip -dc build/echo2-linux-base.cpio.gz | sha256sum`. The tested player-equipped base produced `6a502e6d8ca87881449eeaf36b5ded3ca208c07386e1fd592cc44f579718370b` for that command.

## ARM emulator

Obtain the matching generic kernel and driver bundle from the [official Alpine ARMv7 release files](https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/).

```sh
curl --fail --location --output build/alpine-netboot-3.24.4-armv7.tar.gz \
  https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/alpine-netboot-3.24.4-armv7.tar.gz
printf '%s  %s\n' 047e5609d8bb2d61867eb5309f63e1e3bf0793d73b0fa107da7df008c6d55110 \
  build/alpine-netboot-3.24.4-armv7.tar.gz | sha256sum -c -
tar -xOf build/alpine-netboot-3.24.4-armv7.tar.gz boot/vmlinuz-lts > build/vmlinuz-lts
python3 tests/boot-rootfs.py build/echo2-linux-base.cpio.gz --kernel build/vmlinuz-lts \
  --expect-player --feature build/echo2-voice.squashfs --netboot build/alpine-netboot-3.24.4-armv7.tar.gz
```

`E2-PLAYER-OK` checks a fixed PCM sample in the guest. `E2-FEATURE-OK` checks the raw SHA-256 inside the guest, mounts the virtual feature disk read-only, verifies the voice executable and its build information, then unmounts it. The test loads the generic kernel's matching modules from a temporary second initramfs and powers down the VM. The base system starts without network or audio services. The emulator uses a virtual ARM board and serial console. Radar-specific audio, radios, buttons and LEDs require separate hardware checks.

To run the optional voice binary's version command in the isolated emulator, build a separate combined initramfs. This larger artifact is for QEMU. The upstream Go program probes GPIO at startup even for `--version`. For host-side inspection use `python3 tools/build-voice.py --inspect`.

```sh
python3 tools/build-rootfs.py --player build/echo2-player --voice-dir dist/voice \
  --output build/echo2-linux-initramfs.cpio.gz
python3 tests/boot-rootfs.py build/echo2-linux-initramfs.cpio.gz --kernel build/vmlinuz-lts \
  --expect-player --expect-voice
```
