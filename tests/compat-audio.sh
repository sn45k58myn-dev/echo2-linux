#!/bin/sh
# Exercise the original LibreEcho 0.16.8 audio test programs with Linux/musl.
# Requires the local export from tools/export-audio-sources.py, a C compiler
# with libc development headers and SpeexDSP development headers/library.
set -eu

project=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-"$project/build/compat-src"}
ui="$snapshot/ui"
platform="$snapshot/platform/tools/mt8163-arm32/airplay"
if [ ! -f "$snapshot/snapshot.json" ]; then
    echo "Run tools/export-audio-sources.py before this test." >&2
    exit 1
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

compile() {
    "${CC:-cc}" -D_POSIX_C_SOURCE=200809L -std=c99 -O2 \
        -Wall -Wextra -Wpedantic -Werror "$@"
}

compile "$platform/test_aec_reference.c" "$platform/aec_reference.c" -o "$work/sender"
compile -I"$ui/src" "$ui/tests/test_voice_reference.c" "$ui/src/adapter/voice_reference.c" \
    -lspeexdsp -lm -o "$work/receiver"
compile -I"$ui/src" "$ui/tests/test_voice_aec.c" "$ui/src/adapter/voice_aec.c" \
    -lspeexdsp -lm -o "$work/filter"
compile -I"$ui/src" -I"$ui/src/adapter" "$ui/tests/test_voice_aec_guard.c" \
    "$ui/src/adapter/voice_aec_guard.c" "$ui/src/adapter/voice_aec.c" \
    "$ui/src/adapter/voice_reference.c" -lspeexdsp -lm -o "$work/guard"
compile -I"$platform" -I"$ui/src" "$project/tests/compat-audio-link.c" \
    "$platform/aec_reference.c" "$ui/src/adapter/voice_reference.c" \
    -lspeexdsp -lm -o "$work/link"

for test in sender receiver filter guard; do
    "$work/$test"
done
"$work/link" "$work"
