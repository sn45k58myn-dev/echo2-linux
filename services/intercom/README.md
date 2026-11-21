# Linux room intercom

The service runs an authenticated HTTPS page and a foreground WSS call on the same port. The iPhone client and its bounded 16 kHz AudioWorklet come from the previously tested LibreEcho 0.16.8 client. The independent Linux gateway supplies sign-in, call reservations, audio scheduling and room endpoints. [Attribution and original sources](NOTICE)

Build from the project directory with `python3 tools/build-intercom.py`. The static Linux ARMv7 executable and licences are under `dist/intercom`. Supply a passphrase file readable only by the service and a trusted server certificate and key for the exact address opened in Safari.

```sh
dist/intercom/bin/echo2-intercom --listen :8443 \
  --cert /run/keys/room.crt --key /run/keys/room.key \
  --password-file /run/keys/room-password --allow-calls --backend probe
```

Open the HTTPS address in Safari, sign in, tap **Call room** and grant microphone access. Listen for a low note, say a word and pause. The test room generates two higher notes when it receives speech followed by a pause. The signed-in page shows the phone and room packet counts, and the number of voice confirmations. The probe uses synthetic room audio and retains only aggregate statistics. It opens no room hardware. Leaving the page ends the call.

The `fifo` backend connects an external Linux room engine. Start its capture writer and playback reader on separate owner-only named pipes. Supply their paths with `--room-mic` and `--room-speaker`. Each carries 16 kHz mono, signed 16-bit little-endian samples. The gateway preserves partial reads until it has a full 320-sample frame. A disconnected room engine, prolonged capture starvation, or a full playback pipe ends the call.

FIFO mode additionally requires `--privacy-state` and `--dnd-state`. Each must read exactly `0` after trimming whitespace while access is allowed. Changing either to `1` or losing the state file closes an ongoing call. The room engine owns any native acoustic echo cancellation, call indication, Sendspin ducking and resume. Integrating and measuring those behaviours on the Echo 2 board is a separate hardware test.

The service permits one foreground call at a time and gates calls behind `--allow-calls`. A signed-in client obtains a single-use ticket before opening WSS. Sessions expire after an hour, unused tickets after 30 seconds, and connected calls after ten minutes. The server compares request origins with the actual HTTPS host and keeps credentials out of URLs and application logs. The local status endpoint `/api/v1/intercom` exposes only session-protected counts and peak levels.

## Verification

- `python3 tests/intercom-container.py` starts the actual ARMv7 binary in Alpine and exercises sign-in, WSS and bidirectional synthetic PCM. It binds only to the loopback interface and creates a short-lived test identity. Install Python's `cryptography` and `websockets` packages for this test. Add `--browser` with Node and Playwright Chromium installed to exercise the call page using a synthetic microphone.
- `python3 tests/intercom-roundtrip.py --origin https://host:port --password-file /path/to/password --ca /path/to/issuer.pem` exercises an idle probe in a guest or on another host. It checks the frequencies and both sequence counters and signs out at the end.
- From this directory on Linux, run `go test -race ./...` for the named-pipe duplex and live privacy test. It compares distinct samples in both directions, including a split capture frame, before toggling the privacy input and checking call teardown.
