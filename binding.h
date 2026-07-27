/* ===========================================================================
 * ==                                                                       ==
 * ==   B U I L D - S P E C I F I C   B I N D I N G   B L O C K             ==
 * ==                                                                       ==
 * ==   Everything in this file is valid for EXACTLY ONE BUILD of ONE DLL:  ==
 * ==                                                                       ==
 * ==     W7K_UA_SDK.dll   PE32 i386   4,744,256 bytes   2025-04-22         ==
 * ==     sha256 5b77f4529b16d238ced6c9e27ec1281c708409950596d0afb9df693e   ==
 * ==            acca496b                                                   ==
 * ==     pdb    ...\w7k-ptt-sdk\bin\W7K_SDK\Win32\Release\W7K_UA_SDK.pdb   ==
 * ==                                                                       ==
 * ==   If your DLL hashes differently, EVERY OFFSET BELOW IS WRONG and     ==
 * ==   calling through them executes arbitrary bytes. The shim hashes the  ==
 * ==   file at startup and refuses to bind on a mismatch unless you pass   ==
 * ==   --force-unverified-dll. Re-derive, do not guess.                    ==
 * ==                                                                       ==
 * ==   Source of these facts, with per-item evidence and confidence:       ==
 * ==     internal note W7K_SHIM_BINDING_FACTS_2026-07-27.md (not public)   ==
 * ==                                                                       ==
 * =========================================================================== */
#ifndef WAVE_SHIM_BINDING_H
#define WAVE_SHIM_BINDING_H

#include <stdint.h>

/* --- What to change for a different build ---------------------------------
 *   1. EXPECTED_SHA256          — the new file's hash
 *   2. the four ctor RVAs       — not virtual, not exported; must be found
 *   3. object sizes             — read off the `operator new` feeding the ctor
 *   4. OPERATOR_NEW_RVA         — the DLL's own CRT allocator
 *   5. the byte_len constants   — re-read the `cmp` in each process()
 * Vtable slot indices and the calling convention are stable across all 16
 * codec classes in this DLL family and are the least likely to move.
 * ------------------------------------------------------------------------- */

#define EXPECTED_SHA256 \
    "5b77f4529b16d238ced6c9e27ec1281c708409950596d0afb9df693eacca496b"
#define EXPECTED_SIZE   4744256u

/* Preferred image base. Present only to document what the RVAs were derived
 * from — the shim NEVER uses it. All addresses = returned HMODULE + RVA. */
#define DOCUMENTED_IMAGE_BASE 0x10000000u

/* --- Vtable layout (identical for all four classes) ----------------------- */
#define VT_DTOR     0   /* destructor            */
#define VT_PROCESS  1   /* process()             */
#define VT_RESET    2   /* reset()               */

/* --- Allocation ----------------------------------------------------------- */
#define OPERATOR_NEW_RVA  0x0022b5ccu  /* __cdecl, one size arg, returns ptr  */

#define SIZEOF_ENCODER    0x16e8u      /* 5864 — both Ambe encoders           */
#define SIZEOF_DECODER    0x07f0u      /* 2032 — both Ambe decoders           */

/* Offset at which the constructor stores its one argument (the caller-supplied
 * buffer-provider object). process() dereferences this unconditionally. */
#define PROVIDER_OFF_ENCODER 0x16e4u
#define PROVIDER_OFF_DECODER 0x07ecu

/* --- Per-class bindings ---------------------------------------------------
 * ctor: __thiscall, this in ECX, ONE stack arg, ret $4, returns this in EAX.
 * All other entry points are reached through the vtable at object[0].
 * ------------------------------------------------------------------------- */
typedef struct {
    const char *name;
    uint32_t    ctor_rva;
    uint32_t    vtable_rva;      /* documentation / sanity check only         */
    uint32_t    object_size;
    uint32_t    provider_off;
    uint32_t    in_bytes;        /* process() rejects anything else           */
    uint32_t    in_bytes_alt;    /* second accepted size, 0 if none           */
    uint32_t    out_bytes;       /* for in_bytes; the DLL never reports it    */
    uint32_t    frames_per_call; /* for in_bytes                              */
    uint32_t    unit_bytes;      /* decoders: input bytes per frame; 0 = enc  */
} ClassBinding;

