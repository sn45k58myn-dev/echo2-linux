#!/bin/sh
# Run in an exclusively allocated ramfs directory containing the private jail.
set -eu
test "$(stat -f -c %T .)" = ramfs || exit 70
test -x ./run-private && test -d ./jail/bench || exit 71
mode=${1:-fixture}
case "$mode" in fixture|stream) ;; *) exit 72 ;; esac
jail=$(pwd)/jail
umask 077
mkdir session.active || exit 75
logpid=
buspid=
mounted=0
finish() {
    for pid in "$buspid" "$logpid"; do
        test -z "$pid" || kill "$pid" 2>/dev/null || :
    done
    for pid in "$buspid" "$logpid"; do
        test -z "$pid" || wait "$pid" 2>/dev/null || :
    done
    if test "$mounted" = 1; then umount "$jail/proc" || return 1; fi
    if test -d "$jail/bench/calibration"; then
        for i in 0 1 2 3 4 5 6; do rm -f "$jail/bench/calibration/miccal.$i"; done
    fi
    rmdir session.active
}
trap finish EXIT
trap 'exit 125' INT TERM HUP
if test -d "$jail/bench/calibration"; then
    sh ./stage-mic-calibration.sh /sys/firmware/devicetree/base/idme "$jail/bench/calibration"
fi
for node in 'null 3' 'urandom 9' 'random 8' 'zero 5'; do
    set -- $node
    if test ! -c "$jail/dev/$1"; then
        mknod "$jail/dev/$1" c 1 "$2"
        chmod 666 "$jail/dev/$1"
    fi
done
rm -f "$jail/dev/socket/dbus" "$jail/dev/socket/logdw"
mkdir -p "$jail/proc"
mount -t proc -o ro,nodev,nosuid,noexec proc "$jail/proc"
mounted=1
service_log=native-services.log
if test "$mode" = stream; then service_log=/dev/null; fi
./run-private "$jail" /bench/log-sink >"$service_log" 2>&1 &
logpid=$!
./run-private "$jail" /bench/start-bus >native-bus.log 2>&1 &
buspid=$!
n=0
while test ! -S "$jail/dev/socket/dbus" && test "$n" -lt 30; do sleep .1; n=$((n+1)); done
test -S "$jail/dev/socket/dbus" || exit 73
if test "$mode" = stream; then
    timeout -k 1 150 ./run-private "$jail" /bench/native-stream || exit 74
    exit 0
fi
echo "native-start $(cut -d' ' -f1 /proc/uptime)"
timeout -k 1 32 ./run-private "$jail" /bench/driver || exit 74
echo "native-end $(cut -d' ' -f1 /proc/uptime)"
wc -c "$jail/tmp/asr.pcm"
