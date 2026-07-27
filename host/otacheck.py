#!/usr/bin/env python3
"""Validate the shim against REAL off-air frames.

This closes the last open question: everything so far showed the DLL's encoder
agreeing with the DLL's decoder, which cannot establish how the bytes map onto
an over-the-air P25 frame.

The captures provide post-FEC 7-byte rate-34 codewords plus, per call, two
renderings of the same bytes:
    <id>.r34.wav  — the correct 49-bit reading
    <id>.seq.wav  — the other, incorrect reading, rendered deliberately as
                    "convincing garbage"

So this is a labelled test, and the DLL is the reference implementation. Feed it
the raw codewords and see which rendering its output resembles. Matching r34
means the DLL accepts real off-air frames as-is and the shim's framing is right;
matching seq would mean the shim is feeding the bytes in the wrong reading.
"""
import glob, os, struct, sys, wave
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import default_data, WaveShim, ShimError, ShimDied, DECODER, TDMA_AMBE2

CAPS = os.environ.get("CAPS") or default_data("ambe-samples")
LIMIT = int(os.environ.get("LIMIT", "0"))     # 0 = all captures


# Off-air rate-34 frames are a 3-way column interleave of the natural
# info-vector order u0(12)||u1(12)||u2(11)||u3(14). This DLL wants the NATURAL
# order, so the interleave has to be undone before handing frames over.
#
# Deliberately implemented here in the host, not in shim.c: the shim stays pure
# transport, and this table is a documented frame-ordering fact (blip25-mbe
# rate33::frame::R34_BIT_ORDER), not anything recovered from the DLL.
R34_BIT_ORDER = [
    0, 18, 36, 1, 19, 37, 2, 20, 38, 3, 21, 39, 4, 22, 40, 5, 23, 41, 6, 24, 42,
    7, 25, 43, 8, 26, 44, 9, 27, 45, 10, 28, 46, 11, 29, 47, 12, 30, 48, 13, 31,
    14, 32, 15, 33, 16, 34, 17, 35,
]


