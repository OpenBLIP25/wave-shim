#!/usr/bin/env python3
"""Can several codec instances run at once without interfering?

Relevant because the AXS console mixes multiple sources simultaneously, which
means several vocoder instances live at the same time.

The property that actually matters is INSTANCE ISOLATION: an instance's output
must depend only on what that instance was fed, not on what other instances did
in between. Test: run each stream alone, then run them interleaved call-by-call
on separate handles, and require the outputs to be bit-identical.

This does NOT test multi-threading — the shim is single-threaded by design. See
the report for what is and isn't established about that.
"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import WaveShim, ENCODER, DECODER, TDMA_AMBE2, FDMA_IMBE

CAPS = os.environ.get("CAPS", "/mnt/c/temp/ambe-samples")
PCM = os.environ.get("PCM", "vectors/clean.pcm")
N = int(os.environ.get("N", "60"))
STREAMS = int(os.environ.get("STREAMS", "6"))

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(name)


def main():
    raw = open(PCM, "rb").read()
    frames = [raw[i*320:(i+1)*320] for i in range(len(raw)//320)]
    # give each stream genuinely different audio
    streams = [[frames[(k*997 + i) % len(frames)] for i in range(N)]
               for k in range(STREAMS)]

    s = WaveShim()
    s.hello()

    # --- 1. each stream alone -------------------------------------------
    solo = []
    for k in range(STREAMS):
        h = s.open(ENCODER, TDMA_AMBE2)
        solo.append([s.process(h, f) for f in streams[k]])
        s.close_handle(h)

    # --- 2. all streams interleaved, one handle each ---------------------
    handles = [s.open(ENCODER, TDMA_AMBE2) for _ in range(STREAMS)]
    inter = [[] for _ in range(STREAMS)]
    for i in range(N):
        for k in range(STREAMS):                # round-robin, every frame
            inter[k].append(s.process(handles[k], streams[k][i]))
    for h in handles:
        s.close_handle(h)

    print(f"{STREAMS} simultaneous encoder instances, {N} frames each, "
          f"round-robin:\n")
    for k in range(STREAMS):
        check(f"stream {k} identical solo vs interleaved", solo[k] == inter[k],
              "" if solo[k] == inter[k] else
              f"{sum(1 for a,b in zip(solo[k],inter[k]) if a!=b)} frames differ")

    # --- 3. mixed kinds and rates at once --------------------------------
    hs = [s.open(ENCODER, TDMA_AMBE2), s.open(DECODER, TDMA_AMBE2),
          s.open(ENCODER, FDMA_IMBE), s.open(DECODER, FDMA_IMBE)]
    ok = True
    try:
        for i in range(20):
            s.process(hs[0], frames[i])
            s.process(hs[1], b"\x11" + b"\0"*7 + b"\x22" + b"\0"*7)
            s.process(hs[2], b"".join(frames[i:i+3]))
            s.process(hs[3], bytes(36))
    except Exception as e:
        ok = False
        print("   ", e)
    for h in hs:
        s.close_handle(h)
    check("all four classes running simultaneously, interleaved", ok)

    # --- 4. does an encoder instance survive its neighbours being churned? -
    h = s.open(ENCODER, TDMA_AMBE2)
    a = [s.process(h, f) for f in streams[0][:20]]
    h2 = s.open(ENCODER, TDMA_AMBE2)
    for f in streams[1][:20]:
        s.process(h2, f)
    s.close_handle(h2)                          # neighbour opened AND closed
    b = [s.process(h, f) for f in streams[0][20:40]]
    s.close_handle(h)

    h = s.open(ENCODER, TDMA_AMBE2)
    ref = [s.process(h, f) for f in streams[0][:40]]
    s.close_handle(h)
    check("an instance is unaffected by a neighbour being opened and closed "
          "mid-stream", a + b == ref,
          "" if a + b == ref else "output changed")

    s.close()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: {', '.join(FAILS)}")
        return 1
    print("instance isolation confirmed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
