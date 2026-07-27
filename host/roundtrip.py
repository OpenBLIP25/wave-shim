#!/usr/bin/env python3
"""Encode real speech through the DLL and feed the result straight back to the
DLL's decoder. Answers whether the near-silent decode seen in the smoke test was
an artifact of testing two frames after a reset, or a wrong frame layout.

Measures RMS over a long run, and sweeps the small space of plausible layouts
for the padded unit (where the pad byte sits, and how many frames per call).
"""
import os, struct, subprocess, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import shim_argv, default_dll

DLL_WIN = default_dll()
PCM = os.environ.get("PCM", "vectors/voiced.pcm")

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK = 0
KIND_ENC, KIND_DEC = 0, 1
RATE_TDMA, RATE_FDMA = 0, 1
NFRAMES = int(os.environ.get("NFRAMES", "180"))


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
            if not c: raise SystemExit("shim died — see stderr trace")
            out += c
        return out
    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=15)


def best_corr(dec, ref):
    """Best normalised cross-correlation of decoded audio against the original,
    searching a few frames of lag. RMS only says "not silent"; correlation says
    "the same speech". A wrong unit layout still decodes to plausible-sounding
    energy, so this is the metric that actually discriminates."""
    if not dec: return 0.0, 0
    d = np.frombuffer(dec, dtype="<i2").astype(np.float64)
    r = np.frombuffer(ref, dtype="<i2").astype(np.float64)
    n = min(len(d), len(r))
    if n < 8000: return 0.0, 0
    d, r = d[:n], r[:n]
    best, best_lag = 0.0, 0
    for lag in range(0, 8 * 160):          # up to 8 frames of delay
        a = d[lag:] if lag else d
        b = r[:len(a)]
        m = min(len(a), len(b))
        if m < 4000: break
        a, b = a[:m], b[:m]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0: continue
        c = float(np.dot(a, b) / (na * nb))
        if c > best: best, best_lag = c, lag
    return best, best_lag


def env_corr(dec, ref, lag):
    """Frame-energy correlation. A 2450 bps parametric vocoder does not preserve
    waveform phase, so raw waveform correlation understates how well it tracked
    the speech. The envelope is the fair measure of "same audio"."""
    if not dec: return 0.0
    d = np.frombuffer(dec, dtype="<i2").astype(np.float64)[lag:]
    r = np.frombuffer(ref, dtype="<i2").astype(np.float64)
    n = min(len(d), len(r)) // 160 * 160
    if n < 160 * 20: return 0.0
    de = np.sqrt((d[:n].reshape(-1, 160) ** 2).mean(axis=1))
    re = np.sqrt((r[:n].reshape(-1, 160) ** 2).mean(axis=1))
    de -= de.mean(); re -= re.mean()
    if np.linalg.norm(de) == 0 or np.linalg.norm(re) == 0: return 0.0
    return float(np.dot(de, re) / (np.linalg.norm(de) * np.linalg.norm(re)))


def env_lag_scan(dec, ref, maxframes=6):
    """Envelope correlation at INTEGER FRAME shifts. The waveform-domain lag
    search returns a sub-frame number that reflects waveform alignment inside a
    phase-free synthesis, not the frame-index delay — so it must not be read as
    "the delay". This scan is the frame-granular measure, and it should agree
    with the differential impulse tests."""
    d = np.frombuffer(dec, dtype="<i2").astype(np.float64)
    r = np.frombuffer(ref, dtype="<i2").astype(np.float64)
    out = []
    for k in range(maxframes + 1):
        dd = d[k*160:]
        n = min(len(dd), len(r)) // 160 * 160
        if n < 160 * 20: break
        de = np.sqrt((dd[:n].reshape(-1,160)**2).mean(axis=1))
        re = np.sqrt((r[:n].reshape(-1,160)**2).mean(axis=1))
        de -= de.mean(); re -= re.mean()
        na, nb = np.linalg.norm(de), np.linalg.norm(re)
        out.append((k, float(np.dot(de,re)/(na*nb)) if na and nb else 0.0))
    return out


def rms(pcm):
    if not pcm: return 0.0
    s = struct.unpack(f"<{len(pcm)//2}h", pcm)
    return (sum(v*v for v in s)/len(s)) ** 0.5


