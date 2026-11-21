# Sendspin and iPhone intercom on Echo 2 Linux

The optional audio package runs in the project's Alpine ARMv7 userspace. A generic ARM virtual machine can play a real Sendspin stream into a virtual audio output and accept an authenticated intercom call from an iPhone. Running this test requires no connection to an Echo, Home Assistant or Music Assistant.

## Build

1. Build the base with `make base`. Follow the [Sendspin build instructions](services/sendspin/README.md) to export the pinned source checkouts and build into `dist/sendspin`. The builder also compiles the physical Radar playback engine.
2. Run `python3 tools/build-intercom.py` to generate `dist/intercom`. Docker supplies the pinned Go compiler. The executable includes the mobile page and AudioWorklet. [Intercom endpoints and tests](services/intercom/README.md)
3. Run `python3 tools/build-intercom-bridge.py` with the same source export. This builds the room audio companion and the adapter between its local socket and the intercom's audio pipes. The virtual room uses the probe backend. To test the mixer and capture connections, follow the [room bridge guide](services/intercom-bridge/README.md).
4. Run `make audio-services`. It verifies the binary inventories, hashes and static ARM ELF format, then adds the executables and licence notices to `build/echo2-audio-services.cpio.gz`. To build a read-only feature filesystem on Linux, install squashfs-tools and run `python3 tools/build-audio-feature.py --squashfs`. Both outputs are separate from the board boot image.

## Start the test room

Use the Alpine ARMv7 netboot archive and extracted kernel listed in the [base build guide](README.md). Choose the host's Wi-Fi or Ethernet address for the phone. Prepare a private directory containing a trusted TLS certificate chain in `server.crt`, its matching `server.key` and a temporary room passphrase in `password`. The certificate must cover the exact IP address or DNS name used in Safari. Keep these files outside source control. Only the server's leaf key is needed.

```sh
python3 tools/run-audio-bench.py \
  --kernel "$VM_KERNEL" --netboot "$NETBOOT" --private "$TEST_KEYS" \
  --bind "$BENCH_LAN_ADDRESS" --https-port 9443 --seconds 3600
```

On home Wi-Fi, open `https://<bench-address>:9443/intercom` in Safari. Sign in, tap **Call room**, allow the microphone and listen for the low tone. Say a word and pause. Two higher tones mean the guest received the phone's audio and sent its reply back through the call. Tap **End call**. The virtual room has no access to a household microphone. It keeps packet and level measurements in RAM.

To exercise music on the same running guest, install the pinned server's dependencies and run the following from the host. The component guide identifies the server archive and its checksum. The receiver's loopback port is published only on the Docker host. Its virtual output reports decoded sample counts and peaks in the container log.

```sh
python3 tests/sendspin-stream.py --archive "$SERVER_ARCHIVE" --url ws://127.0.0.1:18928/sendspin
docker logs echo2-linux-audio-bench
```

The guest expires after the selected test window. To end a session early, run `docker stop echo2-linux-audio-bench`. An automated [two-way intercom test](tests/intercom-roundtrip.py) is available when the test room is idle.

For a group test, use `python3 tests/sendspin-group.py --archive "$SERVER_ARCHIVE"` in the isolated ARM-capable Linux environment described in the [Sendspin test guide](services/sendspin/README.md). It pairs two receivers, identifies five markers in their decoded output and compares the virtual audio write times.

## Hardware qualification

The synthetic room and virtual audio output make transport failures distinguishable from microphone, amplifier and acoustic problems. The physical speaker still needs its board audio routing, capture and mute inputs validated under the project's kernel. An acoustic call and a measured two-player test are needed to qualify echo cancellation and multi-room sync. [Validation record](VALIDATION.md)