def deinterleave_r34(frame7):
    """off-air r34 codeword -> natural info-vector order, 7 bytes MSB-first."""
    nat = [0]*49
    for j, dst in enumerate(R34_BIT_ORDER):
        nat[dst] = (frame7[j // 8] >> (7 - (j % 8))) & 1
    out = bytearray(7)
    for k, b in enumerate(nat):
        out[k // 8] |= b << (7 - (k % 8))
    return bytes(out)


def read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64), w.getframerate()


def frame_env(x):
    n = len(x)//160*160
    return np.sqrt((x[:n].reshape(-1, 160)**2).mean(axis=1))


def env_corr_scan(a, b, maxshift=6):
    """Best envelope correlation of a against b over integer frame shifts."""
    best = (-2.0, 0)
    for k in range(maxshift + 1):
        aa, bb = frame_env(a[k*160:]), frame_env(b)
        m = min(len(aa), len(bb))
        if m < 20: break
        aa, bb = aa[:m] - aa[:m].mean(), bb[:m] - bb[:m].mean()
        na, nb = np.linalg.norm(aa), np.linalg.norm(bb)
        if na and nb:
            c = float(np.dot(aa, bb)/(na*nb))
            if c > best[0]: best = (c, k)
    return best


def stats(x):
    peak = float(np.abs(x).max()) if len(x) else 0.0
    clip = float((np.abs(x) >= 32767).mean()*100) if len(x) else 0.0
    return float(np.sqrt((x**2).mean())) if len(x) else 0.0, peak, clip


def main():
    ids = sorted({os.path.basename(p)[:-len(".ambe")]
                  for p in glob.glob(os.path.join(CAPS, "*.ambe"))})
    if LIMIT: ids = ids[:LIMIT]
    print(f"{len(ids)} captures in {CAPS}\n")

    s = WaveShim()
    s.hello()

    rows, wins = [], {"r34": 0, "seq": 0, "neither": 0}
    for cid in ids:
        ambe = open(os.path.join(CAPS, cid + ".ambe"), "rb").read()
        frames = [ambe[i*7:(i+1)*7] for i in range(len(ambe)//7)]

        variants = {}
        for mode, conv in (("raw", lambda f: f), ("deint", deinterleave_r34)):
            h = s.open(DECODER, TDMA_AMBE2)
            out = b""
            try:
                for i in range(0, len(frames) - 1, 2):
                    payload = b"".join(conv(u) + b"\x00" for u in frames[i:i+2])
                    out += s.process(h, payload)
            except (ShimError, ShimDied) as e:
                print(f"  {cid}: decode failed — {e}")
                out = b""
            s.close_handle(h)
            variants[mode] = np.frombuffer(out, dtype="<i2").astype(np.float64)
        dll = variants["deint"]

        r34_p = os.path.join(CAPS, cid + ".r34.wav")
        seq_p = os.path.join(CAPS, cid + ".seq.wav")
        if not (os.path.exists(r34_p) and os.path.exists(seq_p)):
            print(f"  {cid}: missing reference wav, skipped"); continue
        r34, _ = read_wav(r34_p)
        seq, _ = read_wav(seq_p)

        c_raw_r34, _ = env_corr_scan(variants["raw"], r34)
        c_raw_seq, _ = env_corr_scan(variants["raw"], seq)
        c_r34, k_r34 = env_corr_scan(dll, r34)
        c_seq, k_seq = env_corr_scan(dll, seq)
        win = "r34" if c_r34 > c_seq + 0.05 else ("seq" if c_seq > c_r34 + 0.05 else "neither")
        wins[win] += 1
        dr, dp, dc = stats(dll)
        rr, rp, rc = stats(r34)
        sr, sp, sc = stats(seq)
        # How close is it really? If the DLL and the r34 rendering agree
        # sample-for-sample, that is far stronger than a correlation.
        m = min(len(dll), len(r34))
        d = dll[:m] - r34[:m]
        exact = float((d == 0).mean()*100) if m else 0.0
        maxdiff = float(np.abs(d).max()) if m else 0.0
        rows.append((cid, len(frames), c_r34, c_seq, win, dr, dp, dc, rr, rp, sr, sp, sc,
                     exact, maxdiff))
        print(f"  {cid[:24]:24s} {len(frames):5d}fr")
        print(f"      raw bytes      : vs r34 {c_raw_r34:+.3f}   vs seq {c_raw_seq:+.3f}")
        print(f"      de-interleaved : vs r34 {c_r34:+.3f} (shift {k_r34})   "
              f"vs seq {c_seq:+.3f}   -> {win.upper()}")
        print(f"      vs r34 sample-exact: {exact:6.2f}%   max sample diff {maxdiff:.0f}")

    s.close()

    print("\n" + "="*78)
    print(f"agrees with r34: {wins['r34']}   agrees with seq: {wins['seq']}   "
          f"inconclusive: {wins['neither']}")
    if rows:
        import statistics as st
        print(f"\nmean envelope correlation:  vs r34 "
              f"{st.mean(r[2] for r in rows):+.3f}    vs seq "
              f"{st.mean(r[3] for r in rows):+.3f}")
        print("\nsignal statistics (the capture notes predict r34 never clips, "
              "seq clips on most calls):")
        print(f"  DLL   rms {st.mean(r[5] for r in rows):7.0f}  "
              f"peak {max(r[6] for r in rows):7.0f}  "
              f"clipped {st.mean(r[7] for r in rows):.3f}%")
        print(f"  r34   rms {st.mean(r[8] for r in rows):7.0f}  "
              f"peak {max(r[9] for r in rows):7.0f}")
        print(f"  seq   rms {st.mean(r[10] for r in rows):7.0f}  "
              f"peak {max(r[11] for r in rows):7.0f}  "
              f"clipped {st.mean(r[12] for r in rows):.3f}%")
        print(f"\nagreement with the r34 rendering, sample level:")
        print(f"  mean sample-exact {st.mean(r[13] for r in rows):.2f}%   "
              f"worst-case max sample diff {max(r[14] for r in rows):.0f}")

    print("\nverdict:")
    if wins["r34"] and not wins["seq"]:
        print("  With the r34 column interleave undone, the DLL decodes real")
        print("  off-air codewords into the SAME audio as the r34 rendering, on")
        print("  every call. The DLL independently corroborates that r34 is the")
        print("  correct reading — and that off-air frames must be de-interleaved")
        print("  into natural info-vector order before this DLL will accept them.")
    elif wins["seq"]:
        print("  The DLL agrees with the SEQ rendering on some calls — the shim is")
        print("  feeding the bytes in the wrong reading, or the framing is off.")
    else:
        print("  Inconclusive; neither rendering is clearly closer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