def main():
    raw = open(PCM, "rb").read()
    frames = [raw[i*320:(i+1)*320] for i in range(min(NFRAMES, len(raw)//320))]
    print(f"input: {PCM}  {len(frames)} frames of 20 ms, rms={rms(b''.join(frames)):.0f}")

    s = Shim()
    s.call(OP_HELLO)

    # ---- encode the whole run -------------------------------------------
    st, he = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    if st != ST_OK: raise SystemExit(he.decode(errors="replace"))
    enc = []
    for f in frames:
        st, o = s.call(OP_PROCESS, he + f)
        if st != ST_OK: raise SystemExit("encode failed: " + o.decode(errors="replace"))
        enc.append(o)
    s.call(OP_CLOSE, he)
    distinct = len(set(enc))
    print(f"tdma encode: {len(enc)} frames of {len(enc[0])} bytes, "
          f"{distinct} distinct ({100*distinct/len(enc):.0f}% unique)")
    print(f"  first: {enc[0].hex()}   mid: {enc[len(enc)//2].hex()}")

    # ---- decode, sweeping the plausible unit layouts ---------------------
    # The decoder takes 16 or 32 bytes: 2 or 4 units of 8, where a unit holds a
    # 7-byte payload plus one pad byte. Where the pad sits is unverified, so try
    # both, at both call sizes.
    print("\ntdma decode sweep (rms of the decoded audio; input rms "
          f"{rms(b''.join(frames)):.0f}):")
    results = []
    best_out = {}
    for units in (2, 4):
        for pad, label in ((b"\x00", "trailing"), (None, "leading")):
            st, hd = s.call(OP_OPEN, bytes([KIND_DEC, RATE_TDMA]))
            if st != ST_OK:
                print("  OPEN failed:", hd.decode(errors="replace")); continue
            out = b""
            ok = True
            for i in range(0, len(enc) - units + 1, units):
                grp = enc[i:i+units]
                if pad is None:
                    payload = b"".join(b"\x00" + u for u in grp)
                else:
                    payload = b"".join(u + pad for u in grp)
                st, o = s.call(OP_PROCESS, hd + payload)
                if st != ST_OK:
                    print(f"  {units} units, {label} pad: rejected — "
                          f"{o.decode(errors='replace')}")
                    ok = False
                    break
                out += o
            s.call(OP_CLOSE, hd)
            if ok:
                if units == 2: best_out[label.strip()] = out
                r = rms(out)
                c, lag = best_corr(out, b"".join(frames))
                e = env_corr(out, b"".join(frames), lag)
                results.append((c, r, units, label, lag, e))
                print(f"  {units} units/call, {label:8s} pad: rms={r:7.1f}  "
                      f"waveform corr={c:+.3f}  envelope corr={e:+.3f}  "
                      f"lag {lag/160:.2f} frames")

    # ---- FDMA round trip -------------------------------------------------
    print("\nfdma round trip:")
    st, hfe = s.call(OP_OPEN, bytes([KIND_ENC, RATE_FDMA]))
    st, hfd = s.call(OP_OPEN, bytes([KIND_DEC, RATE_FDMA]))
    fout = b""
    fenc = []
    for i in range(0, len(frames) - 2, 3):
        st, o = s.call(OP_PROCESS, hfe + b"".join(frames[i:i+3]))
        if st != ST_OK: raise SystemExit("fdma encode: " + o.decode(errors="replace"))
        fenc.append(o)
        st, d = s.call(OP_PROCESS, hfd + o)     # encoder output fed verbatim
        if st != ST_OK: raise SystemExit("fdma decode: " + d.decode(errors="replace"))
        fout += d
    s.call(OP_CLOSE, hfe); s.call(OP_CLOSE, hfd)
    print(f"  encoded {len(fenc)} calls x {len(fenc[0])} bytes, "
          f"{len(set(fenc))} distinct")
    fc, flag = best_corr(fout, b"".join(frames))
    fe = env_corr(fout, b"".join(frames), flag)
    print(f"  decoded {len(fout)} bytes, rms={rms(fout):.1f}, "
          f"waveform corr={fc:+.3f}, envelope corr={fe:+.3f}, "
          f"lag {flag/160:.2f} frames")

    s.close()

    print("\nverdict:")
    # Compare by PAD POSITION: 2-units and 4-units per call produce the same
    # stream, so those two rows are duplicates, not competing hypotheses.
    by_pad = {}
    for c, r, units, label, lag, e in results:
        if label not in by_pad or c > by_pad[label][0]:
            by_pad[label] = (c, e, units, lag)
    for label, (c, e, units, lag) in sorted(by_pad.items(), key=lambda kv: -kv[1][0]):
        print(f"  {label:8s} pad: waveform {c:+.3f}  envelope {e:+.3f}")
    if len(by_pad) == 2:
        (win, (cw, ew, _, lagw)), (lose, (cl, _, _, _)) = sorted(
            by_pad.items(), key=lambda kv: -kv[1][0])
        if cw > 0.25 and cw - cl > 0.2:
            print(f"  => the pad byte is {win.upper()}: payload then pad. "
                  f"The {lose} layout decodes to uncorrelated noise "
                  f"({cl:+.3f}), so this is settled.")
            print(f"  => end-to-end lag {lagw/160:.2f} frames "
                  f"(encoder delay + decoder priming)")
        else:
            print("  => still not separated; needs a sharper discriminator")
    print(f"  fdma (encoder output fed back verbatim): waveform {fc:+.3f}, "
          f"envelope {fe:+.3f}")

    print("\nenvelope correlation vs integer frame shift (the frame-granular"
          " delay measure):")
    best_tdma = None
    for label, blob in (("tdma", best_out.get("trailing")), ("fdma", fout)):
        if not blob: continue
        scan = env_lag_scan(blob, b"".join(frames))
        line = "  ".join(f"{k}:{c:+.3f}" for k, c in scan)
        peak = max(scan, key=lambda kv: kv[1])
        print(f"  {label}: {line}   => peak at {peak[0]} frame(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
