# Host targets. Building the optional feature filesystem requires Linux squashfs-tools.
PYTHON ?= python3
KERNEL_OUT ?= $(CURDIR)/build/linux
BASE := build/echo2-linux-base.cpio.gz
FEATURE := build/echo2-voice.squashfs
PLAYER := build/echo2-player
VOICE := dist/voice

.PHONY: base voice feature kernel boot-test feature-test

base: $(BASE)

$(PLAYER): src/player.c tools/build-player.py tools/player-image/Dockerfile tools/player-image/compile.sh
	$(PYTHON) tools/build-player.py --output "$@"

$(BASE): $(PLAYER) src/echo2-board rootfs/init rootfs/etc/inittab tools/build-rootfs.py licenses/tinyalsa.txt
	$(PYTHON) tools/build-rootfs.py --player "$(PLAYER)" --output "$@" --replace

voice:
	@if test -d "$(VOICE)"; then $(PYTHON) tools/build-voice.py --inspect; else $(PYTHON) tools/build-voice.py; fi

feature: voice
	$(PYTHON) tools/build-rootfs.py --voice-dir "$(VOICE)" --feature-output "$(FEATURE)" --replace

kernel:
	@test -n "$(KERNEL_SRC)" || { echo "Set KERNEL_SRC to the pinned board kernel checkout" >&2; exit 2; }
	bash tools/build-kernel.sh "$(KERNEL_SRC)" "$(KERNEL_OUT)"

boot-test: base
	@test -n "$(VM_KERNEL)" || { echo "Set VM_KERNEL to Alpine's ARMv7 vmlinuz-lts" >&2; exit 2; }
	$(PYTHON) tests/boot-rootfs.py "$(BASE)" --kernel "$(VM_KERNEL)" --expect-player

feature-test: base feature
	@test -n "$(VM_KERNEL)" && test -n "$(NETBOOT)" || { echo "Set VM_KERNEL and NETBOOT for the Alpine ARMv7 VM" >&2; exit 2; }
	$(PYTHON) tests/boot-rootfs.py "$(BASE)" --kernel "$(VM_KERNEL)" --expect-player --feature "$(FEATURE)" --netboot "$(NETBOOT)"
