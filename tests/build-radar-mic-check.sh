#!/bin/sh
# Run inside tools/player-image with the pinned TinyALSA archive and project
# mounted read-only as /archive.tar.gz and /project. Mount a writable /out.
set -eu
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
echo '9a5850a05395978c9eb1a1c8b180f4501c5c3b13534c97921a677d2810a9c91b  /archive.tar.gz' | sha256sum -c -
tar -xf /archive.tar.gz
cd tinyalsa-a7cd26a17fe2add2ff38d2f281ed0b919187a068
cc -std=c11 -Os -Wall -Wextra -Werror -Iinclude -c /project/tests/radar-mic-check.c -o "$work/check.o"
cc -D_GNU_SOURCE -Os -Iinclude -ffunction-sections -fdata-sections -c src/pcm.c src/pcm_hw.c src/snd_card_plugin.c
cc -static -Wl,--gc-sections "$work/check.o" pcm.o pcm_hw.o snd_card_plugin.o -lm -o "$work/radar-mic-check"
strip "$work/radar-mic-check"
"$work/radar-mic-check" --self-test
install -m 0755 "$work/radar-mic-check" /out/radar-mic-check
sha256sum /out/radar-mic-check
