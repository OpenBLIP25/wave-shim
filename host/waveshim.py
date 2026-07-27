"""Reference client for the wave-shim protocol.

This is the implementation to copy or port when writing a host (mbe-bench or
anything else). The protocol is deliberately small enough to reimplement in an
afternoon in any language; see PROTOCOL.md for the wire format.

    from waveshim import WaveShim, ENCODER, DECODER, TDMA_AMBE2, FDMA_IMBE

    with WaveShim() as s:
        h = s.open(ENCODER, TDMA_AMBE2)
        bits = s.process(h, pcm_320_bytes)     # -> 7 bytes
        s.close_handle(h)
"""
import os, struct, subprocess

ENCODER, DECODER = 0, 1
TDMA_AMBE2, FDMA_IMBE = 0, 1

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK, ST_ERR = 0, 1

DEFAULT_STAGE = os.environ.get("WINSTAGE", "/mnt/c/temp/wave-shim")
DEFAULT_SHIM = os.environ.get("SHIM_CMD", f"{DEFAULT_STAGE}/wave-shim.exe")
DEFAULT_DLL = os.environ.get("DLL_WIN", r"C:\temp\wave-shim\W7K_UA_SDK.dll")


class ShimError(RuntimeError):
    """The shim answered with an error status. The message is the shim's."""


class ShimDied(RuntimeError):
    """The shim closed the pipe — look at its stderr for the crash trace."""


class WaveShim:
    def __init__(self, shim=DEFAULT_SHIM, dll=DEFAULT_DLL, stderr=None):
        self.p = subprocess.Popen(shim.split() + [dll],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=stderr)

    # --- transport -------------------------------------------------------
    def _rd(self, n):
        out = b""
        while len(out) < n:
            c = self.p.stdout.read(n - len(out))
            if not c:
                raise ShimDied("shim closed the pipe")
            out += c
        return out

    def _call(self, op, payload=b""):
        body = bytes([op]) + payload
        self.p.stdin.write(struct.pack("<I", len(body)) + body)
        self.p.stdin.flush()
        (n,) = struct.unpack("<I", self._rd(4))
        r = self._rd(n)
        return r[0], r[1:]

    def _ok(self, op, payload=b""):
        st, body = self._call(op, payload)
        if st != ST_OK:
            raise ShimError(body.decode(errors="replace"))
        return body

    # --- operations ------------------------------------------------------
    def hello(self):
        return self._ok(OP_HELLO).decode(errors="replace")

    def open(self, kind, rate):
        return self._ok(OP_OPEN, bytes([kind, rate]))     # opaque 4-byte handle

    def process(self, handle, data):
        return self._ok(OP_PROCESS, handle + data)

    def reset(self, handle):
        self._ok(OP_RESET, handle)

    def close_handle(self, handle):
        self._ok(OP_CLOSE, handle)

    # --- lifecycle -------------------------------------------------------
    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        self.p.wait(timeout=20)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
