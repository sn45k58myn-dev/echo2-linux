#!/bin/sh
# Run on a working Radar with an idle 0.16.8 audio engine. The caller supplies
# the project meter, player and a short, low-level synthetic 16 kHz mono PCM.
set -eu
dir=${1:?test directory required}
latch=/sys/devices/platform/amz_privacy/privacy_state
capture=/proc/asound/card0/pcm24c/sub0/status
sink=/run/libreecho-audio/system.pcm
wake=/etc/init.d/libreecho-waked.init
assist=/etc/init.d/libreecho-esphomed.init
test "$(cat "$latch")" = 0 || { echo "Microphone is muted"; exit 2; }
grep -qx target_id=radar_puffin /etc/libreecho/target
grep -q '"state":"idle"' /run/libreecho-audio/status.json
test -p "$sink" && test -s "$dir/pilot.pcm" && test -x "$dir/meter" && test -x "$dir/player"
(cd "$dir" && echo "0e5e642d478522143cd6db6a6dadecf158034d88b97f78eca915ba36faeae43d  pilot.pcm" | sha256sum -c -)

resume_wake=0
resume_assist=0
restore() {
    rc=$?
    trap - EXIT HUP INT TERM
    if test "$resume_wake" = 1; then "$wake" start || rc=1; fi
    if test "$resume_assist" = 1; then "$assist" start || rc=1; fi
    exit "$rc"
}
trap restore EXIT
trap 'exit 1' HUP INT TERM

if "$assist" status >/dev/null 2>&1; then resume_assist=1; "$assist" stop; fi
if "$wake" status >/dev/null 2>&1; then resume_wake=1; "$wake" stop; fi
for n in 1 2 3 4; do grep -qx closed "$capture" && break; sleep 1; done
grep -qx closed "$capture" || { echo "Capture is owned by another service"; exit 1; }
echo "Quiet reference"
"$dir/meter" meter
test "$(cat "$latch")" = 0 || exit 2
echo "Synthetic tone via the resident speaker engine"
(
    sleep 0.25
    timeout 6 sh -c '"$1/player" render speech 100 < "$1/pilot.pcm" > /run/libreecho-audio/system.pcm' sh "$dir"
) &
pilot=$!
"$dir/meter" meter || { wait "$pilot" || true; exit 1; }
wait "$pilot"
