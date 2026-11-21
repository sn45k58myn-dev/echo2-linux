#!/usr/bin/env bash
# Build the Radar/Puffin board kernel and separate DTB from a local Linux mirror.
set -euo pipefail
if [[ $# -ne 2 ]]; then
    echo 'Usage: bash tools/build-kernel.sh PATH-TO-KERNEL-SOURCE OUTPUT-DIRECTORY' >&2
    exit 2
fi
source_dir=$(cd -- "$1" && pwd -P)
pin=5bc811d3cd7091e8d70f9faa0bc12c5cf4e77ae0
export GIT_OPTIONAL_LOCKS=0
[[ $(git -C "$source_dir" rev-parse HEAD) == "$pin" &&
   $(git -C "$source_dir" rev-parse 'HEAD^{tree}') == a00c937c9ad7968de46ec5b8d08857b0c89a3fb7 &&
   -z $(git -C "$source_dir" status --porcelain) ]] || {
    echo "Expected a clean checkout at $pin" >&2
    exit 1
}
mkdir -p -- "$2"
out=$(cd -- "$2" && pwd -P)
if [[ "$out/" == "$source_dir/"* ]]; then
    echo 'Choose a separate kernel output directory.' >&2
    exit 1
fi
jobs=${JOBS:-4}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer.' >&2; exit 2; }
export ARCH=arm CROSS_COMPILE=${CROSS_COMPILE:-arm-linux-gnueabihf-}
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=echo2-linux KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP
KBUILD_BUILD_TIMESTAMP=$(git -C "$source_dir" show -s --format=%cD "$pin")
make_kernel=(make -C "$source_dir" O="$out" LD="${CROSS_COMPILE}ld.bfd")
"${make_kernel[@]}" mt8163_arm32_defconfig
"$source_dir/scripts/config" --file "$out/.config" --disable LIBREECHO_DEV_RECOVERY_MARKER
"${make_kernel[@]}" olddefconfig
for symbol in BLK_DEV_INITRD RD_GZIP DEVTMPFS BINFMT_ELF SND_SOC_MT8163_RADAR_PUFFIN; do
    grep -qx "CONFIG_$symbol=y" "$out/.config"
done
"${make_kernel[@]}" -j"$jobs" zImage libreecho-radar-puffin.dtb
(cd "$out/arch/arm/boot" && sha256sum zImage dts/libreecho-radar-puffin.dtb)
