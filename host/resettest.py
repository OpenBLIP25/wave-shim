#!/usr/bin/env python3
"""Is RESET per-instance, and is it equivalent to a fresh instance?

The real-world pattern: a console resets a vocoder between transmissions. Two
things must hold for that to be safe.

  1. RESET must touch ONLY its own instance — resetting the vocoder for one
     talkgroup must not disturb another that is mid-transmission.
  2. RESET must actually clear the state, i.e. an instance that has been reset
     must behave like a newly constructed one, not like one carrying residue
     from the previous transmission.

Both are checked bit-exactly, plus repeated reset cycles to mimic many
back-to-back transmissions.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import WaveShim, ENCODER, DECODER, TDMA_AMBE2

PCM = os.environ.get("PCM", "vectors/clean.pcm")
N = int(os.environ.get("N", "40"))

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(name)


def main():
    raw = open(PCM, "rb").read()
    frames = [raw[i*320:(i+1)*320] for i in range(len(raw)//320)]
    txA = frames[100:100+N]          # "transmission" A
    txB = frames[900:900+N]          # a different talkgroup's audio

    s = WaveShim()
    s.hello()

    # --- reference: a FRESH instance encoding txB ------------------------
    h = s.open(ENCODER, TDMA_AMBE2)
    fresh_B = [s.process(h, f) for f in txB]
    s.close_handle(h)

    # --- 1. does RESET make an instance behave like a fresh one? ---------
    h = s.open(ENCODER, TDMA_AMBE2)
    for f in txA:                     # previous transmission
        s.process(h, f)
    s.reset(h)                        # between transmissions
    after_reset_B = [s.process(h, f) for f in txB]
    s.close_handle(h)
    check("after RESET an instance matches a freshly opened one, bit for bit",
          after_reset_B == fresh_B,
          "" if after_reset_B == fresh_B else
          f"{sum(1 for a,b in zip(after_reset_B, fresh_B) if a!=b)}/{N} frames differ")

    # --- 2. is RESET confined to its own instance? -----------------------
    # Instance X runs straight through. Instance Y is reset repeatedly in the
    # middle of X's run. X must be unaffected.
    hx = s.open(ENCODER, TDMA_AMBE2)
    solo_X = [s.process(hx, f) for f in txA]
    s.close_handle(hx)

    hx = s.open(ENCODER, TDMA_AMBE2)
    hy = s.open(ENCODER, TDMA_AMBE2)
    inter_X = []
    for i, f in enumerate(txA):
        inter_X.append(s.process(hx, f))
        s.process(hy, txB[i])
        if i % 5 == 0:
            s.reset(hy)               # hammer RESET on the neighbour
    s.close_handle(hx); s.close_handle(hy)
    check("resetting one instance does NOT disturb another mid-transmission",
          inter_X == solo_X,
          "" if inter_X == solo_X else
          f"{sum(1 for a,b in zip(inter_X, solo_X) if a!=b)}/{N} frames differ")

    # --- 3. many back-to-back transmissions on one instance --------------
    h = s.open(ENCODER, TDMA_AMBE2)
    outs = []
    for tx in range(25):
        s.reset(h)
        outs.append([s.process(h, f) for f in txB[:10]])
    s.close_handle(h)
    all_same = all(o == outs[0] for o in outs)
    check("25 reset-then-transmit cycles all produce identical output",
          all_same,
          "" if all_same else "output drifted across transmissions")
    check("and that output matches a fresh instance", outs[0] == fresh_B[:10])

    # --- 4. same for the decoder ----------------------------------------
    unit = lambda b: b + b"\x00"
    pair = b"".join(unit(bytes([0x11 + i] + [0]*6)) for i in range(2))
    hd = s.open(DECODER, TDMA_AMBE2)
    fresh_d = s.process(hd, pair)
    s.close_handle(hd)

    hd = s.open(DECODER, TDMA_AMBE2)
    for _ in range(10):
        s.process(hd, pair)
    s.reset(hd)
    after_d = s.process(hd, pair)
    s.close_handle(hd)
    check("decoder RESET also returns it to the fresh state",
          after_d == fresh_d,
          "" if after_d == fresh_d else "decoder retained state across RESET")

    s.close()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: {', '.join(FAILS)}")
        return 1
    print("RESET is per-instance and equivalent to a fresh instance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
