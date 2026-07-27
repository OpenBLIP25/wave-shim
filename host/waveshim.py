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
import os, shlex, struct, subprocess

ENCODER, DECODER = 0, 1
TDMA_AMBE2, FDMA_IMBE = 0, 1

OP_HELLO, OP_OPEN, OP_CLOSE, OP_RESET, OP_PROCESS = 1, 2, 3, 4, 5
ST_OK, ST_ERR = 0, 1


# --- platform resolution ---------------------------------------------------
# The shim is a PE32 binary; three host environments can run it.
#
#   native Windows  python + build\wave-shim.exe, run directly
#   WSL             PE binaries execute via binfmt_misc interop, but they must
#                   live on a Windows-visible path (\\wsl.localhost\... is not
#                   usable as a Windows working directory) -- hence `make stage`
#   Linux + Wine    same binary, launched through `wine`
#
# Every default below is overridable:
#   SHIM_CMD  full command line to launch the shim  (wins over WINSTAGE)
#   WINSTAGE  directory holding wave-shim.exe / stub.dll
#   DLL_WIN   path to W7K_UA_SDK.dll, as the *shim* will see it

def is_wsl():
    if os.name == "nt":
        return False
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def default_stage():
    """Where wave-shim.exe lives, by default, for this platform."""
    if s := os.environ.get("WINSTAGE"):
        return s
    if is_wsl():
        return "/mnt/c/temp/wave-shim"   # staged; see `make stage`
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "build")


def default_shim_cmd():
    """Command line that launches the shim. Wine is used only on non-WSL Linux;
    native Windows and WSL both execute the PE directly."""
    if c := os.environ.get("SHIM_CMD"):
        return c
    exe = os.path.join(default_stage(), "wave-shim.exe")
    if os.name == "nt" or is_wsl():
        return exe
    return f"{os.environ.get('WINE', 'wine')} {exe}"


def to_win_path(p):
    """Express a host path the way the *shim* will resolve it.

    Under WSL the shim runs as a real Windows process, so a /mnt/c/... path
    means nothing to its LoadLibraryEx — it has to be handed C:\\... instead.
    Native Windows and Wine both resolve the host-side path as-is."""
    if not is_wsl():
        return p
    parts = p.replace("\\", "/").split("/")
    if len(parts) > 3 and parts[0] == "" and parts[1] == "mnt" and len(parts[2]) == 1:
        return parts[2].upper() + ":\\" + "\\".join(parts[3:])
    return p          # not under a drive mount; hand it over unchanged


def staged(name):
    """A file in the stage directory, as the shim will see it."""
    return to_win_path(os.path.join(default_stage(), name))


def default_dll():
    """Path to the real DLL, as the shim will resolve it."""
    return os.environ.get("DLL_WIN") or staged("W7K_UA_SDK.dll")


def default_stub():
    """Path to the step-1 stub DLL, as the shim will resolve it."""
    return os.environ.get("STUB_WIN") or staged("stub.dll")


def default_data(name):
    """Test-data directory `name`. Under WSL these are staged into C:\\temp by
    `make stage-data`; elsewhere they default to a `vectors/` directory beside
    the repo, matching the PCM/CHIP defaults in the individual suites.

    Override the root with DATA_DIR, or any single directory with its own
    variable (CAPS, TV_STD_SRC, TV_STD_R34, TV_RC_SRC, TV_RC_R34, CHIP, DEC)."""
    root = os.environ.get("DATA_DIR")
    if root:
        return os.path.join(root, name)
    if is_wsl():
        return os.path.join("/mnt/c/temp", name)
    return os.path.join("vectors", name)


def shim_argv(shim=None):
    """Launch command as an argv list. A bare path may legitimately contain
    spaces (common on Windows), so only word-split when it is not a file that
    exists as given."""
    shim = shim if shim is not None else default_shim_cmd()
    if isinstance(shim, (list, tuple)):
        return list(shim)
    if os.path.exists(shim):
        return [shim]
    return shlex.split(shim, posix=(os.name != "nt"))


DEFAULT_STAGE = default_stage()
DEFAULT_SHIM = default_shim_cmd()
DEFAULT_DLL = default_dll()


class ShimError(RuntimeError):
    """The shim answered with an error status. The message is the shim's."""


class ShimDied(RuntimeError):
    """The shim closed the pipe — look at its stderr for the crash trace."""


class WaveShim:
    def __init__(self, shim=None, dll=None, stderr=None):
        cmd = shim_argv(shim) + [dll if dll is not None else default_dll()]
        self.p = subprocess.Popen(cmd,
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
