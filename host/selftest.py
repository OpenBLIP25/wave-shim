#!/usr/bin/env python3
"""Step-1 self-test: drive wave-shim.exe against stub.dll and check that the
calling convention, the this-pointer, construction and the frame marshalling
all behave, against a target whose correct answers are known.

Runs the shim as a native Windows process through WSL interop (no Wine).
Override with SHIM_CMD if you want to run it some other way, e.g.
    SHIM_CMD='wine /mnt/c/temp/wave-shim/wave-shim.exe' python3 host/selftest.py
"""
import os, struct, subprocess, sys

WINSTAGE = os.environ.get("WINSTAGE", "/mnt/c/temp/wave-shim")
SHIM = os.environ.get("SHIM_CMD", f"{WINSTAGE}/wave-shim.exe")
DLL_WIN = os.environ.get("STUB_WIN", r"C:\temp\wave-shim\stub.dll")

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK = 0
KIND_ENC, KIND_DEC = 0, 1
RATE_TDMA, RATE_FDMA = 0, 1


class Shim:
    def __init__(self, dll):
        cmd = SHIM.split() + [dll]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)

    def call(self, op, payload=b""):
        body = bytes([op]) + payload
        self.p.stdin.write(struct.pack("<I", len(body)) + body)
        self.p.stdin.flush()
        hdr = self._read(4)
        (n,) = struct.unpack("<I", hdr)
        resp = self._read(n)
        return resp[0], resp[1:]

    def _read(self, n):
        out = b""
        while len(out) < n:
            chunk = self.p.stdout.read(n - len(out))
            if not chunk:
                raise RuntimeError("shim closed the pipe (crashed?)")
            out += chunk
        return out

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


FAILS = []

def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def pcm_frame(seed, n=160):
    """Deterministic 20 ms of int16 the stub's fingerprint can be predicted from."""
    return b"".join(struct.pack("<h", ((seed * 37 + i * 11) % 2000) - 1000)
                    for i in range(n))


def expected_fingerprint(pcm, frame_index, fbytes):
    acc = sum(struct.unpack(f"<{len(pcm)//2}h", pcm)) & 0xFFFFFFFF
    o = bytearray(struct.pack("<I", acc))
    o += bytes([frame_index & 0xFF, 0xA5, 0x5A])
    o += bytes([0xC0 | i for i in range(7, fbytes)])
    return bytes(o[:fbytes])


def main():
    print(f"shim : {SHIM}")
    print(f"dll  : {DLL_WIN}")
    s = Shim(DLL_WIN)

    st, banner = s.call(OP_HELLO)
    check("HELLO returns OK", st == ST_OK)
    text = banner.decode(errors="replace")
    print("  --- banner ---")
    for line in text.strip().splitlines():
        print("   |", line)
    check("running in stub mode", "mode=stub" in text)
    check("all four rates advertised", text.count("rate ") == 4)

    # --- encoder, TDMA: one 20 ms frame per call ---------------------------
    st, h = s.call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    check("OPEN tdma encoder", st == ST_OK, text)
    if st != ST_OK:
        print("   ", h.decode(errors="replace"))
        return finish()
    handle = h

    st, out = s.call(OP_PROCESS, handle + pcm_frame(1))
    check("PROCESS accepts 320 bytes", st == ST_OK, out.decode(errors="replace"))
    check("tdma encoder emits 7 bytes", len(out) == 7, f"got {len(out)}")
    check("payload is the expected fingerprint",
          out == expected_fingerprint(pcm_frame(1), 0, 7), out.hex())

    st, out2 = s.call(OP_PROCESS, handle + pcm_frame(2))
    check("frame counter advanced (state persists across calls)",
          st == ST_OK and out2[4] == 1, out2.hex() if st == ST_OK else "err")

    st, err = s.call(OP_PROCESS, handle + pcm_frame(3)[:300])
    check("wrong input length is rejected, not silently accepted", st != ST_OK)

    st, out3 = s.call(OP_PROCESS, handle + pcm_frame(2))
    check("a rejected call does NOT wedge the instance",
          st == ST_OK and len(out3) == 7,
          "instance went dead after an error" if st != ST_OK else "")

    st, _ = s.call(OP_RESET, handle)
    check("RESET returns OK", st == ST_OK)
    st, out4 = s.call(OP_PROCESS, handle + pcm_frame(1))
    check("RESET rewound the frame counter",
          st == ST_OK and out4 == expected_fingerprint(pcm_frame(1), 0, 7))

    st, _ = s.call(OP_CLOSE, handle)
    check("CLOSE returns OK", st == ST_OK)

    # --- encoder, FDMA: three frames batched per call ----------------------
    st, h = s.call(OP_OPEN, bytes([KIND_ENC, RATE_FDMA]))
    check("OPEN fdma encoder", st == ST_OK)
    if st == ST_OK:
        batch = pcm_frame(10) + pcm_frame(11) + pcm_frame(12)
        st, out = s.call(OP_PROCESS, h + batch)
        check("PROCESS accepts 960 bytes", st == ST_OK, out.decode(errors="replace"))
        check("fdma encoder emits 33 bytes (3 x 11)", len(out) == 33, f"got {len(out)}")
        ok = all(out[f*11:(f+1)*11] == expected_fingerprint(pcm_frame(10+f), f, 11)
                 for f in range(3))
        check("each of the 3 batched frames is fingerprinted separately", ok, out.hex())
        s.call(OP_CLOSE, h)

    # --- decoder ------------------------------------------------------------
    st, h = s.call(OP_OPEN, bytes([KIND_DEC, RATE_TDMA]))
    check("OPEN tdma decoder", st == ST_OK)
    if st == ST_OK:
        frames = bytes([0x11] + [0]*7 + [0x22] + [0]*7)
        st, pcm = s.call(OP_PROCESS, h + frames)
        check("PROCESS accepts 16 bytes", st == ST_OK, pcm.decode(errors="replace"))
        check("tdma decoder emits 640 bytes (2 frames x 160 samples)", len(pcm) == 640,
              f"got {len(pcm)}")
        if len(pcm) == 640:
            samples = struct.unpack("<160h", pcm[:320][:320])
            check("decoded samples match the seed of unit 0",
                  samples[0] == 0x11*16 - 80, str(samples[:3]))
        s.call(OP_CLOSE, h)

    # --- protocol hygiene ---------------------------------------------------
    st, _ = s.call(0x7F)
    check("unknown opcode is refused, not fatal", st != ST_OK)
    st, _ = s.call(OP_PROCESS, struct.pack("<I", 99) + b"x"*320)
    check("bad handle is refused, not fatal", st != ST_OK)
    st, _ = s.call(OP_HELLO)
    check("shim still alive after all error paths", st == ST_OK)

    s.close()
    return finish()


def finish():
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): {', '.join(FAILS)}")
        return 1
    print("all checks passed — thiscall plumbing, construction and marshalling "
          "are validated against a known-answer target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
