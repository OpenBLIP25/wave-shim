# wave-shim

Calls the vocoder objects inside `W7K_UA_SDK.dll` from a Win32 console process
and exposes them over a length-prefixed stdio protocol, so a Linux host can
drive them.

**Scope.** Binary interop against a proprietary DLL. The codec is invoked as a
black box. Nothing here models, reimplements, transliterates or explains what
the codec does internally, and nothing learned here belongs in a codec
implementation. This project is deliberately separate from any MBE/AMBE work.

## What this is NOT — read this before you assume anything

Every line below is measured, not inferred, and each links to the section with
the evidence. Most of these fail *silently and plausibly* rather than loudly,
which is why they are collected here.

**1. This is not the AMBE-3000 chip's codec, and not DVSI's reference encoder.**
It is the WAVE 7000 PTT softclient's vocoder — a sibling integration of the same
codec family. Measured against three independent references (the physical chip,
DVSI's STD config, DVSI's RC config), the encoder agrees on a few percent of
frames at best. The decoder is exact on a pure tone but diverges on speech.
Valid as an oracle for **the WAVE desktop target only**. Do not use it as a
stand-in for the radio chip. → *Is this DLL the same as the chip?*

**2. There is no FEC. The DLL trusts its input completely.** Every one of the 49
payload bits reaches the synthesis; nothing is corrected. Feed it a corrupted
frame and you get corrupted audio with no error, no flag and no complaint. The
`processFrame` FEC-error log strings exist in the binary but are not exercised
on this path — do not read them as evidence of correction. → *No FEC on this
path*

**3. Concealment must happen outside this shim.** There is no bad-frame/BFI
input and no way to tell the DLL "this frame was lost" — it will happily
synthesize garbage from it. If your bitstream carries a concealed/erasure flag
(P25 captures do), the host must detect it and substitute a repeat or mute
*before* calling. Synthesizing bad frames is what static sounds like. → *No FEC
on this path*

**4. Off-air frames are not accepted as-is.** A P25 rate-34 codeword off the air
is a 3-way column interleave; this DLL wants natural info-vector order. Fed raw
bytes it does not fail — it produces confident, speech-like garbage that is
*louder* than a correct decode. → *Validated against real off-air audio*

**5. Wrong is usually loud, not silent.** A wrong frame layout, a wrong bit
order, or a wrong vector pairing all produce plausible audio, sometimes with
higher RMS than the correct answer. **Judge by envelope correlation.** RMS
cannot distinguish them, and waveform correlation understates a *correct* result
because a parametric vocoder does not preserve phase. → *Findings*

**6. It is not delay-free.** The encoder delays by one frame (the decoder does
not). There is no flush call — push one extra frame of silence to drain the
tail. And aligning decoded audio to original audio is a *different* number than
aligning bits to input frames. → *Findings*

**7. Instance creation is not thread-safe.** Many instances in one thread is
confirmed bit-exact. Per-frame paths are lock-guarded by the DLL, but instance
creation touches an unguarded process-global registry, and concurrent calls from
multiple threads have never been tested. Serialize OPEN/CLOSE. → *Multiple
simultaneous instances*

**8. The bindings are valid for exactly one DLL build.** Different build, every
offset is wrong and you are executing arbitrary bytes. The shim hashes the DLL
and refuses to bind on a mismatch. → `binding.h`

**9. `mode=stub` is not the real codec.** The stub exists to validate calling
convention and marshalling with known answers. A host doing real work should
refuse to proceed if `HELLO` reports `mode=stub`.

**10. Never judge behaviour from the first frames after a reset.** They are a
startup transient and decode to near-silence even when everything is correct.
This artifact already cost one round of false diagnosis here.

## Status

| | |
|---|---|
| Step 1 — stub DLL, convention + marshalling validated | **done, 22/22 checks pass** |
| Step 2 — real DLL: all four classes construct and run | **done** |
| Round trip, frame layout, delay characterisation | **done, measured** |
| Validated against 14 real off-air P25 captures | **done, 14/14** |
| Compared to chip + DVSI reference vectors | **done — not bit-identical** |
| FEC presence, multi-instance isolation | **done, measured** |
| Sustained load: 20 000 frames, 1500 handle cycles | **clean, 37x realtime** |
| Protocol specified for host authors | `PROTOCOL.md` |

