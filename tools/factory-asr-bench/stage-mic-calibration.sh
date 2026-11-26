#!/bin/sh
# Copy only the seven mic coefficients into the private, per-session DSP directory.
set -eu
src=$1
dest=$2
for i in 0 1 2 3 4 5 6; do
    input="$src/miccal.$i/value"
    value=$(tr -d '\000' < "$input")
    case "$value" in ''|*[!0-9]*) exit 76 ;; esac
    test "${#value}" -le 5 && test "$value" -gt 0 && test "$value" -le 65535 || exit 76
    printf '%s' "$value" > "$dest/miccal.$i"
    chmod 0444 "$dest/miccal.$i"
done
