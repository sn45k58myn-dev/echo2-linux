#!/bin/sh
set -eu
cd /tmp
echo '9a5850a05395978c9eb1a1c8b180f4501c5c3b13534c97921a677d2810a9c91b  /input/tinyalsa.tar.gz' | sha256sum -c -
tar -xf /input/tinyalsa.tar.gz
cd tinyalsa-a7cd26a17fe2add2ff38d2f281ed0b919187a068
cc -std=c11 -Wall -Wextra -Werror -Os -Iinclude -c /input/player.c -o /tmp/player.o
cc -D_GNU_SOURCE -Os -Iinclude -ffunction-sections -fdata-sections -c src/pcm.c src/pcm_hw.c src/snd_card_plugin.c
cc -static -Wl,--gc-sections /tmp/player.o pcm.o pcm_hw.o snd_card_plugin.o -o /tmp/echo2-player
strip /tmp/echo2-player
/tmp/echo2-player --help >/dev/null
install -m 0755 /tmp/echo2-player /out/echo2-player
