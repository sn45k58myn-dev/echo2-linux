#!/bin/sh
# No configuration edits. The installed voice services are restarted by their
# own init scripts. A phone/intercom, mute or a lost controller ends the trial.
set -eu
umask 077
d=/run/libreecho/groot-paired-diag
mi=/etc/init.d/libreecho-micd.init
wi=/etc/init.d/libreecho-waked.init
sm=/usr/local/sbin/libreecho-micd
sw=/run/libreecho/features/wakeword/root/usr/local/sbin/libreecho-waked
tw=$d/mirror-waked
exe() { readlink "/proc/$(cat "/var/run/libreecho-${1}d.pid" 2>/dev/null)/exe" 2>/dev/null || :; }
stock_ok() {
    test "$(sha256sum "$sw" | cut -d' ' -f1)" = 0ab166a8a225cd6e60a9f1edd09015d279cf78e9c0389473dcc4773d826060da &&
    test "$(sha256sum "$sm" | cut -d' ' -f1)" = 4f0788c06f5d18967ac72cac3dbacf8bcdb494390e617bafac749f40837aed3a &&
    grep -qx 'target_id=radar_puffin' /etc/libreecho/target
}
allowed() {
    test "$(cat /sys/devices/platform/amz_privacy/privacy_state)" = 0 &&
    { test ! -e /data/libreecho/config/do-not-disturb || test "$(cat /data/libreecho/config/do-not-disturb)" = off; } &&
    test ! -e /run/libreecho/intercom.lease && test ! -e /run/libreecho/intercom-assist.lease &&
    test ! -e /data/libreecho/update/install.lock && test ! -e /data/libreecho/update/pending &&
    test ! -e /data/libreecho/config/wake-dump-seconds
}
healthy() { test "$(exe mic)" = "$sm" && test "$(exe wake)" = "$1" && test -S /run/libreecho/wakeword.sock && test -S /run/libreecho-audio/aec-reference.sock; }
wait_healthy() { for i in $(seq 1 30); do if healthy "$1"; then return; fi; sleep 1; done; return 1; }
remove_stale_own_mirror() {
    path=/run/libreecho-audio/prebeam-ref.sock
    test -f "$d/mirror-id" || return 0
    expected=$(cat "$d/mirror-id")
    for i in $(seq 1 15); do
        test -S "$path" || return 0
        test ! -L "$path" || return 1
        test "$(stat -c '%d:%i:%u' "$path")" = "$expected" || return 0
        if ! awk -v wanted="$path" '$NF==wanted {live=1} END{exit !live}' /proc/net/unix; then
            test "$(stat -c '%d:%i:%u' "$path")" = "$expected" || return 0
            rm -f "$path"
            return
        fi
        sleep .2
    done
    return 1
}
restore() {
    rm -f "$d/armed" "$d/lease"
    [ ! -x "$d/mux" ] || "$d/mux" --stop-indicator || :
    if test ! -e "$d/bounced"; then return 0; fi
    if test -f "$d/restored" && healthy "$sw"; then return 0; fi
    if ! mkdir "$d/restoring" 2>/dev/null; then
        for i in $(seq 1 30); do if test -f "$d/restored" && healthy "$sw"; then return 0; fi; sleep 1; done
        return 1
    fi
    trap 'rmdir "$d/restoring" 2>/dev/null || :' EXIT
    stock_ok
    if test "$(exe wake)" = "$tw"; then DAEMON="$tw" "$wi" stop; else "$wi" stop; fi
    "$mi" stop; "$mi" start; "$wi" start
    wait_healthy "$sw"
    remove_stale_own_mirror
    touch "$d/restored"
}
case "$1" in
start)
    stock_ok; allowed; healthy "$sw"; test ! -e /run/libreecho-audio/prebeam-ref.sock
    test "$(stat -c '%u:%a' "$d")" = 0:700 && test ! -L "$d"
    test "$(sha256sum "$tw" | cut -d' ' -f1)" = 5d2d57408c8f55f28f319386acd33067f5aae5e1ee408c3dd1e2c265cc2dd8e0
    test "$(sha256sum "$d/mux" | cut -d' ' -f1)" = 0f112717d6a708d79c8a034403c4fed29e7e01c0744c6aa0c7d54f63707005cb
    test ! -e "$d/raw"; mkfifo -m 600 "$d/raw"
    touch "$d/heartbeat"
    setsid sh "$d/session.sh" watchdog </dev/null >"$d/guard.log" 2>&1 &
    trap restore EXIT
    touch "$d/bounced"
    "$wi" stop; "$mi" stop
    CAPTURE_MUX="$d/mux" "$mi" start
    DAEMON="$tw" "$wi" start
    wait_healthy "$tw"
    trap - EXIT;;
watchdog)
    trap '' HUP
    start=$(date +%s)
    while test -d "$d" && test ! -e "$d/restored"; do
        now=$(date +%s); heartbeat=$(stat -c %Y "$d/heartbeat" || echo 0)
        if ! allowed || test "$((now-start))" -gt 135 || test "$((now-heartbeat))" -gt 30; then restore; break; fi
        sleep 1
    done
    if test -d "$d"; then touch "$d/guard-finished"; fi;;
restore) restore;;
esac
