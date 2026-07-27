#!/usr/bin/env python3
"""Does this DLL's decoder apply FEC, or does it trust its input?

The hypothesis: this is WAVE 7000 PTT, which sits BEHIND the FNE. The FNE would
already have done error correction, so the softclient would receive clean,
post-FEC info bits and have no need for an FEC layer or a bad-frame input.

Evidence for that already: the payloads are info-bit-sized (7 bytes = 49 bits,
no room for the ~23 FEC bits of a rate-33 frame). Evidence against: the decoder
logs FEC error counters ("FEC errors: E0(%u), E1(%u)").

Decisive test: flip one bit at a time and see whether the decoded audio changes.
  * every flipped bit changes the output  -> no correction; the DLL trusts input
  * some flips are absorbed                -> something is correcting them

Each trial uses a FRESH decoder instance, because the decoder is stateful and a
reused one would carry contamination from the previous trial.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import default_data, WaveShim, DECODER, TDMA_AMBE2

CAPS = os.environ.get("CAPS") or default_data("ambe-samples")
R34_BIT_ORDER = [
    0, 18, 36, 1, 19, 37, 2, 20, 38, 3, 21, 39, 4, 22, 40, 5, 23, 41, 6, 24, 42,
    7, 25, 43, 8, 26, 44, 9, 27, 45, 10, 28, 46, 11, 29, 47, 12, 30, 48, 13, 31,
    14, 32, 15, 33, 16, 34, 17, 35,
]


def deinterleave(f):
    nat = [0]*49
    for j, dst in enumerate(R34_BIT_ORDER):
        nat[dst] = (f[j // 8] >> (7 - (j % 8))) & 1
    out = bytearray(7)
    for k, b in enumerate(nat):
        out[k // 8] |= b << (7 - (k % 8))
    return bytes(out)


def flip(frame, bit):
    b = bytearray(frame)
    b[bit // 8] ^= 1 << (7 - (bit % 8))
    return bytes(b)


def main():
    import glob
    cid = sorted(glob.glob(os.path.join(CAPS, "*.ambe")))[0]
    ambe = open(cid, "rb").read()
    frames = [deinterleave(ambe[i*7:(i+1)*7]) for i in range(len(ambe)//7)]
    # pick a voiced-looking pair well into the call
    base_pair = frames[100:102]

    s = WaveShim()
    s.hello()

    def decode(pair):
        h = s.open(DECODER, TDMA_AMBE2)
        out = s.process(h, b"".join(u + b"\x00" for u in pair))
        s.close_handle(h)
        return np.frombuffer(out, dtype="<i2").astype(np.int64)

    ref = decode(base_pair)
    print(f"baseline decode: {len(ref)} samples, rms {np.sqrt((ref**2).mean()):.0f}\n")

    changed, same = [], []
    for bit in range(56):                      # 49 payload + 7 pad bits
        trial = [flip(base_pair[0], bit), base_pair[1]]
        got = decode(trial)
        n = min(len(got), len(ref))
        d = int(np.abs(got[:n] - ref[:n]).max())
        (changed if d > 0 else same).append((bit, d))

    payload_changed = [b for b, _ in changed if b < 49]
    payload_same = [b for b, _ in same if b < 49]
    pad_changed = [b for b, _ in changed if b >= 49]

    print(f"payload bits (0-48): {len(payload_changed)} of 49 changed the output")
    if payload_same:
        print(f"  bits with NO effect: {payload_same}")
    print(f"pad bits (49-55): {len(pad_changed)} of 7 changed the output")
    print()

    if len(payload_changed) >= 46:
        print("=> NO error correction. Nearly every payload bit reaches the")
        print("   synthesis directly, so the DLL TRUSTS its input: a corrupted")
        print("   frame produces corrupted audio, silently.")
        print("   Consistent with a softclient sitting behind the FNE, which")
        print("   would have already corrected errors upstream.")
    elif len(payload_changed) < 30:
        print("=> Something IS correcting bits: a substantial share of single-bit")
        print("   flips are absorbed with no change to the audio.")
    else:
        print("=> Mixed. Some bits are absorbed; needs a closer look at which.")
    if pad_changed:
        print(f"\n   NOTE: {len(pad_changed)} pad bit(s) changed the output — the")
        print("   8th byte of each unit is NOT ignored padding after all.")
    else:
        print("\n   The 7 pad bits are ignored, as expected.")
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
