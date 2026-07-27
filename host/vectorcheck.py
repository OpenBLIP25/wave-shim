#!/usr/bin/env python3
"""Compare the DLL's ENCODER against reference bitstreams for the same PCM.

This closes the encoder-side gap: until now the encoder was only ever checked by
feeding its output back into the DLL's own decoder, which cannot detect a
systematic disagreement with the reference.

It also answers a question worth settling with measurement rather than
assumption: is this DLL bit-identical to the physical AMBE-3000 chip?

Reference sets (source PCM + reference output, same frame count):
  omap_handoff_anchors_2026-06-27/chip_io/encode/*.chip34.bit
      physical chip, rate 34, 7 bytes/frame — the same shape the DLL emits

Two unknowns have to be swept, not assumed:
  * frame alignment — the DLL encoder has a measured 1-frame delay, and the
    reference may have its own
  * bit order — the DLL emits NATURAL info-vector order, while off-air/reference
    bitstreams are often the r34 column interleave (this exact trap already bit
    us on the decode side)
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import WaveShim, ShimError, ShimDied, ENCODER, TDMA_AMBE2

CHIP = os.environ.get(
    "CHIP", "vectors/chip_io/encode")

R34_BIT_ORDER = [
    0, 18, 36, 1, 19, 37, 2, 20, 38, 3, 21, 39, 4, 22, 40, 5, 23, 41, 6, 24, 42,
    7, 25, 43, 8, 26, 44, 9, 27, 45, 10, 28, 46, 11, 29, 47, 12, 30, 48, 13, 31,
    14, 32, 15, 33, 16, 34, 17, 35,
]


def interleave_r34(nat7):
    """natural info-vector order -> off-air r34 column interleave."""
    nat = [(nat7[k // 8] >> (7 - (k % 8))) & 1 for k in range(49)]
    out = bytearray(7)
    for j, src in enumerate(R34_BIT_ORDER):
        out[j // 8] |= nat[src] << (7 - (j % 8))
    return bytes(out)


def bits(b):
    return np.unpackbits(np.frombuffer(b, dtype=np.uint8))


def compare(a_frames, b_frames):
    """frame-exact % and bit-agreement % over the overlap."""
    n = min(len(a_frames), len(b_frames))
    if n == 0: return 0.0, 0.0, 0
    exact = sum(1 for i in range(n) if a_frames[i] == b_frames[i])
    ab = bits(b"".join(a_frames[:n]))
    bb = bits(b"".join(b_frames[:n]))
    # only the first 49 bits of each 56-bit frame carry payload
    mask = np.tile(np.array([1]*49 + [0]*7, dtype=bool), n)
    agree = float((ab[mask] == bb[mask]).mean()*100)
    return exact*100.0/n, agree, n


def decode_section(s):
    """Feed reference r33 bitstreams to the DLL decoder and compare its PCM to
    the physical chip's decode of the same frames.

    Per the capture manifest, the chip's decode of these vectors is
    byte-identical to DVSI's published reference decode — so this is effectively
    a comparison against DVSI itself, not just against one chip."""
    import subprocess, tempfile
    DEC = os.environ.get(
        "DEC", "vectors/chip_io/decode")
    CONV = os.environ.get(
        "CONV", "r33_to_info_r34")
    from waveshim import DECODER
    stems = sorted({f[:-len(".bit")] for f in os.listdir(DEC) if f.endswith(".bit")})
    print("\n" + "="*78)
    print("DECODER vs the physical chip (chip decode == DVSI published reference)\n")
    rows = []
    for stem in stems:
        bit = os.path.join(DEC, stem + ".bit")
        ref_pcm = np.frombuffer(open(os.path.join(DEC, stem + ".chip.pcm"), "rb").read(),
                                dtype="<i2").astype(np.int64)
        with tempfile.NamedTemporaryFile(suffix=".r34", delete=False) as tf:
            out_path = tf.name
        r = subprocess.run([CONV, bit, out_path], capture_output=True)
        if r.returncode != 0:
            print(f"  {stem}: r33->r34 conversion failed"); continue
        r34 = open(out_path, "rb").read()
        os.unlink(out_path)
        frames = [r34[i*7:(i+1)*7] for i in range(len(r34)//7)]

        h = s.open(DECODER, TDMA_AMBE2)
        got = b""
        try:
            for i in range(0, len(frames) - 1, 2):
                got += s.process(h, b"".join(u + b"\x00" for u in frames[i:i+2]))
        except (ShimError, ShimDied) as e:
            print(f"  {stem}: decode failed — {e}"); continue
        s.close_handle(h)
        dll = np.frombuffer(got, dtype="<i2").astype(np.int64)

        n = min(len(dll), len(ref_pcm))
        d = dll[:n] - ref_pcm[:n]
        exact = float((d == 0).mean()*100)
        maxd = int(np.abs(d).max()) if n else 0
        # active-sample view: silence is easy and inflates the number
        act = np.abs(ref_pcm[:n]) > 200
        exact_act = float((d[act] == 0).mean()*100) if act.any() else 0.0
        rows.append((stem, exact, exact_act, maxd))
        print(f"  {stem:10s} {n:6d} samples   sample-exact {exact:6.2f}%   "
              f"active-only {exact_act:6.2f}%   max diff {maxd}")
    if rows:
        import statistics as st
        print(f"\n  mean sample-exact {st.mean(r[1] for r in rows):.2f}%  "
              f"(active-only {st.mean(r[2] for r in rows):.2f}%)")
        if st.mean(r[1] for r in rows) > 99.9:
            print("\n  => the DLL decoder IS the reference decoder, sample for sample.")
        elif st.mean(r[1] for r in rows) > 50:
            print("\n  => close but not identical: same algorithm, small numeric"
                  " differences.")
        else:
            print("\n  => the DLL decoder is NOT sample-identical to the chip/DVSI"
                  " reference.")
    return rows


def ref_encode_section(s, src_dir, bit_dir, label, stems, cap=600):
    """DLL encoder vs a DVSI reference encode of the SAME source PCM.

    Pairing matters and is easy to get wrong: within each tree the true source
    PCM sits at the TOP level, while `rNN/*.pcm` is the encode-decode OUTPUT at
    rate NN. Pairing rNN bits with r33's pcm compares the encode of decoded
    audio against the encode of the original — which produces a meaningless
    near-zero score. (Learned the hard way; the first run of this test did
    exactly that.)
    """
    print("\n" + "="*78)
    print(f"ENCODER vs {label}\n")
    rows = []
    for stem in stems:
        sp = os.path.join(src_dir, stem + ".pcm")
        bp = os.path.join(bit_dir, stem + ".bit")
        if not (os.path.exists(sp) and os.path.exists(bp)):
            continue
        pcm = open(sp, "rb").read()
        ref = open(bp, "rb").read()
        in_frames = [pcm[i*320:(i+1)*320] for i in range(len(pcm)//320)][:cap]
        ref_frames = [ref[i*7:(i+1)*7] for i in range(len(ref)//7)]
        if not in_frames or not ref_frames: continue

        h = s.open(ENCODER, TDMA_AMBE2)
        got = []
        try:
            for f in in_frames:
                got.append(s.process(h, f))
        except (ShimError, ShimDied) as e:
            print(f"  {stem}: encode failed — {e}"); continue
        s.close_handle(h)

        best = (0.0, 0.0, None, None)
        for order, conv in (("natural", lambda x: x),
                            ("r34-interleaved", interleave_r34)):
            cf = [conv(g) for g in got]
            for shift in range(0, 3):
                ex, ag, n = compare(cf[shift:], ref_frames)
                if ex > best[0] or (ex == best[0] and ag > best[1]):
                    best = (ex, ag, order, shift)
        rows.append((stem, best))
        print(f"  {stem:10s} {len(in_frames):4d}fr   best {best[2]:15s} shift {best[3]}"
              f"   {best[0]:5.1f}% frame-exact   {best[1]:5.1f}% bit-agreement")
    if rows:
        import statistics as st
        print(f"\n  mean frame-exact {st.mean(r[1][0] for r in rows):.1f}%   "
              f"mean bit-agreement {st.mean(r[1][1] for r in rows):.1f}%")
    return rows


def main():
    stems = sorted({f[:-len(".chip34.bit")] for f in os.listdir(CHIP)
                    if f.endswith(".chip34.bit")})
    print(f"reference: {CHIP}")
    print(f"stems: {', '.join(stems)}\n")

    s = WaveShim()
    s.hello()
    overall = []

    for stem in stems:
        pcm = open(os.path.join(CHIP, stem + ".pcm"), "rb").read()
        ref = open(os.path.join(CHIP, stem + ".chip34.bit"), "rb").read()
        in_frames = [pcm[i*320:(i+1)*320] for i in range(len(pcm)//320)]
        ref_frames = [ref[i*7:(i+1)*7] for i in range(len(ref)//7)]

        h = s.open(ENCODER, TDMA_AMBE2)
        got = []
        try:
            for f in in_frames:
                got.append(s.process(h, f))
        except (ShimError, ShimDied) as e:
            print(f"  {stem}: encode failed — {e}"); continue
        s.close_handle(h)

        print(f"  {stem}  ({len(in_frames)} frames)")
        best = (0.0, 0.0, None, None)
        for order, conv in (("natural", lambda x: x),
                            ("r34-interleaved", interleave_r34)):
            conv_frames = [conv(g) for g in got]
            for shift in range(0, 4):
                ex, ag, n = compare(conv_frames[shift:], ref_frames)
                if ex > best[0] or (ex == best[0] and ag > best[1]):
                    best = (ex, ag, order, shift)
        # show the grid so a near-miss is visible, not just the winner
        for order, conv in (("natural", lambda x: x),
                            ("r34-interleaved", interleave_r34)):
            conv_frames = [conv(g) for g in got]
            cells = []
            for shift in range(0, 4):
                ex, ag, n = compare(conv_frames[shift:], ref_frames)
                cells.append(f"shift{shift}: {ex:5.1f}% frames / {ag:5.1f}% bits")
            print(f"      {order:16s} " + "   ".join(cells))
        overall.append((stem, best))
        print(f"      best: {best[2]} shift {best[3]} -> "
              f"{best[0]:.1f}% frame-exact, {best[1]:.1f}% bit-agreement\n")

    ref_encode_section(s, "/mnt/c/temp/tv-std-src", "/mnt/c/temp/tv-std-r34",
                       "DVSI reference, STD codec config (tv-std)",
                       ["clean", "dam", "noisy"])
    ref_encode_section(s, "/mnt/c/temp/tv-rc-src", "/mnt/c/temp/tv-rc-r34",
                       "DVSI reference, RC codec config (tv-rc)",
                       ["clean", "dam", "alert"])
    decode_section(s)
    s.close()

    print("="*78)
    if overall:
        bf = max(b[0] for _, b in overall)
        bb = max(b[1] for _, b in overall)
        print(f"best frame-exact across all stems: {bf:.1f}%")
        print(f"best bit-agreement across all stems: {bb:.1f}%")
        print("\nverdict:")
        if bf > 99:
            print("  The DLL encoder reproduces the physical chip's bitstream.")
        elif bb > 90:
            print("  Close but NOT bit-identical: the DLL and the chip mostly")
            print("  agree bit-for-bit but differ on a minority of frames.")
        elif bb > 60:
            print("  The DLL and the chip AGREE PARTIALLY — well above the ~50%")
            print("  a coin flip would give, so they share structure, but they")
            print("  are decidedly NOT the same encoder.")
        else:
            print("  The DLL encoder is NOT the chip's encoder. Bit agreement is")
            print("  at or near chance, i.e. these are different implementations")
            print("  of the same format, not the same code.")
        print("\n  (Chance level is ~50% bit-agreement for unrelated bitstreams.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
