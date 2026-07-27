# Builds a PE32 (i386) exe + DLL. Three host environments are supported:
#
#   Linux (cross)   i686-w64-mingw32-gcc; run the result under Wine, or via
#                   WSL interop if this is WSL
#   WSL             as above, but PE binaries execute natively through
#                   binfmt_misc — no Wine. They must sit on a Windows-visible
#                   path, so `make stage` copies them to $(WINSTAGE).
#   native Windows  MSYS2/MinGW; `make CC=gcc` (a 32-bit MinGW gcc). No staging.
#
# `?=` will not work for CC: make predefines it, so only replace it when the
# value is still make's own default and leave any user/environment value alone.
ifeq ($(origin CC),default)
CC      := i686-w64-mingw32-gcc
endif
CFLAGS  ?= -O2 -Wall -Wextra -std=gnu99
BUILD   := build

# Staging is a WSL-only step; elsewhere the binaries run from $(BUILD) as built.
IS_WSL := $(shell grep -qi microsoft /proc/version 2>/dev/null && echo 1)

all: $(BUILD)/wave-shim.exe $(BUILD)/stub.dll

$(BUILD)/wave-shim.exe: shim.c binding.h protocol.h | $(BUILD)
	$(CC) $(CFLAGS) -o $@ shim.c

$(BUILD)/stub.dll: stub/stub.c | $(BUILD)
	$(CC) $(CFLAGS) -shared -o $@ stub/stub.c -Wl,--kill-at

$(BUILD):
	mkdir -p $(BUILD)

# Under WSL the binaries must live on a Windows-visible path — \\wsl.localhost\...
# is not usable as a Windows process's working directory. Everywhere else this
# is a no-op and the host scripts launch straight out of $(BUILD).
# Set WINSTAGE explicitly to force staging on any platform.
WINSTAGE ?= $(if $(IS_WSL),/mnt/c/temp/wave-shim,)

stage: all
	@if [ -n "$(WINSTAGE)" ]; then \
	   mkdir -p "$(WINSTAGE)" && \
	   cp $(BUILD)/wave-shim.exe $(BUILD)/stub.dll "$(WINSTAGE)/" && \
	   echo "staged -> $(WINSTAGE)"; \
	 else \
	   echo "no staging needed; running from $(BUILD)/"; \
	 fi

test: stage
	python3 host/selftest.py

# Real-DLL suites. Supply your own licensed W7K_UA_SDK.dll (from the WAVE 7000
# PTT softclient); it is not redistributed here. Stage it alongside the exe:
#   cp /path/to/W7K_UA_SDK.dll $(WINSTAGE)/
realtest: stage
	python3 host/realsmoke.py
	python3 host/roundtrip.py
	python3 host/decoderdelay.py
	python3 host/delay_resolve.py

# ---------------------------------------------------------------------------
# Test data staging. Reference vectors live on a Windows network share, which is
# NOT mounted in WSL (network drives do not automount) — so it is copied to
# C:\temp via Windows. cmd.exe cannot handle the space in "DVSI Vectors";
# use PowerShell for those. Run this once before otatest/vectortest.
#
# Point these at your own copies; the vectors are not in this repo:
#   make stage-data DATA_SHARE='\\share\path' DVSI_VECTORS='\\share\path\DVSI Vectors'
# ---------------------------------------------------------------------------
PS := /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe

# Windows-side (not WSL) paths to your own copies of the test data.
DATA_SHARE   ?=
DVSI_VECTORS ?=
AMBE_SAMPLES ?= $(DATA_SHARE)\ambe-samples
DVSI          = $(DVSI_VECTORS)

stage-data:
	@test -n "$(IS_WSL)"       || { echo "stage-data is WSL-only; elsewhere copy the vectors yourself and point CAPS/TV_*/CHIP/DEC at them"; exit 1; }
	@test -n "$(DATA_SHARE)"   || { echo "set DATA_SHARE=<windows path containing ambe-samples>"; exit 1; }
	@test -n "$(DVSI_VECTORS)" || { echo "set DVSI_VECTORS=<windows path to the DVSI vector trees>"; exit 1; }
	/mnt/c/Windows/System32/cmd.exe /c 'robocopy $(AMBE_SAMPLES) C:\temp\ambe-samples /E /NFL /NDL /NJH /NP' || true
	$(PS) -NoProfile -Command "New-Item -ItemType Directory -Force -Path C:\temp\tv-std-src,C:\temp\tv-std-r34,C:\temp\tv-rc-src,C:\temp\tv-rc-r34 | Out-Null; 	  Copy-Item '$(DVSI)\tv-std\tv\*.pcm' C:\temp\tv-std-src\ ; 	  Copy-Item '$(DVSI)\tv-std\tv\r34\*.bit' C:\temp\tv-std-r34\ ; 	  Copy-Item '$(DVSI)\tv-rc\*.pcm' C:\temp\tv-rc-src\ ; 	  Copy-Item '$(DVSI)\tv-rc\r34\*.bit' C:\temp\tv-rc-r34\ "
	@echo "staged: ambe-samples, tv-std-{src,r34}, tv-rc-{src,r34}"

# Off-air validation. Needs `make stage-data` first.
otatest: stage
	python3 host/otacheck.py

# Reference-vector comparison: DLL encoder vs physical-chip bitstream, and DLL
# decoder vs the chip/DVSI reference decode. Needs `make stage-data` first.
# NOTE: pair each tree's TOP-LEVEL pcm with its own rNN bits. rNN/*.pcm is
# encode-DECODE output, not source, and pairing it scores near zero.
vectortest: stage
	python3 host/vectorcheck.py

# Behavioural probes: FEC presence, multi-instance isolation.
probes: stage
	python3 host/fectest.py
	python3 host/isolation.py
	python3 host/resettest.py

soak: stage
	NFRAMES=20000 CYCLES=200 python3 host/soak.py

checkall: test realtest otatest vectortest probes soak

clean:
	rm -rf $(BUILD)

.PHONY: all stage stage-data test realtest otatest vectortest probes soak checkall clean
