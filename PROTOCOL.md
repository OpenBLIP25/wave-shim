# wave-shim protocol

A host drives `wave-shim.exe` over its stdin/stdout. No sockets, no threads, no
service. Both sides set binary mode; stderr carries diagnostics and is never
part of the protocol.

Reference client: `host/waveshim.py` — copy or port it; the whole protocol is
about 60 lines in any language.

> **Note on mbe-bench.** This format was designed here, not adopted from an
> existing tool: no `mbe-bench` or `shim-host` exists anywhere on this machine,
> so there was nothing to conform to. If one exists elsewhere and already
> defines a wire format, prefer theirs — the shim's operations map onto any
> reasonable framing, and only `op_*` in `shim.c` would change.

## Framing

```
request   [u32 len][u8 op    ][payload...]      len counts op + payload
response  [u32 len][u8 status][payload...]      len counts status + payload
```

All integers little-endian. The length prefix is the only structure — no
delimiters, no escaping, no text parsing. Every request gets exactly one
response, in order. `status` is 0 for OK; anything else means the payload is a
UTF-8 error message (no trailing NUL).

Errors are *responses*, not disconnections: a rejected frame, a bad handle or an
unknown opcode all leave the shim running and usable.

## Operations

| op | name | payload | response |
|---|---|---|---|
| `0x01` | HELLO | — | text banner + capability lines |
| `0x02` | OPEN | `u8 kind, u8 rate` | `u32 handle` |
| `0x03` | CLOSE | `u32 handle` | — |
| `0x04` | RESET | `u32 handle` | — |
| `0x05` | PROCESS | `u32 handle, bytes` | bytes |

`kind`: 0 encoder, 1 decoder. `rate`: 0 `tdma-ambe2` (AMBE+2), 1 `fdma-imbe`
(IMBE). Treat the handle as 4 opaque bytes and pass them back verbatim.

### HELLO

Returns one `mode=` line then one line per class:

```
wave-shim/1 mode=real base=0x58e40000
rate encoder tdma-ambe2 in=320 out=7 frames=1 PttAudio::XisTdmaAmbe::Encoder
rate encoder fdma-imbe in=960 out=36 frames=3 PttAudio::XisFdmaAmbe::Encoder
rate decoder tdma-ambe2 in=16|32 out=640 frames=2 PttAudio::XisTdmaAmbe::Decoder
rate decoder fdma-imbe in=36 out=960 frames=3 PttAudio::XisFdmaAmbe::Decoder
```

A line starting `rate` is usable; `unavailable` means that class did not bind,
and an `unbound:` block explains why. **Parse this rather than hardcoding
sizes** — it is generated from the binding table, so a host that reads it keeps
working if the binding changes.

`mode=stub` means the shim is talking to the test stub, not the real DLL. A host
doing real work should refuse to proceed on `mode=stub`.

### PROCESS

Sizes are exact; anything else is rejected with an error response.

| class | input | output |
|---|---|---|
| encoder tdma-ambe2 | 320 B = 160 × int16 = one 20 ms frame | 7 B |
| encoder fdma-imbe | 960 B = 480 × int16 = three 20 ms frames | 36 B = 3 × 12 |
| decoder tdma-ambe2 | 16 B = 2 units, or 32 B = 4 units | 640 B / 1280 B |
| decoder fdma-imbe | 36 B = 3 × 12 B units | 960 B |

PCM is signed 16-bit little-endian, 8 kHz, mono.

**Off-air frames need de-interleaving first — this is the biggest trap.**
A P25 rate-34 codeword off the air is a 3-way column interleave of the natural
info-vector order `u0(12)‖u1(12)‖u2(11)‖u3(14)`. **This DLL wants the natural
order.** Hand it raw off-air bytes and it decodes them into confident,
speech-like garbage — loud (rms ~2500 vs ~540) and clipping on most calls,
rather than failing. Undo the interleave with `R34_BIT_ORDER` first; see
`host/otacheck.py::deinterleave_r34`. Validated on 14 real captures: with the
de-interleave, the DLL matches the correct rendering at envelope correlation
**+0.998**; without it, **+0.003**.

The shim does NOT do this for you — it stays pure transport, and the bit order
is a property of your bitstream source, not of the DLL binding.

**Frame packing.** A TDMA unit is **7 payload bytes followed by 1 pad byte**
(pad trailing — established by round trip, envelope correlation +0.987 for
trailing vs −0.408 for leading). An FDMA unit is 11 payload bytes plus 1 pad,
and the FDMA encoder's 36-byte output feeds the FDMA decoder **verbatim** with
no repacking. 2 vs 4 units per decoder call is batching only; both produce the
same stream.

## Things a host must get right

1. **The encoder has a one-frame delay.** Output frame N carries the audio fed
   at frame N−1. The decoder adds none. To align *bits to input frames*, shift
   by 1.
2. **Aligning decoded PCM to the original is a different number.** A frame's
   energy is spread by the synthesis, arriving at +2 to +3 frames. Align audio
   by envelope cross-correlation, not by the causal delay and not by waveform
   correlation (which reports a misleading sub-frame lag because the vocoder
   does not preserve phase).
3. **There is no flush call.** Push one extra frame of silence to drain the
   tail.
4. **Instances are stateful.** RESET returns one to a fresh state; the first
   frames after a reset are a startup transient and are not representative.
   Never judge codec behaviour from the first frame or two.
5. **Judge audio by envelope correlation, not RMS.** A wrong frame layout
   decodes to speech-like energy *louder* than the correct one while being
   anti-correlated with the actual speech. RMS cannot see that.
6. **There is no FEC and no bad-frame input.** Measured: all 49 payload bits
   reach the synthesis, none are corrected. A corrupted frame decodes to
   corrupted audio silently. **Concealment is the host's job** — if your
   bitstream flags a lost or bad frame, substitute a repeat or mute *before*
   calling PROCESS. Handing it a bad frame produces static, not an error.
7. **This is not the AMBE-3000 chip's codec.** It is the WAVE softclient's
   vocoder. Measured against the physical chip and two DVSI reference configs,
   the encoder matches a few percent of frames at best. Treat it as an oracle
   for the WAVE target, not as a chip stand-in.
8. **Serialize OPEN and CLOSE.** Many instances in one thread is confirmed
   bit-exact and safe; instance creation touches unguarded process-global state,
   and concurrent multi-threaded operation is untested.

## Performance

~1850 frames/s for encode+decode on this machine, about 37× realtime, measured
over 20 000 frames. 1500 open/close cycles show no memory growth.

## Versioning

The banner's `wave-shim/1` is the protocol version. Additive changes (new ops,
new capability lines) keep the version; anything that would break an existing
host bumps it.
