#!/bin/sh
# Run inside the ARMv7 compiler image, with verified UI sources at /src.
set -eu
mkdir -p /out/bin /out/licenses
cc -static -std=c11 -Wall -Wextra -Werror -Os -Wl,--build-id=none -s /project/bridge.c -o /out/bin/echo2-intercom-bridge
cd /src
cc -static -std=c99 -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE -Os -Wl,--build-id=none -s \
    -Isrc -Isrc/adapter src/adapter/intercomd.c src/intercom_protocol.c src/adapter/radio_resample.c \
    src/adapter/adapter_client.c src/config_store.c src/json.c src/log.c -lm -o /out/bin/libreecho-intercomd
cp LICENSE /out/licenses/LibreEcho-UI-LICENSE
cp /project/NOTICE /out/licenses/
cp /project/LICENSE.musl /out/licenses/
cp /project/COPYING3 /project/COPYING.RUNTIME /out/licenses/
cd /out
{ cc --version | head -n1; apk --no-network info -v musl libgcc; } > BUILDINFO
sha256sum bin/echo2-intercom-bridge bin/libreecho-intercomd > SHA256SUMS