`make checkall` runs every suite below: stub, real DLL, off-air, reference
vectors, behavioural probes and the soak.

Step 1 already earned its keep: it caught a wrong `out_bytes` in the binding
table (the Tdma decoder returns 640 bytes per call, not 320) before that error
could be confused with a codec problem.

## Building and running

The shim is a PE32 (i386) console binary and the host is plain Python 3 talking
to it over stdin/stdout. Nothing in the protocol or the shim is tied to one
operating system; only the *launch* differs.

```
make            # build PE32 exe + stub.dll into build/
make test       # step-1 self-test against stub.dll — no real DLL needed
make checkall   # every suite: stub, real DLL, off-air, vectors, probes, soak
```

`make test` runs on a clean checkout immediately after `make`: the stub supplies
known answers, so the calling convention and marshalling are validated without
the proprietary DLL present.

### The three supported environments

| | build | launch | staging |
|---|---|---|---|
| **native Windows** (MSYS2/MinGW) | `make CC=gcc` | direct | none |
| **WSL** | `make` (cross) | direct, via `binfmt_misc` interop | `make stage` |
| **Linux + Wine** | `make` (cross) | through `wine` | none |

The host scripts detect which of these they are on and resolve the shim path,
the DLL path and the test-data paths accordingly. Every default is overridable:

| variable | meaning |
|---|---|
| `SHIM_CMD` | full command line to launch the shim (wins over `WINSTAGE`) |
| `WINSTAGE` | directory holding `wave-shim.exe` / `stub.dll` |
| `DLL_WIN`, `STUB_WIN` | DLL paths **as the shim will resolve them** |
| `DATA_DIR` | root for test data; or set `CAPS`, `TV_*`, `CHIP`, `DEC` individually |
| `PCM` | source PCM for the round-trip, soak and delay suites |

**On WSL**, PE binaries execute natively through `binfmt_misc`, so the shim is a
real Windows process and the pipe crosses the boundary unchanged. That is better
than Wine here — Wine reimplements the Win32 surface, and when a proprietary DLL
misbehaves you do not want two unknowns. Because the shim is a Windows process,
any path you hand it must be a *Windows* path (`C:\...`, not `/mnt/c/...`); the
host scripts convert automatically. `make stage` copies the binaries to
`/mnt/c/temp/wave-shim` so they sit on a Windows-visible path.

**On native Windows and under Wine** no staging happens — `make stage` reports
that and the scripts launch straight out of `build/`. Set `WINSTAGE` explicitly
if you want to stage anyway.

> Wine is the one path **not** exercised here; it is wired up and needs no code
> change, but it has not been run. WSL and the stub suite are what the 22/22
> result above was measured on.

MinGW matters for one reason worth knowing: the DLL can throw an MSVC C++
exception that a MinGW-built binary **cannot catch** (see `binding.h`, provider
notes). The route this shim takes avoids that path entirely.

### Building on Windows (MSYS2)

There are no prebuilt binaries, by design — see *Why no releases* below. The
build has no dependencies beyond a 32-bit MinGW gcc, so it is short:

