#!/bin/sh
# This startup file belongs only to the virtual machine. It has no board devices.
set -eu
cd /etc/echo2-bench
while read -r module; do insmod "drivers/$module"; done < modules
ip link set lo up
ip link set eth0 up
ip addr add 10.0.2.15/24 dev eth0
# The forwarded socket can retain a source address outside the guest subnet.
# The return route goes to slirp; restrict=on denies guest-initiated LAN access.
ip route add default via 10.0.2.2 dev eth0
echo 'Echo 2 Linux: ARMv7 bench network ready'
if test -f network-check; then
    cat > /run/netcheck <<'EOF'
#!/bin/sh
IFS= read -r line
while IFS= read -r line && test "${#line}" -gt 1; do :; done
printf 'HTTP/1.1 200 OK\r\nContent-Length: 14\r\nConnection: close\r\n\r\necho2-audio-vm'
EOF
    chmod 700 /run/netcheck
    exec busybox nc -lk -p 8443 -e /run/netcheck
fi

mkdir -p /run/echo2
umask 077
echo2-sendspin-sink /run/echo2/sendspin.sock &
for i in 1 2 3 4 5; do test -S /run/echo2/sendspin.sock && break; sleep 1; done
echo2-sendspind --check --socket /run/echo2/sendspin.sock
# The SDK also logs provisioning material. Export only these fixed test events.
mkfifo /run/echo2/receiver-events
awk '
    /Processed new codec header: flac, 48000 Hz, 2 ch, 16-bit/ { print "bench: 48 kHz stereo FLAC"; fflush() }
    /Lost sync/ { print "bench: receiver lost sync"; fflush() }
    /Regained sync/ { print "bench: receiver regained sync"; fflush() }
' < /run/echo2/receiver-events &
# Docker publishes the unpaired listener on host loopback. QEMU's restricted
# network stops the guest from initiating connections to the home LAN.
echo2-sendspind --run --name 'Echo 2 Linux Bench' --socket /run/echo2/sendspin.sock \
    --control-socket /run/echo2/sendspin-control.sock --state-file /run/echo2/bench-pairings \
    --unpaired-access on --port 8928 >/run/echo2/receiver-events 2>&1 &
receiver=$!
for i in 1 2 3 4 5; do
    kill -0 "$receiver" || exit 1
    test -S /run/echo2/sendspin-control.sock && break
    sleep 1
done
test -S /run/echo2/sendspin-control.sock
exec echo2-intercom --listen :8443 --backend probe --allow-calls \
    --cert /etc/echo2-bench/private/server.crt --key /etc/echo2-bench/private/server.key \
    --password-file /etc/echo2-bench/private/password