/* MEASURED, and it contradicts the original static reading: the ENCODER HAS A
 * ONE-FRAME DELAY. Output frame N carries the audio fed at frame N-1; the PCM
 * handed to a given call does not affect that call's output at all. Established
 * differentially (two instances, identical audio except one frame, find the
 * first diverging output) because a real vocoder's output never repeats exactly
 * even on digital silence. Align output to input with a one-frame shift, and
 * push one extra frame of silence to drain the tail — there is no flush call.
 * The codec is otherwise deterministic: identical input, identical output. */
#define ENCODER_DELAY_FRAMES 1

/* Bytes of PCM one 20 ms frame occupies: 160 samples x int16. */
#define PCM_BYTES_PER_FRAME 320u

/* in_bytes / out_bytes, all in BYTES (the descriptor's +4 field is a byte
 * count, not a sample count — see fact sheet §5):
 *
 *   Tdma encoder : 320 in  = 160 int16 @ 8 kHz = ONE 20 ms frame
 *                    7 out = 49 info bits, packed MSB-first
 *   Fdma encoder : 960 in  = 480 int16        = THREE 20 ms frames
 *                   36 out = 3 x 12-byte units (11 used + 1 pad) -- MEASURED
 *                            against the DLL; an earlier static reading said 33
 *                            (packed), which was wrong. Note it matches the
 *                            Fdma decoder's 36-byte input exactly.
 *   Tdma decoder :  16 in  = 2 x 8-byte units (7 used + 1 pad); 32 also legal
 *                  320 out = 160 int16 per frame
 *   Fdma decoder :  36 in  = 3 x 12-byte units (11 used + 1 pad)
 *                  960 out = 480 int16
 *
 * UNVERIFIED: the bit-order/field mapping inside the 7- and 11-byte units, and
 * whether the FEC layer is applied. The payload sizes are info-bit-sized, yet
 * the decoders log FEC error counters. Round-trip through the DLL before
 * trusting these bytes against any external reference. */

static const ClassBinding BIND_TDMA_ENCODER = {
    "PttAudio::XisTdmaAmbe::Encoder",
    0x001c2a30u, 0x003e4714u, SIZEOF_ENCODER, PROVIDER_OFF_ENCODER,
    /* in */ 320u, /* alt */ 0u, /* out */ 7u, /* frames */ 1u, /* unit */ 0u
};
static const ClassBinding BIND_TDMA_DECODER = {
    "PttAudio::XisTdmaAmbe::Decoder",
    0x001c2280u, 0x003e4704u, SIZEOF_DECODER, PROVIDER_OFF_DECODER,
    /* in */ 16u, /* alt */ 32u, /* out */ 640u, /* frames */ 2u, /* unit */ 8u
};
static const ClassBinding BIND_FDMA_ENCODER = {
    "PttAudio::XisFdmaAmbe::Encoder",
    0x001c44a0u, 0x003e4b30u, SIZEOF_ENCODER, PROVIDER_OFF_ENCODER,
    /* in */ 960u, /* alt */ 0u, /* out */ 36u, /* frames */ 3u, /* unit */ 0u
};
static const ClassBinding BIND_FDMA_DECODER = {
    "PttAudio::XisFdmaAmbe::Decoder",
    0x001c3ed0u, 0x003e4b20u, SIZEOF_DECODER, PROVIDER_OFF_DECODER,
    /* in */ 36u, /* alt */ 0u, /* out */ 960u, /* frames */ 3u, /* unit */ 12u
};

/* Output size for a given accepted input size. The Tdma decoder is the only
 * variable-output call: 16 bytes in -> 2 frames -> 640 bytes of PCM, 32 in ->
 * 4 frames -> 1280. Encoders are fixed. */
