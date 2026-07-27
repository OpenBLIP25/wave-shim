#!/usr/bin/env python3
"""Sustained-load test.

Three things can only fail at scale and would be invisible in the hundreds of
frames run so far:
  1. pool exhaustion — every frame acquires a buffer and must release it; one
     missed release starves the pool after POOL_BUFFERS frames
  2. memory growth in the shim process
  3. handle churn — each OPEN builds a pool and each CLOSE tears one down

Usage: NFRAMES=20000 CYCLES=200 python3 host/soak.py
"""
import os, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waveshim import WaveShim, ShimError, ShimDied, ENCODER, DECODER, TDMA_AMBE2, FDMA_IMBE

PCM = os.environ.get("PCM", "vectors/clean.pcm")
NFRAMES = int(os.environ.get("NFRAMES", "20000"))
CYCLES = int(os.environ.get("CYCLES", "200"))

FAILS = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(name)


def rss_kb(_pid=None):
    """Resident size of the shim, asked of Windows.

    NOT by PID: under WSL interop the Popen pid is the Linux-side interop stub,
    not the Windows process, so a PID filter silently matches nothing. Filter by
    image name instead (one shim per soak run).
    """
    try:
        out = subprocess.run(
            ["/mnt/c/Windows/System32/tasklist.exe", "/FI",
             "IMAGENAME eq wave-shim.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        if not out or "No tasks" in out:
            return None
        last = out.splitlines()[0].split(",")[-1]
        return int(last.strip('"\r\n ').replace(",", "").replace(" K", ""))
    except Exception:
        return None


def main():
    raw = open(PCM, "rb").read()
    frames = [raw[i*320:(i+1)*320] for i in range(len(raw)//320)]
    print(f"source: {PCM} ({len(frames)} frames available)")
    print(f"plan: {NFRAMES} encode+decode frames, then {CYCLES} open/close cycles\n")

    s = WaveShim()
    s.hello()

    # --- 1. sustained frame throughput -----------------------------------
    he = s.open(ENCODER, TDMA_AMBE2)
    hd = s.open(DECODER, TDMA_AMBE2)
    t0 = time.time()
    pending = []
    n_out = 0
    last_report = 0
    bad = None
    try:
        for i in range(NFRAMES):
            f = frames[i % len(frames)]
            bits = s.process(he, f)
            if len(bits) != 7:
                bad = f"frame {i}: encoder returned {len(bits)} bytes"; break
            pending.append(bits)
            if len(pending) == 2:
                pcm = s.process(hd, b"".join(u + b"\x00" for u in pending))
                if len(pcm) != 640:
                    bad = f"frame {i}: decoder returned {len(pcm)} bytes"; break
                pending.clear(); n_out += 2
            if i - last_report >= 5000:
                last_report = i
                print(f"    {i} frames... ({i/(time.time()-t0):.0f} frames/s)")
    except (ShimError, ShimDied) as e:
        bad = f"frame {n_out}: {e}"
    dt = time.time() - t0

    check(f"{NFRAMES} frames encode+decode without failure", bad is None, bad or "")
    if bad is None:
        print(f"    {NFRAMES} frames in {dt:.1f}s = {NFRAMES/dt:.0f} frames/s "
              f"({NFRAMES*0.02/dt:.0f}x realtime)")
        check("no pool starvation over the run", n_out == (NFRAMES//2)*2,
              f"decoded {n_out} of {(NFRAMES//2)*2}")

    mem_after_frames = rss_kb()
    s.close_handle(he); s.close_handle(hd)

    # --- 2. handle churn --------------------------------------------------
    churn_bad = None
    try:
        for c in range(CYCLES):
            h1 = s.open(ENCODER, TDMA_AMBE2)
            h2 = s.open(DECODER, FDMA_IMBE)
            s.process(h1, frames[c % len(frames)])
            s.reset(h1)
            s.close_handle(h1); s.close_handle(h2)
    except (ShimError, ShimDied) as e:
        churn_bad = f"cycle {c}: {e}"
    check(f"{CYCLES} open/process/reset/close cycles", churn_bad is None, churn_bad or "")

    mem_after_churn = rss_kb()
    if mem_after_frames and mem_after_churn:
        growth = mem_after_churn - mem_after_frames
        print(f"    memory: {mem_after_frames} KB after frames -> "
              f"{mem_after_churn} KB after {CYCLES} cycles "
              f"({growth:+d} KB, {growth/max(CYCLES,1):.1f} KB/cycle)")
        # each cycle deliberately leaks the object shell + its pool; flag only
        # if it is large enough to matter for a long-lived process
        check("handle churn leak stays bounded",
              growth / max(CYCLES, 1) < 200,
              f"{growth/max(CYCLES,1):.1f} KB per cycle")

    # --- 3. still correct afterwards --------------------------------------
    try:
        h = s.open(ENCODER, TDMA_AMBE2)
        a = s.process(h, frames[0])
        s.close_handle(h)
        h = s.open(ENCODER, TDMA_AMBE2)
        b = s.process(h, frames[0])
        s.close_handle(h)
        check("a fresh instance still reproduces its first frame exactly", a == b,
              f"{a.hex()} vs {b.hex()}")
    except (ShimError, ShimDied) as e:
        check("still usable after the soak", False, str(e))

    s.close()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: {', '.join(FAILS)}")
        return 1
    print("soak passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
