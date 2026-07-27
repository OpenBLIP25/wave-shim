#!/usr/bin/env python3
"""Real-DLL smoke test: all four classes, a round trip, and the impulse test.

The impulse test is the one that matters most — it answers whether the encoder
delays its output relative to its input, which static reading could only infer.
"""
import math, os, struct, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import shim_argv, default_dll

DLL_WIN = default_dll()

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK = 0
KIND_ENC, KIND_DEC = 0, 1
RATE_TDMA, RATE_FDMA = 0, 1
ENCODER_DELAY_FRAMES = 1     # measured; see PROTOCOL.md and the fact sheet

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: FAILS.append(name)


class Shim:
    def __init__(self):
        self.p = subprocess.Popen(shim_argv() + [DLL_WIN],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    def call(self, op, payload=b""):
        body = bytes([op]) + payload
        self.p.stdin.write(struct.pack("<I", len(body)) + body)
        self.p.stdin.flush()
        hdr = self._rd(4)
        (n,) = struct.unpack("<I", hdr)
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
        self.p.stdin.close(); self.p.wait(timeout=10)


def tone(f=300, n=160, amp=8000):
    return b"".join(struct.pack("<h", int(amp*math.sin(2*math.pi*f*i/8000)))
                    for i in range(n))
SILENCE = b"\0" * 320


def rms(pcm):
    s = struct.unpack(f"<{len(pcm)//2}h", pcm)
    return (sum(v*v for v in s)/len(s)) ** 0.5


def main():
    s = Shim()
    st, _ = s.call(OP_HELLO)
    check("HELLO", st == ST_OK)

    # --- all four classes construct ---------------------------------------
    handles = {}
    for kind, rate, label in [(KIND_ENC, RATE_TDMA, "tdma encoder"),
                              (KIND_ENC, RATE_FDMA, "fdma encoder"),
                              (KIND_DEC, RATE_TDMA, "tdma decoder"),
                              (KIND_DEC, RATE_FDMA, "fdma decoder")]:
        st, h = s.call(OP_OPEN, bytes([kind, rate]))
        check(f"OPEN {label}", st == ST_OK, h.decode(errors="replace"))
        if st == ST_OK: handles[label] = h

    # --- encoders produce audio-dependent output ---------------------------
    h = handles["tdma encoder"]
    st, a = s.call(OP_PROCESS, h + tone())
    st2, b = s.call(OP_PROCESS, h + SILENCE)
    check("tdma encoder emits 7 bytes", st == ST_OK and len(a) == 7)
    check("output depends on the audio", a != b, f"{a.hex()} vs {b.hex()}")

    hf = handles["fdma encoder"]
    st, f3 = s.call(OP_PROCESS, hf + tone()*3)
    check("fdma encoder emits 36 bytes", st == ST_OK and len(f3) == 36,
          f"got {len(f3)}")

    # --- decoders accept what the encoders produced ------------------------
    # Feed a real run, not two frames: the first frames after a reset are a
    # startup transient and decode to near-silence even when everything is
    # correct. That artifact previously read as a failure.
    hd = handles["tdma decoder"]
    enc_run = []
    for i in range(20):
        st, o = s.call(OP_PROCESS, h + tone(f=300 + 5*i))
        enc_run.append(o)
    out = b""
    for i in range(0, len(enc_run), 2):
        st, pcm = s.call(OP_PROCESS, hd + b"".join(u + b"\x00" for u in enc_run[i:i+2]))
        if st != ST_OK: break
        out += pcm
    check("tdma decoder accepts 16 bytes", st == ST_OK,
          out and "" or "rejected")
    check("tdma decoder emits 640 bytes per call", len(out) == 640 * 10,
          f"got {len(out)}")
    if out:
        tail = out[len(out)//2:]        # skip the startup transient
        check("decoded audio carries real energy once past the transient",
              rms(tail) > 100, f"rms={rms(tail):.1f}")

    hdf = handles["fdma decoder"]
    st, pcm3 = s.call(OP_PROCESS, hdf + f3)   # encoder output feeds the decoder verbatim
    check("fdma decoder accepts 36 bytes", st == ST_OK, pcm3.decode(errors="replace"))
    check("fdma decoder emits 960 bytes", len(pcm3) == 960, f"got {len(pcm3)}")

    for lbl in handles: s.call(OP_CLOSE, handles[lbl])

    # --- THE IMPULSE TEST (differential) -----------------------------------
    # A real vocoder's output never repeats exactly even on digital silence, so
    # "wait for a steady state" does not work. Instead run TWO encoder instances
    # fed byte-identical audio EXCEPT for one frame. Their outputs must be
    # identical up to that frame; the first frame where they diverge is the
    # frame that carries the impulse. Immune to drift, because both instances
    # share the same history.
    print("\n  --- encoder delay (differential impulse test) ---")
    st, ha = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    st, hb = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    PRIME, HIT = 8, 8
    loud = tone(f=500, amp=20000)
    outs_a, outs_b = [], []
    for i in range(PRIME + 4):
        pcm_a = loud if i == HIT else SILENCE
        pcm_b = SILENCE
        st, oa = s.call(OP_PROCESS, ha + pcm_a); outs_a.append(oa)
        st, ob = s.call(OP_PROCESS, hb + pcm_b); outs_b.append(ob)

    check("identical input gives identical output (the codec is deterministic)",
          outs_a[:HIT] == outs_b[:HIT],
          "instances diverged before the impulse — not deterministic")

    diverge = [i for i in range(len(outs_a)) if outs_a[i] != outs_b[i]]
    for i in range(len(outs_a)):
        tag = "  <- loud frame went IN here" if i == HIT else ""
        d = "DIFFERS" if outs_a[i] != outs_b[i] else "same   "
        print(f"   frame {i:2d}  {d}  {outs_a[i].hex()}{tag}")
    if diverge:
        first = diverge[0]
        print(f"   first divergence: output frame {first}, impulse fed at {HIT}"
              f"  => delay = {first - HIT} frame(s)")
        # The one-frame delay is a PROPERTY of this codec, established by
        # measurement (it overturned the original static reading). Assert the
        # known value so a change would show up, rather than asserting zero.
        check("encoder delay is the known 1 frame",
              first - HIT == ENCODER_DELAY_FRAMES,
              f"delay is {first - HIT} frame(s), expected "
              f"{ENCODER_DELAY_FRAMES} — the codec's timing changed")
    else:
        check("the impulse changed the output at all", False,
              "both instances produced identical output throughout")
    s.call(OP_CLOSE, ha); s.call(OP_CLOSE, hb)

    s.close()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: {', '.join(FAILS)}")
        return 1
    print("real-DLL smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
