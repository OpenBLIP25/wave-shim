/* stub.dll — a fake "codec" with the SAME BINARY SHAPE as the real thing.
 *
 * Step 1 of the build order exists to separate two kinds of bug that are
 * miserable to debug together: a calling-convention bug and an offset bug.
 * This DLL fixes the offsets (it tells the shim where its own entry points
 * are) so that anything that goes wrong is a convention or marshalling bug,
 * against a target whose correct answers are known.
 *
 * What is deliberately mirrored from W7K_UA_SDK.dll:
 *   - 3-slot vtable at object[0]: [0] dtor, [1] process, [2] reset
 *   - __thiscall everywhere; ctor takes ONE stack arg and returns this
 *   - object sizes 0x16e8 / 0x7f0, provider stored at 0x16e4 / 0x7ec
 *   - process(): two stack params, the second ignored
 *   - param1 TRIPLE-indirect to {void *buf; uint32_t byte_len}
 *   - exact-length input check; on ANY error, write 0 into *param1
 *   - output delivered through the provider, never into a caller buffer
 *   - the provider is vended BY THIS DLL, not implemented by the caller
 *
 * What is deliberately NOT mirrored: anything about the codec itself. The
 * "encoded" bytes are an arithmetic fingerprint of the input, chosen so the
 * host can verify byte-for-byte that the right samples arrived in the right
 * order with the right stride.
 *
 * Written in C with hand-rolled vtables rather than C++ classes on purpose:
 * MinGW's C++ ABI emits TWO destructor slots where MSVC emits one, which would
 * shift process() and reset() by a slot and defeat the whole point.
 */
#include <windows.h>
#include <stdint.h>
#include <string.h>

#define EXPORT __declspec(dllexport)

typedef struct { void *buf; uint32_t byte_len; } Desc;

/* --- the provider ---------------------------------------------------------
 * Opaque to the caller, exactly as in the real DLL. Holds the output buffer
 * that process() hands back. */
typedef struct {
    uint32_t magic;
    uint32_t refs;
    uint8_t  out[4096];
    uint32_t out_len;
} Provider;

#define PROVIDER_MAGIC 0x50524f56u  /* 'PROV' */

/* --- the objects ---------------------------------------------------------- */
#define STUB_SIZEOF_ENCODER 0x16e8u
#define STUB_SIZEOF_DECODER 0x07f0u
#define STUB_PROVIDER_OFF_ENCODER 0x16e4u
#define STUB_PROVIDER_OFF_DECODER 0x07ecu

/* offsets inside the object we use for state, well clear of the provider slot */
#define OFF_FRAME_COUNT 0x10u
#define OFF_IS_DECODER  0x14u
#define OFF_RATE        0x18u
#define OFF_HISTORY     0x20u   /* rolling sum, proves state persists          */

#define RATE_TDMA 0u
#define RATE_FDMA 1u

static uint32_t *slot(void *self, uint32_t off) {
    return (uint32_t *)((uint8_t *)self + off);
}

/* --- vtable methods ------------------------------------------------------- */

static void __attribute__((thiscall)) stub_dtor(void *self) {
    Provider *p;
    uint32_t off = *slot(self, OFF_IS_DECODER) ? STUB_PROVIDER_OFF_DECODER
                                               : STUB_PROVIDER_OFF_ENCODER;
    p = (Provider *)(uintptr_t)*slot(self, off);
    if (p && p->magic == PROVIDER_MAGIC && p->refs) p->refs--;
    *slot(self, off) = 0;
}

static void __attribute__((thiscall)) stub_reset(void *self) {
    *slot(self, OFF_FRAME_COUNT) = 0;
    *slot(self, OFF_HISTORY)     = 0;
}

/* The heart of step 1: same indirection depth, same error convention. */
static void __attribute__((thiscall)) stub_process(void *self, Desc ***param1,
                                                   uint32_t unused)
{
    Desc **a, *d;
    Provider *prov;
    uint32_t want_in, out_len, i, is_dec, rate, sum;
    const uint8_t *in;
    uint8_t *out;

    (void)unused;                      /* mirrors the real DLL: never read */

    if (!param1) return;
    a = *param1;
    if (!a) return;                    /* already-cleared slot -> silent no-op */
    d = *a;
    if (!d) { *param1 = 0; return; }

    is_dec = *slot(self, OFF_IS_DECODER);
    rate   = *slot(self, OFF_RATE);

    if (!is_dec) want_in = (rate == RATE_FDMA) ? 960u : 320u;
    else         want_in = (rate == RATE_FDMA) ?  36u :  16u;

    if (d->byte_len != want_in || !d->buf) { *param1 = 0; return; }

    prov = (Provider *)(uintptr_t)*slot(self,
            is_dec ? STUB_PROVIDER_OFF_DECODER : STUB_PROVIDER_OFF_ENCODER);
    if (!prov || prov->magic != PROVIDER_MAGIC) { *param1 = 0; return; }

    in  = (const uint8_t *)d->buf;
    out = prov->out;

    if (!is_dec) {
        /* "encode": per 20 ms frame emit a fingerprint of that frame's samples.
         * 7 bytes for Tdma, 11 for Fdma, matching the real payload sizes. */
        uint32_t frames = (rate == RATE_FDMA) ? 3u : 1u;
        uint32_t fbytes = (rate == RATE_FDMA) ? 11u : 7u;
        uint32_t f;
        out_len = frames * fbytes;
        for (f = 0; f < frames; f++) {
            const int16_t *s = (const int16_t *)(in + f * 320u);
            uint8_t *o = out + f * fbytes;
            int32_t acc = 0;
            for (i = 0; i < 160u; i++) acc += s[i];
            sum = (uint32_t)acc;
            o[0] = (uint8_t)(sum      );
            o[1] = (uint8_t)(sum >>  8);
            o[2] = (uint8_t)(sum >> 16);
            o[3] = (uint8_t)(sum >> 24);
            o[4] = (uint8_t)(*slot(self, OFF_FRAME_COUNT) + f);
            o[5] = 0xA5;
            o[6] = 0x5A;
            for (i = 7; i < fbytes; i++) o[i] = (uint8_t)(0xC0 | i);
            *slot(self, OFF_HISTORY) += sum;
        }
        *slot(self, OFF_FRAME_COUNT) += frames;
    } else {
        /* "decode": expand each unit into 160 samples the host can predict. */
        uint32_t frames = (rate == RATE_FDMA) ? 3u : 2u;
        uint32_t stride = (rate == RATE_FDMA) ? 12u : 8u;
        uint32_t f;
        out_len = frames * 320u;
        for (f = 0; f < frames; f++) {
            int16_t *s = (int16_t *)(out + f * 320u);
            uint8_t seed = in[f * stride];
            for (i = 0; i < 160u; i++)
                s[i] = (int16_t)((int32_t)seed * 16 + (int32_t)i - 80);
        }
        *slot(self, OFF_FRAME_COUNT) += frames;
    }

    prov->out_len = out_len;
}