static __inline uint32_t expected_out_bytes(const ClassBinding *b, uint32_t in) {
    if (!b->unit_bytes) return b->out_bytes;
    return (in / b->unit_bytes) * PCM_BYTES_PER_FRAME;
}
static __inline uint32_t expected_frames(const ClassBinding *b, uint32_t in) {
    if (!b->unit_bytes) return b->frames_per_call;
    return in / b->unit_bytes;
}

/* --- Calling convention ---------------------------------------------------
 *
 *   void __thiscall process(void *this, Desc ***param1, uint32_t unused);
 *
 * ret $8: two stack params, callee-cleaned. The second is NEVER referenced by
 * any of the four bodies — pass 0.
 *
 * param1 is TRIPLE-indirect. The DLL does, literally:
 *       ecx = *param1 ;  ecx = *ecx ;  len = ecx[+4] ;  buf = ecx[+0]
 * so the caller must hold three live cells:
 *       Desc  d    = { pcm_or_frame_bytes, byte_count };
 *       Desc *pd   = &d;
 *       Desc **ppd = &pd;
 *       process(obj, &ppd, 0);
 *
 * There is NO return value. Failure is signalled by the DLL writing 0 into
 * *param1 (i.e. clearing ppd). Re-seed all three cells before EVERY call: a
 * cleared slot makes every later call short-circuit into a silent no-op at the
 * `test ecx,ecx; je` on entry, which reads as "the codec stopped working".
 *
 * Output is NOT written to a caller buffer. process() obtains its output buffer
 * from the provider object stored at PROVIDER_OFF_*.
 * ------------------------------------------------------------------------- */

/* --- The provider: RESOLVED (route A) -------------------------------------
 *
 * The ctor's argument is an intrusive refcounted wrapper around a BUFFER POOL,
 * and the DLL will build one for you. No AudioController, no AudioSession.
 *
 * The global registry the pool factory touches (0x461530/0x461534) is
 * initialised by the MSVC CRT dynamic-initialiser table (.CRT$XCU at .rdata
 * 0x338434, entry 0x22cc0) at DLL_PROCESS_ATTACH — i.e. by LoadLibrary itself.
 * Verified by locating that exact pointer inside the NULL-terminated init
 * array. This is what makes standalone construction viable.
 *
 * Two object shapes matter, and the whole call contract falls out of them:
 *
 *   cell (one dword, caller-owned)
 *     -> N : refcount node, 0x3c bytes, from the DLL's operator new
 *              +0x00  P              (the payload descriptor)
 *              +0x04  refcount       (starts at 1)
 *              +0x08  0
 *              +0x0c  CRITICAL_SECTION
 *     -> P : payload descriptor
 *              +0x00  void*    data
 *              +0x04  uint32   length
 *              +0x08  uint32   capacity (0xc80 = 3200 with the stock policy)
 *
 * process()'s param1 is `&cell`. The triple indirection is param1 -> N -> P,
 * and P is what earlier notes called the {buf,len} descriptor.
 *
 * On success the DLL ASSIGNS the output node into your cell: assign() first
 * RELEASES whatever the cell held, then stores the output node and bumps its
 * refcount. Two consequences that decide the design:
 *
 *   - the input cell must hold a GENUINE pool node, not a fabricated struct.
 *     Release on a fake node decrements whatever sits at +4 and enters a
 *     CRITICAL_SECTION that was never initialised. That is almost certainly
 *     the true cause of the old harness's per-frame "device or resource busy"
 *     and its cell-goes-to-zero corruption.
 *   - after process() returns, read the OUTPUT as N=cell, P=*N,
 *     data=P[0], len=P[4] — then release the cell to return the buffer to the
 *     pool, or the pool exhausts.
 *
 * So the per-frame sequence is: acquire a buffer from the pool, copy PCM into
 * P->data, set P->length, call process(), read the output node back out of the
 * same cell, copy the bytes out, release. That is exactly what the real
 * application does, with nothing fabricated.
 * ------------------------------------------------------------------------- */

