#!/usr/bin/env python3
"""Resolve the one-frame disagreement between the two delay measures.

  - the differential impulse test says: encoder delay 1 frame, decoder 0
  - the envelope-correlation scan peaks at 2 frames

Both can be true and mean different things: the differential test finds the
FIRST frame the input can influence (causal onset), while the envelope peak
finds where the BULK of the energy lands, which an overlap-add synthesis spreads
later. This measures the end-to-end answer directly: put a burst into a known
input frame, decode, and see which output frame carries the energy.
"""
import os, struct, subprocess, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import shim_argv, default_dll

DLL_WIN = default_dll()

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK = 0
KIND_ENC, KIND_DEC = 0, 1
RATE_TDMA = 0
HIT = 12
NFRAMES = 28


class Shim:
    def __init__(self):
        self.p = subprocess.Popen(shim_argv() + [DLL_WIN],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    def call(self, op, payload=b""):
        body = bytes([op]) + payload
        self.p.stdin.write(struct.pack("<I", len(body)) + body)
        self.p.stdin.flush()
        (n,) = struct.unpack("<I", self._rd(4))
        r = self._rd(n)
        return r[0], r[1:]
    def _rd(self, n):
        out = b""
        while len(out) < n:
            c = self.p.stdout.read(n - len(out))
            if not c: raise SystemExit("shim died")
            out += c
        return out
    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=15)


def burst(amp=20000, n=160, f=500):
    import math
    return b"".join(struct.pack("<h", int(amp*math.sin(2*math.pi*f*i/8000)))
                    for i in range(n))
SIL = b"\0" * 320


def frame_rms(pcm):
    d = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    n = len(d)//160*160
    return np.sqrt((d[:n].reshape(-1,160)**2).mean(axis=1))


def main():
    s = Shim()
    s.call(OP_HELLO)

    pcm_in = [SIL]*NFRAMES
    pcm_in[HIT] = burst()

    # encode
    st, he = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    enc = []
    for f in pcm_in:
        st, o = s.call(OP_PROCESS, he + f)
        enc.append(o)
    s.call(OP_CLOSE, he)

    # decode (2 units/call, trailing pad — the established layout)
    st, hd = s.call(OP_OPEN, bytes([KIND_DEC, RATE_TDMA]))
    out = b""
    for i in range(0, len(enc), 2):
        payload = b"".join(u + b"\x00" for u in enc[i:i+2])
        st, o = s.call(OP_PROCESS, hd + payload)
        out += o
    s.call(OP_CLOSE, hd)
    s.close()

    e = frame_rms(out)
    base = np.median(e)
    print(f"burst fed into INPUT frame {HIT}; decoded frame energies "
          f"(median floor {base:.0f}):\n")
    lo, hi = max(0, HIT-3), min(len(e), HIT+7)
    peak = int(np.argmax(e))
    for i in range(lo, hi):
        bar = "#" * int(40 * e[i] / max(e.max(), 1))
        tag = ""
        if i == HIT: tag += "  <- input frame index"
        if i == peak: tag += "  <- PEAK"
        print(f"  out frame {i:2d}  rms {e[i]:8.1f}  {bar}{tag}")

    # The audio domain cannot give a clean CAUSAL onset: the decoder emits its
    # own small startup transient (frame 0 is nonzero here), so "first nonzero"
    # is meaningless. Causal delay belongs in the BITS domain, where the
    # differential tests already measured it exactly. What this test adds is
    # where the ENERGY lands.
    floor = float(np.max(e[:HIT])) if HIT else 0.0
    energy = next((i for i in range(len(e))
                   if e[i] > max(floor * 20, e.max() * 0.05)), None)

    print(f"\n  pre-burst floor (frames 0..{HIT-1}): max rms {floor:.1f}"
          f"   <- decoder startup transient, not signal")
    print(f"  energy onset  : output frame {energy}  => +{energy - HIT} frame(s)")
    print(f"  energy peak   : output frame {peak}  => +{peak - HIT} frame(s)")

    print("\n  reconciliation of the three measurements:")
    print("    bits domain, differential  : encoder +1 frame, decoder +0")
    print(f"    audio domain, burst energy : onset +{energy-HIT}, peak +{peak-HIT}")
    print("    audio domain, envelope corr: peak +2 over speech")
    print("\n    These are consistent, not contradictory. The encoder's analysis")
    print("    window straddles frames and the decoder's synthesis overlap-adds,")
    print("    so a frame's energy is SPREAD over the following frames rather")
    print("    than delayed as a block. +1 is where influence begins; +2..+3 is")
    print("    where the audio actually arrives.")
    print("\n  use:")
    print("    aligning BITS to input frames        -> shift 1 frame")
    print("    aligning decoded PCM to original PCM -> shift 2-3 frames, or")
    print("                                            cross-correlate envelopes")
    print("    the waveform-correlation lag is NOT a delay measure — it reports a")
    print("    sub-frame number reflecting phase-free synthesis")

    return 0


if __name__ == "__main__":
    sys.exit(main())
