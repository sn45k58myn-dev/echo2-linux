# Linux intercom room bridge

The optional bridge connects the Linux HTTPS intercom gateway's two PCM FIFOs to the 0.16.8 LibreEcho room companion. The companion feeds the system mixer and subscribes to the voice service's processed microphone stream. It shares the existing audio owner with music. This is a user-space package, and all tests run without a physical Echo.

## Build and test

Export the compatibility sources with `tools/export-audio-sources.py` using the three checkouts pinned by the project and `--output build/compat-complete`. The bridge builder verifies that export before compiling both ARMv7 programs in the existing Alpine player toolchain.

```sh
python tools/build-intercom-bridge.py
python tools/build-intercom.py
python tests/intercom-bridge-container.py
```

The last command needs Docker with ARMv7 support and the Python packages `cryptography` and `websockets` on the host. The test runs inside an isolated container with loopback HTTPS/WSS, a temporary TLS identity, a synthetic indexed mic source and a reader on the system PCM FIFO. It checks duplex PCM, fresh connections, cues, Assist release, denial when muted or in Do Not Disturb and closure during an active call. Only counts and assertions appear in the results.

`dist/intercom-bridge` contains `bin/echo2-intercom-bridge`, `bin/libreecho-intercomd`, `BUILDINFO`, `SHA256SUMS` and the applicable licence texts. Pass `--sources` or `--output` for another source/export directory or a separate destination. An existing bundle must match exactly.

## Connect a room implementation

1. Run the native `libreecho-intercomd` with the room's config, privacy state, audio, wake and LED sockets, system mixer FIFO, Assist lease and acknowledgement paths. The room must explicitly allow calling and use Home Assistant voice mode. Its audio policy and local indications remain active.
2. Create a directory owned by the service account with mode 0700. Start the bridge under the same UID as the companion. Pass it the private directory, the companion's seqpacket socket and the two live policy-file paths. Each state file contains a single `0` while calls are allowed. Missing, unreadable or asserted state closes the bridge.
3. Start the HTTPS gateway on `--backend fifo`. Set `--room-mic` to `room-mic.pcm` inside that directory and `--room-speaker` to its sibling `room-speaker.pcm`. Pass the same policy files as `--privacy-state` and `--dnd-state`. Configure the gateway's certificate, key, owner passphrase and room opt-in as described in the [gateway documentation](../intercom/README.md).

```sh
echo2-intercom-bridge "$room_fifo_dir" "$companion_socket" "$privacy_state" "$dnd_state"
```

The bridge makes new 0600 FIFOs for each call. The gateway opens them on an authenticated call. After the phone starts sending PCM, the bridge connects to a same-UID companion and waits for its `READY` reply. During the cue and Assist handoff, the gateway receives silence. The media format at this boundary is mono signed 16-bit little-endian PCM at 16 kHz, with 320 samples every 20 ms. The original companion converts the phone signal into a stereo 48 kHz mixer input and returns indexed 16 kHz room capture to the caller.

## Validation limits

The synthetic source stands in for the audio already processed by the room voice service. The bench proves the transport into the shared mixer and the reverse mic path. Physical mic channel selection, actual mute-switch reporting, room-acoustic cancellation, hardware cue/LED behaviour, music ducking and speaker timing still need a board running the new Linux audio drivers. The bench uses no room recordings.

The native companion comes from the [LibreEcho-UI 0.16.8 source](https://github.com/sn45k58myn-dev/LibreEcho-UI/tree/984ebc22cf68f1900e931d1129357db91951ea06). Attribution and notices accompany the binaries.
