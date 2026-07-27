#!/usr/bin/env python3
"""Measure the DECODER's delay the same differential way the encoder's was
measured: two instances, byte-identical frame streams except for ONE frame,
find the first output frame where they diverge.

The encoder's one-frame delay was found this way after the naive "wait for a
steady state" approach failed. Assuming the decoder has no delay because the
encoder's is known would repeat exactly that mistake.
"""
import os, struct, subprocess, sys

WINSTAGE = os.environ.get("WINSTAGE", "/mnt/c/temp/wave-shim")
SHIM = os.environ.get("SHIM_CMD", f"{WINSTAGE}/wave-shim.exe")
DLL_WIN = os.environ.get("DLL_WIN", r"C:\temp\wave-shim\W7K_UA_SDK.dll")
PCM = os.environ.get("PCM", "vectors/voiced.pcm")

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK = 0
KIND_ENC, KIND_DEC = 0, 1
RATE_TDMA, RATE_FDMA = 0, 1

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: FAILS.append(name)


class Shim:
    def __init__(self):
        self.p = subprocess.Popen(SHIM.split() + [DLL_WIN],
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


def main():
    raw = open(PCM, "rb").read()
    frames = [raw[i*320:(i+1)*320] for i in range(40)]
    s = Shim()
    s.call(OP_HELLO)

    # --- get two clearly different encoded frames -------------------------
    st, he = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    enc = []
    for f in frames:
        st, o = s.call(OP_PROCESS, he + f)
        enc.append(o)
    s.call(OP_CLOSE, he)

    st, he2 = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    quiet = []
    for _ in range(8):
        st, o = s.call(OP_PROCESS, he2 + b"\0" * 320)
        quiet.append(o)
    s.call(OP_CLOSE, he2)
    alt = quiet[-1]
    print(f"speech-encoded frame: {enc[10].hex()}")
    print(f"silence-encoded frame (the substitution): {alt.hex()}")

    # --- differential decode ----------------------------------------------
    # 2 units per call, pad trailing (the layout established by roundtrip.py).
    HIT = 10
    UNITS = 2
    stream_a = list(enc[:32])
    stream_b = list(enc[:32])
    stream_b[HIT] = alt
    if stream_a[HIT] == stream_b[HIT]:
        check("the substituted frame actually differs", False)
        return 1

    outs = {}
    for name, stream in (("a", stream_a), ("b", stream_b)):
        st, hd = s.call(OP_OPEN, bytes([KIND_DEC, RATE_TDMA]))
        out = b""
        for i in range(0, len(stream), UNITS):
            payload = b"".join(u + b"\x00" for u in stream[i:i+UNITS])
            st, o = s.call(OP_PROCESS, hd + payload)
            if st != ST_OK:
                raise SystemExit("decode failed: " + o.decode(errors="replace"))
            out += o
        s.call(OP_CLOSE, hd)
        outs[name] = [out[i*320:(i+1)*320] for i in range(len(out)//320)]

    a, b = outs["a"], outs["b"]
    check("identical input frames give identical output (deterministic)",
          a[:HIT] == b[:HIT], "diverged before the substituted frame")

    diverge = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    print(f"\n  substituted input frame index: {HIT}")
    for i in range(max(0, HIT-2), min(len(a), HIT+4)):
        tag = "  <- substituted frame" if i == HIT else ""
        print(f"   output frame {i:2d}  "
              f"{'DIFFERS' if a[i] != b[i] else 'same   '}{tag}")
    if diverge:
        first = diverge[0]
        delay = first - HIT
        print(f"\n  first divergence at output frame {first} "
              f"=> DECODER DELAY = {delay} frame(s)")
        check("decoder delay measured", True)
        print(f"\n  end-to-end: encoder 1 frame + decoder {delay} frame(s) "
              f"= {1 + delay} frames of algorithmic delay")
    else:
        check("the substitution changed the output at all", False)

    s.close()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: {', '.join(FAILS)}")
        return 1
    print("decoder delay measured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