1. Install [MSYS2](https://www.msys2.org/), then from the **MSYS2 MINGW32**
   shell (not the plain MSYS shell — the environment decides the target
   architecture):

   ```
   pacman -S --needed mingw-w64-i686-gcc make git
   ```

2. Build and run the self-test:

   ```
   git clone https://github.com/OpenBLIP25/wave-shim
   cd wave-shim
   make CC=gcc          # in MINGW32, plain `gcc` is the i686 compiler
   make test
   ```

`make test` needs Python 3; the reference-vector and off-air suites additionally
need NumPy. Either MSYS2's Python (`pacman -S mingw-w64-i686-python-numpy`) or a
python.org install works — the shim is a separate process, so the host
interpreter's architecture does not have to match. If your Python is named
`python` rather than `python3`, the Makefile detects that; override with
`make test PYTHON=/c/Python312/python.exe` if it guesses wrong.

No staging step is involved: on Windows the scripts launch `build\wave-shim.exe`
directly.

> Untested. This path is wired up and the Makefile no longer hard-codes anything
> that would block it, but the 22/22 result above was measured on WSL. If you hit
> something, an issue with the MSYS2 environment name and the error is welcome.

### Supplying the DLL and the test data

Neither is in this repo, and neither is needed to build.

```
cp /path/to/W7K_UA_SDK.dll <stage or build dir>/
```

`W7K_UA_SDK.dll` ships with the WAVE 7000 PTT softclient and is **not
redistributed here** — supply your own licensed copy. The shim checks its SHA256
and refuses to bind to anything but the one build `binding.h` describes.

The fixtures the suites default to (`vectors/clean.pcm`, `vectors/voiced.pcm`,
`vectors/chip_io/`, `vectors/ambe-samples/`) are likewise absent; point the
variables above at your own copies.

`make stage-data` automates that copy **on WSL only**, from a Windows share:

```
make stage-data DATA_SHARE='\\your-share\path' DVSI_VECTORS='\\your-share\path\DVSI Vectors'
```

A mapped network drive is **not** mounted in WSL — network drives do not
automount — so the copy goes through Windows. `cmd.exe` cannot handle the space
in `"DVSI Vectors"`; that part uses PowerShell. Elsewhere, copy the trees
yourself and set `DATA_DIR`.

### Why no releases

There are deliberately no prebuilt binaries attached to this repo.

1. **A prebuilt exe would work for almost nobody.** `binding.h` is valid for
   exactly one DLL build, and the shim verifies its SHA256 before binding. A
   binary compiled against these offsets is useful only to someone holding that
   same DLL; anyone else gets a refusal and has to re-derive the offsets and
   rebuild regardless.
2. **It would look like malware, because structurally it is the same shape.** An
   unsigned PE that calls `LoadLibrary` and then dispatches into raw computed
   addresses is what a loader or injector looks like to a scanner. Shipping one
   invites Defender and SmartScreen warnings that users would have to be told to
   click past — exactly the habit nobody should be teaching.
3. **The safety claim has to be checkable.** The reason it is reasonable to run
   this at all is that the shim refuses to bind to an unexpected DLL. That is
   only a meaningful guarantee if you can read the source that enforces it. A
   release artifact converts it into something you take on faith.
4. **Building is two compiler invocations** with no dependencies, no configure
   step and no code generation.

Build from source, and read `binding.h` before you run it against a real DLL.

## Layout

```
binding.h          THE build-specific block — RVAs, vtable indices, object
                   sizes, frame sizes, calling convention. Valid for exactly
                   one build; the shim hashes the DLL and refuses to bind on a
                   mismatch unless --force-unverified-dll.
protocol.h         wire format, shared by shim and host
shim.c             the executable: LoadLibraryEx, base+RVA resolution,
                   __thiscall dispatch, framing, crash reporting
stub/stub.c        step-1 target: same binary shape, known answers
host/waveshim.py   reference client — copy or port this
host/selftest.py   step-1: drives the shim against the stub, 22 properties
host/realsmoke.py  real DLL: all four classes, round trip, delay regression
host/realprobe.py  real DLL: minimal "push N frames and print them"
host/roundtrip.py  real DLL: speech round trip + frame-layout discrimination
host/decoderdelay.py  differential decoder-delay measurement
host/delay_resolve.py reconciles the three different delay numbers
host/otacheck.py   real off-air captures vs known-correct renderings
host/vectorcheck.py  vs physical chip + DVSI reference vectors
host/fectest.py    single-bit-flip sweep: is any error correction happening?
host/isolation.py  many simultaneous instances, bit-exact independence
host/resettest.py  RESET is per-instance and equals a fresh instance
host/soak.py       sustained load, handle churn, memory
```

## Protocol

Request `[u32 len][u8 op][payload]`, response `[u32 len][u8 status][payload]`,
little-endian, no delimiters. Ops: `HELLO`, `OPEN(kind, rate)`, `PROCESS(handle,
bytes)`, `RESET(handle)`, `CLOSE(handle)`. Errors come back as status 1 with a
human-readable reason rather than a dead pipe.

`HELLO` advertises only the rates whose entry points actually bound; anything
unresolved is listed under `unbound:` with the reason.

## Findings from the real DLL

- **The encoder has a one-frame delay; the decoder has none.** Output frame N
  carries the audio fed at frame N-1. Static reading said otherwise; the
  measurement wins. Drain the tail with one extra frame of silence.
- **Three delay numbers, all real, all different — do not conflate them.**
  Causal (bits) +1; decoded-audio energy onset +2; energy peak +3. The extra
  frames are synthesis spread, not delay: a frame's energy is distributed over
  the following frames by the overlap-add synthesis. Align *bits to input
  frames* by 1; align *decoded PCM to original PCM* by envelope correlation
  (~2-3). The waveform-correlation lag is not a delay measure at all — it
  reports a misleading sub-frame number because the vocoder drops phase.
- **The FDMA encoder emits 36 bytes, not 33** — 3 x 12-byte units (11 used +
  1 pad), matching the FDMA decoder's 36-byte input exactly.
- **`vtable[0]` is MSVC's scalar deleting destructor**, `(this, unsigned flags)`,
  `ret $4`. Calling it with no argument corrupts the caller's stack.
- **Each codec instance needs its own pool.** The destructor tears its pool
  down, so a shared pool dies with the first CLOSE.
- **The round trip works, and the TDMA pad byte is TRAILING** (payload then
  pad). Over 180 frames of speech: envelope correlation **+0.987** trailing vs
  **-0.408** leading. The earlier "near-silent decode" was an artifact of
  testing two repeated frames straight after a reset, not a layout problem.
  FDMA needs no repacking — the encoder's 36 bytes feed the decoder verbatim
  (envelope corr **+0.993**). 2 vs 4 units per call is pure batching.
- Use **envelope** correlation, not RMS and not waveform correlation, to judge a
  layout: the wrong layout still decodes to speech-like *energy* (rms 2461 vs an
  input rms of 2034) while being anti-correlated with the actual speech. RMS
  cannot see that; waveform correlation understates a correct result because a
  parametric vocoder does not preserve phase.

## Validated against real off-air audio

14 real P25 Phase 2 captures (271 s, post-FEC rate-34 codewords), each shipped
with two renderings of the same bytes — the correct 49-bit reading and the
incorrect one, the latter deliberately rendered as convincing garbage. That
makes it a labelled test, and the DLL is the reference implementation.

| feeding | vs correct rendering | vs garbage rendering |
|---|---|---|
| raw off-air bytes | −0.09 | **+0.91** |
| de-interleaved | **+0.998** | +0.003 |

14 of 14 captures agree with the correct rendering once de-interleaved, and the
DLL's signal statistics match it (rms 539 vs 537, no clipping) rather than the
garbage (rms 2486, clipping on most calls). So: the shim feeds off-air frames
correctly, and the DLL independently corroborates which of the two readings is
right — arrived at from the opposite direction.

**Off-air frames must be de-interleaved into natural info-vector order before
this DLL will accept them.** Fed raw, it does not fail — it produces confident
garbage. See `PROTOCOL.md` and `host/otacheck.py`.

## Is this DLL the same as the chip? No — measured, both directions

A natural assumption is that this DLL is bit-identical to the physical
AMBE-3000. It is not, on either side. `make vectortest` measures it against
reference vectors with matching source material.

Two independent references agree on the answer.

**Encoder** vs DVSI's published reference encodes, each paired with its own
tree's top-level source PCM, sweeping alignment and bit order:

| reference | stem | frame-exact | bit-agreement |
|---|---|---|---|
| RC config (`tv-rc`) | clean | 3.8% | 78.5% |
| RC config | dam | 1.3% | 77.3% |
| RC config | alert | 0.2% | 68.1% |
| STD config (`tv-std`) | clean | 0.2% | 70.1% |
| STD config | dam | 0.3% | 70.3% |
| STD config | noisy | 0.2% | 72.9% |

**Encoder** vs the physical chip's rate-34 bitstream, same PCM in
(`chip_io/encode/*.chip34.bit`), sweeping frame alignment and bit order:

| stem | best frame-exact | best bit-agreement |
|---|---|---|
| mark | 2.0% | 79.8% |
| cpvbad | 1.0% | 76.8% |
| dtone_10 | 0.8% | 58.4% |

Both references give the same verdict: a fraction of a percent frame-exact, and
bit agreement that is above the ~50% chance floor but nowhere near identity. The
DLL shares the format and the structure; it does not share the encoder's
decisions.

**Pairing the vectors correctly is the trap here.** Within each DVSI tree the
source PCM lives at the TOP level; `rNN/*.pcm` is the encode-*decode* output at
rate NN. Pairing `r34/*.bit` with `r33/*.pcm` compares the encode of decoded
audio against the encode of the original and scores near zero — the first run of
this test did exactly that. `tv-rc` and `tv-std` are two different DVSI codec
configurations (`-c RC` / `-c STD` in DVSI's own compare scripts); both contain
usable straight-encode references, they produce different bits, and they ship
different source audio under identical filenames. Never cross the trees.

The winning alignment is r34-interleaved at shift 0, which incidentally confirms
two things: the DLL emits **natural** order while the chip's captures are
interleaved, and both encoders carry the same 1-frame delay.

**Decoder** vs the chip's decode of the same frames — and per the capture
manifest, the chip's decode is byte-identical to DVSI's *published reference*,
so this is effectively a comparison against DVSI itself:

| stem | sample-exact | active-only | max diff |
|---|---|---|---|
| dtone_10 (DTMF tone) | **98.93%** | 98.96% | **1** |
| cpvbad | 25.9% | 21.8% | 649 |
| tia11 | 32.6% | 2.9% | 8841 |
| fambf22c | 40.5% | 2.1% | 7185 |
| mark | 11.1% | 2.8% | 6473 |

On a pure tone the DLL is the reference decoder to within a single LSB. On
speech it diverges substantially. The tone-perfect / speech-divergent split
points at voiced synthesis rather than at anything structural — but chasing that
is codec work, not shim work.

**Consequence for anyone using this as an oracle:** it is a valid reference for
the WAVE desktop target, and it is *not* a bit-exact stand-in for the radio
chip. Do not mix the two.

## No FEC on this path — the DLL trusts its input

Flipping one bit at a time in a frame and re-decoding with a fresh instance:
**49 of 49 payload bits change the audio, 0 of 7 pad bits do.** Nothing is
corrected. A corrupted frame produces corrupted audio, silently — there is no
error-correction layer and no bad-frame input to signal one.

That fits the product: this is a softclient sitting *behind* the FNE, so error
correction has already happened upstream and the vocoder receives clean
post-FEC info bits. It also explains the payload sizes (49 bits, with no room
for a rate-33 frame's FEC bits).

The decoder's `processFrame` does carry FEC-error log strings, so the code
exists — but it is not exercised on this entry point. Treat those strings as
belonging to another path or configuration, not as evidence of correction here.

The same test independently confirms the framing: the 8th byte of each unit is
genuinely ignored padding.

## Multiple simultaneous instances: confirmed

Relevant if a console mixes several sources at once. Six encoder instances run
round-robin, one call each per frame, produce output **bit-identical** to
running each stream alone. All four classes run simultaneously, and an
instance is unaffected by a neighbour being opened and closed mid-stream.

Threading is a separate question and only partly answered:

| | |
|---|---|
| many instances in one thread | **confirmed**, bit-exact isolation |
| per-frame paths | DLL guards them: acquire, release and assign all take a critical section |
| instance *creation* | **unguarded** — the global pool registry insert and its counter have no lock |
| concurrent calls from several threads | **untested** — the shim is single-threaded |

So: serialize OPEN/CLOSE. Per-stream work from separate threads is plausible
given the DLL's own locking, but nobody has demonstrated it.

## RESET between transmissions: safe, and strictly per-instance

The console pattern — reset a vocoder between transmissions — is confirmed
bit-exactly:

- **After RESET an instance is byte-identical to a freshly opened one.** State
  is genuinely cleared, not merely nudged. (Consistent with the binary: the
  constructor calls the same init routine RESET does.)
- **RESET touches only its own instance.** With one instance reset every 5
  frames, a neighbour running straight through was completely unaffected —
  identical output to a solo run. There is no global reset.
- **25 back-to-back reset-then-transmit cycles produce identical output every
  time**, and that output matches a fresh instance. No drift across
  transmissions.
- The decoder behaves the same way.

One expected caveat, not a defect: the first frames after a RESET are a startup
transient, exactly as they are for a newly constructed instance. Do not measure
quality or diagnose behaviour from them.

So you can either RESET between transmissions or close and reopen — they are
equivalent. RESET is cheaper, since it avoids tearing down and rebuilding the
buffer pool.

## Performance and stability

20 000 frames encode+decode: **1850 frames/s, ~37x realtime**, no pool
starvation. 1500 open/process/reset/close cycles: no memory growth (the DLL's
destructor does free its pool). A fresh instance still reproduces its first
frame byte-for-byte after the soak.

## Using it from a host

See `PROTOCOL.md` for the wire format and `host/waveshim.py` for a reference
client. No `mbe-bench`/`shim-host` exists on this machine, so the protocol was
designed here rather than adopted; if one exists elsewhere with its own format,
prefer theirs — only the `op_*` handlers in `shim.c` would change.

## How the provider was resolved (route A)

The constructor takes one argument: a refcounted buffer provider, stored at
`+0x16e4` (encoders) / `+0x7ec` (decoders). `process()` gets its output buffer
from it and dereferences it unconditionally — so a NULL there constructs fine
and crashes on the first call. The DLL builds one for you: the pool factory at
RVA `0x1baf00` takes a `{vtable, bytes_per_buffer}` policy object and returns
the wrapper. The global registry it touches is initialised by the MSVC CRT at
`DLL_PROCESS_ATTACH`, so `LoadLibrary` alone is enough.

It is **not** a caller-implementable interface — `process()` reaches it with a
*direct* call into a concrete DLL-internal class, not a virtual dispatch through
a caller-owned vtable. So the provider has to come from the DLL, and the shim
now does exactly what the DLL's own audio-session factory does:

```
cell -> N : refcount node, 0x3c bytes   -> P : payload descriptor
             +0x00  P                         +0x00  void*  data
             +0x04  refcount                  +0x04  uint32 length
             +0x0c  CRITICAL_SECTION          +0x08  uint32 capacity
```

`process()`'s `param1` is `&cell`; the triple indirection is `param1 -> N -> P`.
On success the DLL *assigns* the output node over your cell, and assign()
releases whatever was there first — so the input cell must hold a **genuine pool
node**, not a fabricated struct. That is almost certainly what made the older
harness throw "device or resource busy" every frame and corrupt its own cell: it
handed the DLL a fake node whose +4 and +0xc were garbage.

Per frame: acquire from the pool, copy PCM into `P->data`, set `P->length`, call
`process()`, read the output back out of the same cell, copy it out, release.
Nothing fabricated, nothing patched, no logger or cache interception.

## Before trusting any output

1. ~~Impulse test~~ — **done, and it overturned the static reading**: there IS a
   one-frame encoder delay. See Findings above.
2. ~~Round-trip~~ — **done**: encoder→decoder round-trips cleanly through the
   DLL for both rates (see Findings).
3. ~~Over-the-air mapping~~ — **done**: validated against 14 real P25 captures,
   which is what DLL-to-DLL agreement could never establish on its own.
4. Still genuinely open: **IMBE/FDMA has no off-air validation** (all 14 captures
   are Phase 2 AMBE+2), and **true multi-threaded operation is untested**.

Full evidence, with per-item confidence and observed-vs-inferred flags:
an internal note, `W7K_SHIM_BINDING_FACTS_2026-07-27.md` (not public).