#define POOL_FACTORY_RVA    0x001baf00u /* (ecx=&cell, edx=count, [esp]=policy)
                                         * caller-cleaned stack arg; cell <- W */
#define POOL_ACQUIRE_RVA    0x001b5d80u /* __thiscall(pool, &cell)             */
#define SP_RELEASE_RVA      0x001b6130u /* __thiscall(&cell)                   */

/* The allocation-policy object handed to the pool factory: 8 bytes,
 * { vtable, bytes_per_buffer }. The stock configuration uses 3200. */
#define ALLOC_POLICY_VTABLE_RVA 0x003e2b54u
#define ALLOC_POLICY_BUFSIZE    0x0c80u

#define NODE_SIZE        0x3cu
#define NODE_OFF_PAYLOAD 0x00u
#define NODE_OFF_REFCNT  0x04u
#define PAY_OFF_DATA     0x00u
#define PAY_OFF_LEN      0x04u
#define PAY_OFF_CAP      0x08u

/* Number of buffers to ask the pool for. The DLL's own factory computes
 * 5 x (configured session count); any sane number works as long as every
 * acquired buffer is released each frame. */
#define POOL_BUFFERS 16u

/* --- superseded note, kept for context ------------------------------------
 * (why this was originally thought to be blocking)
 *
 * The ctor's single argument is a refcounted buffer-provider. It is NOT a
 * caller-implementable interface: process() reaches it as
 *       eax = this[PROVIDER_OFF] ; ecx = *eax ; push &out_slot ; call 0x1b5d80
 * i.e. a DIRECT call into a concrete DLL-internal class, not a virtual
 * dispatch through a caller-owned vtable. So the provider must come from the
 * DLL, and there are two ways to get one:
 *
 *   (A) Call the DLL's own factory. The audio-session factory builds one just
 *       before each ctor call (small operator new of 0x34 / 0xc at RVA
 *       0x1b93xx / 0x1b8bxx). Cleanest if it works; NOT YET IDENTIFIED as a
 *       standalone callable. Set PROVIDER_FACTORY_RVA when found.
 *
 *   (B) Fabricate the object graph and intercept the allocation, as
 *       an internal MSVC harness does. Proven to run 100
 *       frames and produce real, frame-varying output, but it also throws a
 *       benign-but-real MSVC std::system_error on every frame — which a MinGW
 *       build CANNOT catch. Carries a lot of extra build-specific patching.
 *
 * Until (A) is resolved the shim binds and validates everything else, reports
 * the provider as unbound, and refuses OPEN for the real DLL. Step 1 (the stub
 * DLL) exercises the identical plumbing with a provider the stub itself
 * vends — which is exactly the shape (A) will have. */
/* Resolved: route (A) works. See the RESOLVED block above. */

typedef struct { void *buf; uint32_t byte_len; } Desc;

typedef void * (__attribute__((thiscall)) *fn_ctor)   (void *self, void *provider);
typedef void   (__attribute__((thiscall)) *fn_process)(void *self, Desc ***p, uint32_t unused);
typedef void   (__attribute__((thiscall)) *fn_reset)  (void *self);
/* vtable[0] is MSVC's SCALAR DELETING DESTRUCTOR, not a plain destructor:
 * __thiscall(this, unsigned flags), ret $4. flags&1 means "and then operator
 * delete this". Calling it with no argument unbalances the stack and corrupts
 * the caller — verified the hard way. The shim passes 0: destruct without
 * freeing, and leak the object rather than risk a mismatched free. */
typedef void   (__attribute__((thiscall)) *fn_dtor)   (void *self, uint32_t flags);
#define DTOR_FLAG_DESTRUCT_ONLY 0u
#define DTOR_FLAG_AND_DELETE    1u
typedef void * (__cdecl                   *fn_new)    (uint32_t size);

#endif
