CC      := i686-w64-mingw32-gcc
CFLAGS  := -O2 -Wall -Wextra -std=gnu99
BUILD   := build

all: $(BUILD)/wave-shim.exe $(BUILD)/stub.dll

$(BUILD)/wave-shim.exe: shim.c binding.h protocol.h | $(BUILD)
	$(CC) $(CFLAGS) -o $@ shim.c

$(BUILD)/stub.dll: stub/stub.c | $(BUILD)
	$(CC) $(CFLAGS) -shared -o $@ stub/stub.c -Wl,--kill-at

$(BUILD):
	mkdir -p $(BUILD)

# Stage into a Windows-visible directory and run natively through WSL interop.
# No Wine involved: WSL2 executes PE binaries directly.
WINSTAGE ?= /mnt/c/temp/wave-shim

stage: all
	mkdir -p $(WINSTAGE)
	cp $(BUILD)/wave-shim.exe $(BUILD)/stub.dll $(WINSTAGE)/

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
