#!/bin/sh
# Run inside the existing ARMv7 build container. SRC is this directory;
# PRIVATE is an owner's scratch directory containing their extracted system/.
set -eu
: "${SRC:?path to these sources}" "${PRIVATE:?path to the private ARMv7 stock files}"
cc=${CC:-gcc}
stock_lib=$PRIVATE/system/lib
for name in private-exec native-socket-once log-sink start-bus; do
    "$cc" -O2 -static -Wall -Wextra "$SRC/$name.c" -o "$PRIVATE/$name"
done
# The DSP and Android libraries both use the 32-bit softfp calling convention.
set -- -marm -mfloat-abi=softfp -Os -ffreestanding -nostdlib -fno-stack-protector -fno-builtin
"$cc" "$@" -fPIC -shared -Wl,-soname,libshm.so,--hash-style=both \
    "$SRC/private-shm.c" -L"$stock_lib" -lc -o "$PRIVATE/native-shm.so"
set -- "$@" -fPIE -pie -Wl,-e,_start,--dynamic-linker,/system/bin/linker,--allow-shlib-undefined,--hash-style=both
"$cc" "$@" "$SRC/driver.c" -L"$stock_lib" -lasp -lc -o "$PRIVATE/driver"
"$cc" "$@" -O2 -Wl,-E "$SRC/native-stream.c" -L"$stock_lib" -lasp -llog -lc -lgcc -o "$PRIVATE/native-stream"