/* Slot order is the contract being validated. */
static void *stub_vtable_encoder[3] = { (void *)stub_dtor,
                                        (void *)stub_process,
                                        (void *)stub_reset };
static void *stub_vtable_decoder[3] = { (void *)stub_dtor,
                                        (void *)stub_process,
                                        (void *)stub_reset };

/* --- constructors: __thiscall, one stack arg, return this ----------------- */

static void *__attribute__((thiscall)) ctor_common(void *self, void *provider,
                                                   void **vt, uint32_t is_dec,
                                                   uint32_t rate, uint32_t poff)
{
    Provider *p = (Provider *)provider;
    memset(self, 0, is_dec ? STUB_SIZEOF_DECODER : STUB_SIZEOF_ENCODER);
    *(void **)self = vt;
    *slot(self, OFF_IS_DECODER) = is_dec;
    *slot(self, OFF_RATE)       = rate;
    *slot(self, poff)           = (uint32_t)(uintptr_t)provider;
    if (p && p->magic == PROVIDER_MAGIC) p->refs++;   /* null-tolerant, as in
                                                       * the real ctor */
    return self;
}

static void *__attribute__((thiscall)) stub_ctor_tdma_enc(void *self, void *pr) {
    return ctor_common(self, pr, stub_vtable_encoder, 0, RATE_TDMA,
                       STUB_PROVIDER_OFF_ENCODER);
}
static void *__attribute__((thiscall)) stub_ctor_tdma_dec(void *self, void *pr) {
    return ctor_common(self, pr, stub_vtable_decoder, 1, RATE_TDMA,
                       STUB_PROVIDER_OFF_DECODER);
}
static void *__attribute__((thiscall)) stub_ctor_fdma_enc(void *self, void *pr) {
    return ctor_common(self, pr, stub_vtable_encoder, 0, RATE_FDMA,
                       STUB_PROVIDER_OFF_ENCODER);
}
static void *__attribute__((thiscall)) stub_ctor_fdma_dec(void *self, void *pr) {
    return ctor_common(self, pr, stub_vtable_decoder, 1, RATE_FDMA,
                       STUB_PROVIDER_OFF_DECODER);
}

/* --- what the real DLL makes you find the hard way ------------------------ */

static void *__cdecl stub_operator_new(uint32_t size) {
    return HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, size);
}

/* Stands in for the not-yet-identified provider factory (approach (A) in
 * binding.h). The caller never builds one of these itself. */
static void *__cdecl stub_make_provider(void) {
    Provider *p = (Provider *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY,
                                        sizeof(Provider));
    if (p) p->magic = PROVIDER_MAGIC;
    return p;
}

/* How the caller retrieves what process() produced. */
static uint32_t __cdecl stub_provider_take(void *provider, void *dst,
                                           uint32_t cap) {
    Provider *p = (Provider *)provider;
    uint32_t n;
    if (!p || p->magic != PROVIDER_MAGIC) return 0;
    n = p->out_len < cap ? p->out_len : cap;
    memcpy(dst, p->out, n);
    p->out_len = 0;
    return n;
}

/* --- the one export: RVAs, so the shim resolves by base+RVA exactly as it
 * must against the real DLL, but without a hardcoded offset table. ---------- */

typedef struct {
    uint32_t abi_version;
    uint32_t ctor_tdma_enc, ctor_tdma_dec, ctor_fdma_enc, ctor_fdma_dec;
    uint32_t operator_new, make_provider, provider_take;
} StubBindings;

EXPORT const StubBindings *stub_bindings(void) {
    static StubBindings b;
    uintptr_t base = (uintptr_t)GetModuleHandleA("stub.dll");
    b.abi_version   = 1;
    b.ctor_tdma_enc = (uint32_t)((uintptr_t)stub_ctor_tdma_enc  - base);
    b.ctor_tdma_dec = (uint32_t)((uintptr_t)stub_ctor_tdma_dec  - base);
    b.ctor_fdma_enc = (uint32_t)((uintptr_t)stub_ctor_fdma_enc  - base);
    b.ctor_fdma_dec = (uint32_t)((uintptr_t)stub_ctor_fdma_dec  - base);
    b.operator_new  = (uint32_t)((uintptr_t)stub_operator_new   - base);
    b.make_provider = (uint32_t)((uintptr_t)stub_make_provider  - base);
    b.provider_take = (uint32_t)((uintptr_t)stub_provider_take  - base);
    return &b;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)h; (void)reason; (void)r;
    return TRUE;
}
