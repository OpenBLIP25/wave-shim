#!/usr/bin/env python3
"""First contact with the real DLL: construct an encoder and push a few frames.

Deliberately small and loud. Prints the banner, then one line per frame, so a
crash names the last thing that worked.
"""
import os, struct, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import shim_argv, default_dll

DLL_WIN = default_dll()

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK = 0
KIND_ENC, KIND_DEC = 0, 1
RATE_TDMA, RATE_FDMA = 0, 1
NFRAMES = int(os.environ.get("NFRAMES", "10"))


def main():
    p = subprocess.Popen(shim_argv() + [DLL_WIN],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def call(op, payload=b""):
        body = bytes([op]) + payload
        p.stdin.write(struct.pack("<I", len(body)) + body)
        p.stdin.flush()
        hdr = p.stdout.read(4)
        if not hdr or len(hdr) < 4:
            raise SystemExit("shim died — see the stderr trace above")
        (n,) = struct.unpack("<I", hdr)
        r = b""
        while len(r) < n:
            c = p.stdout.read(n - len(r))
            if not c:
                raise SystemExit("shim died mid-response")
            r += c
        return r[0], r[1:]

    st, banner = call(OP_HELLO)
    print("--- banner ---")
    print(banner.decode(errors="replace").rstrip())
    print("---")

    st, h = call(OP_OPEN, bytes([KIND_ENC, RATE_TDMA]))
    if st != ST_OK:
        print("OPEN failed:", h.decode(errors="replace"))
        return 1
    print("OPEN tdma encoder: ok")

    # 20 ms of a 300 Hz tone at 8 kHz, then silence — enough to see whether the
    # output actually varies with the audio.
    import math
    tone = b"".join(struct.pack("<h", int(8000 * math.sin(2*math.pi*300*i/8000)))
                    for i in range(160))
    quiet = b"\0" * 320

    for i in range(NFRAMES):
        pcm = tone if i % 2 == 0 else quiet
        st, out = call(OP_PROCESS, h + pcm)
        if st != ST_OK:
            print(f"frame {i}: ERROR {out.decode(errors='replace')}")
            break
        print(f"frame {i}: {len(out)} bytes  {out.hex()}  ({'tone' if i%2==0 else 'silence'})")

    call(OP_CLOSE, h)
    p.stdin.close()
    p.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
