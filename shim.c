/* wave-shim — exposes the vocoder objects inside W7K_UA_SDK.dll over a
 * length-prefixed stdio protocol, so a Linux host can drive them.
 *
 * The codec is called as a black box. Nothing here models, reimplements or
 * explains what it does internally; this file is transport, binding and
 * marshalling only.
 *
 * Build:  make            (cross-compiles PE32 with i686-w64-mingw32-gcc)
 * Run  :  wave-shim.exe <path-to-dll> [--force-unverified-dll]
 *
 * Two modes, chosen by whether the target exports stub_bindings():
 *   stub — offsets come from the target itself, so only the calling convention
 *          and marshalling are under test. Step 1; must pass before the real
 *          DLL is touched.
 *   real — offsets come from binding.h and are valid for exactly one build.
 *
 * All build-specific magic lives in binding.h. Nothing below hardcodes an
 * offset.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <io.h>
#include <fcntl.h>

#include "binding.h"
#include "protocol.h"

/* ======================================================================== */
/* sha256 — so a wrong build is caught loudly instead of executing arbitrary
 * bytes at a stale offset.                                                  */
/* ======================================================================== */

typedef struct { uint32_t s[8]; uint64_t n; uint8_t b[64]; size_t bl; } Sha;

static const uint32_t K256[64] = {
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};

#define ROR(x,n) (((x)>>(n))|((x)<<(32-(n))))

static void sha_block(Sha *c, const uint8_t *p) {
    uint32_t w[64], a,b,cc,d,e,f,g,h,t1,t2; int i;
    for (i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[i*4]<<24)|((uint32_t)p[i*4+1]<<16)|
               ((uint32_t)p[i*4+2]<<8)|(uint32_t)p[i*4+3];
    for (; i < 64; i++) {
        uint32_t s0 = ROR(w[i-15],7)^ROR(w[i-15],18)^(w[i-15]>>3);
        uint32_t s1 = ROR(w[i-2],17)^ROR(w[i-2],19)^(w[i-2]>>10);
        w[i] = w[i-16]+s0+w[i-7]+s1;
    }
    a=c->s[0];b=c->s[1];cc=c->s[2];d=c->s[3];
    e=c->s[4];f=c->s[5];g=c->s[6];h=c->s[7];
    for (i = 0; i < 64; i++) {
        uint32_t S1 = ROR(e,6)^ROR(e,11)^ROR(e,25);
        uint32_t ch = (e&f)^((~e)&g);
        uint32_t S0 = ROR(a,2)^ROR(a,13)^ROR(a,22);
        uint32_t mj = (a&b)^(a&cc)^(b&cc);
        t1 = h+S1+ch+K256[i]+w[i]; t2 = S0+mj;
        h=g;g=f;f=e;e=d+t1;d=cc;cc=b;b=a;a=t1+t2;
    }
    c->s[0]+=a;c->s[1]+=b;c->s[2]+=cc;c->s[3]+=d;
    c->s[4]+=e;c->s[5]+=f;c->s[6]+=g;c->s[7]+=h;
}

static void sha_init(Sha *c) {
    static const uint32_t iv[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                                   0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(c->s, iv, sizeof iv); c->n = 0; c->bl = 0;
}
static void sha_update(Sha *c, const uint8_t *p, size_t n) {
    c->n += n;
    while (n) {
        size_t k = 64 - c->bl; if (k > n) k = n;
        memcpy(c->b + c->bl, p, k); c->bl += k; p += k; n -= k;
        if (c->bl == 64) { sha_block(c, c->b); c->bl = 0; }
    }
}
static void sha_final(Sha *c, char *hex) {
    uint64_t bits = c->n * 8; uint8_t pad[72]; size_t i, n = 0; uint8_t out[32];
    pad[n++] = 0x80;
    while ((c->bl + n) % 64 != 56) pad[n++] = 0;
    for (i = 0; i < 8; i++) pad[n++] = (uint8_t)(bits >> (56 - i*8));
    sha_update(c, pad, n);
    for (i = 0; i < 8; i++) {
        out[i*4]   = (uint8_t)(c->s[i] >> 24); out[i*4+1] = (uint8_t)(c->s[i] >> 16);
        out[i*4+2] = (uint8_t)(c->s[i] >> 8);  out[i*4+3] = (uint8_t)(c->s[i]);
    }
    for (i = 0; i < 32; i++) sprintf(hex + i*2, "%02x", out[i]);
    hex[64] = 0;
}

static int hash_file(const char *path, char *hex, uint64_t *size_out) {
    FILE *f = fopen(path, "rb"); Sha c; uint8_t buf[65536]; size_t n;
    if (!f) return 0;
    sha_init(&c);
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) sha_update(&c, buf, n);
    *size_out = c.n;
    fclose(f); sha_final(&c, hex);
    return 1;
}

/* ======================================================================== */
/* Crash reporting — an access violation must name the operation in flight.  */
/* ======================================================================== */

static volatile char g_op[192] = "startup";

static void set_op(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf((char *)g_op, sizeof g_op, fmt, ap);
    va_end(ap);
}

static LONG WINAPI crash_filter(EXCEPTION_POINTERS *ep) {
    fprintf(stderr,
        "\n*** wave-shim: unhandled exception 0x%08lx at %p\n"
        "*** operation in flight: %s\n"
        "*** eax=%08lx ebx=%08lx ecx=%08lx edx=%08lx esi=%08lx edi=%08lx\n"
        "*** esp=%08lx ebp=%08lx\n",
        ep->ExceptionRecord->ExceptionCode, ep->ExceptionRecord->ExceptionAddress,
        (const char *)g_op,
        ep->ContextRecord->Eax, ep->ContextRecord->Ebx, ep->ContextRecord->Ecx,
        ep->ContextRecord->Edx, ep->ContextRecord->Esi, ep->ContextRecord->Edi,
        ep->ContextRecord->Esp, ep->ContextRecord->Ebp);
    if (ep->ExceptionRecord->ExceptionCode == 0xe06d7363u)
        fprintf(stderr, "*** MSVC-thrown C++ exception; a MinGW build cannot "
                        "catch it (see binding.h)\n");
    fflush(stderr);
    return EXCEPTION_EXECUTE_HANDLER;
}

/* ======================================================================== */
/* Binding resolution                                                        */
/* ======================================================================== */

typedef struct {
    uint32_t abi_version;
    uint32_t ctor_tdma_enc, ctor_tdma_dec, ctor_fdma_enc, ctor_fdma_dec;
    uint32_t operator_new, make_provider, provider_take;
} StubBindings;

typedef struct {
    const ClassBinding *cb;
    fn_ctor             ctor;     /* NULL = not bound = rate not advertised */
} Slot;

static HMODULE   g_mod;
static uintptr_t g_base;
static int       g_stub_mode;
static fn_new    g_new;
static Slot      g_slots[2][2];   /* [kind][rate] */
static char      g_bind_errors[1024];

/* stub-mode only */
static void *(__cdecl *g_make_provider)(void);
static uint32_t (__cdecl *g_provider_take)(void *, void *, uint32_t);

/* real-mode buffer-pool plumbing (route A) */
typedef void (__attribute__((thiscall)) *fn_acquire)(void *pool, void **cell);
typedef void (__attribute__((thiscall)) *fn_release)(void **cell);
static void      *g_pool_factory;
static fn_acquire g_acquire;
static fn_release g_release;

/* The pool factory takes ecx=&cell, edx=count and ONE caller-cleaned stack
 * arg — not a convention GCC can spell, so it gets a thunk. Memory constraints
 * keep the compiler out of ecx/edx. */
static void *pool_factory_call(void *fn, void **cell, uint32_t count, void *policy)
{
    void *ret;
    __asm__ volatile (
        "pushl %[pol]\n\t"
        "call  *%[fn]\n\t"
        "addl  $4, %%esp\n\t"
        : "=a"(ret)
        : [pol]"m"(policy), [fn]"m"(fn), "c"(cell), "d"(count)
        : "memory", "cc");
    return ret;
}

static void bind_error(const char *fmt, ...) {
    char line[256]; va_list ap; va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap); va_end(ap);
    strncat(g_bind_errors, line, sizeof g_bind_errors - strlen(g_bind_errors) - 1);
    strncat(g_bind_errors, "\n", sizeof g_bind_errors - strlen(g_bind_errors) - 1);
}

static void *at(uint32_t rva) { return (void *)(g_base + rva); }

static int resolve_bindings(void) {
    const StubBindings *(*get)(void) =
        (const StubBindings *(*)(void))(void *)GetProcAddress(g_mod, "stub_bindings");

    g_slots[KIND_ENCODER][RATE_TDMA_AMBE2].cb = &BIND_TDMA_ENCODER;
    g_slots[KIND_DECODER][RATE_TDMA_AMBE2].cb = &BIND_TDMA_DECODER;
    g_slots[KIND_ENCODER][RATE_FDMA_IMBE ].cb = &BIND_FDMA_ENCODER;
    g_slots[KIND_DECODER][RATE_FDMA_IMBE ].cb = &BIND_FDMA_DECODER;

    if (get) {
        const StubBindings *b = get();
        g_stub_mode = 1;
        if (b->abi_version != 1) {
            bind_error("stub abi_version %u unsupported", b->abi_version);
            return 0;
        }
        g_slots[KIND_ENCODER][RATE_TDMA_AMBE2].ctor = (fn_ctor)at(b->ctor_tdma_enc);
        g_slots[KIND_DECODER][RATE_TDMA_AMBE2].ctor = (fn_ctor)at(b->ctor_tdma_dec);
        g_slots[KIND_ENCODER][RATE_FDMA_IMBE ].ctor = (fn_ctor)at(b->ctor_fdma_enc);
        g_slots[KIND_DECODER][RATE_FDMA_IMBE ].ctor = (fn_ctor)at(b->ctor_fdma_dec);
        g_new           = (fn_new)at(b->operator_new);
        g_make_provider = (void *(__cdecl *)(void))at(b->make_provider);
        g_provider_take = (uint32_t (__cdecl *)(void *, void *, uint32_t))
                          at(b->provider_take);
        return 1;
    }

    /* real DLL: offsets from binding.h */
    g_slots[KIND_ENCODER][RATE_TDMA_AMBE2].ctor = (fn_ctor)at(BIND_TDMA_ENCODER.ctor_rva);
    g_slots[KIND_DECODER][RATE_TDMA_AMBE2].ctor = (fn_ctor)at(BIND_TDMA_DECODER.ctor_rva);
    g_slots[KIND_ENCODER][RATE_FDMA_IMBE ].ctor = (fn_ctor)at(BIND_FDMA_ENCODER.ctor_rva);
    g_slots[KIND_DECODER][RATE_FDMA_IMBE ].ctor = (fn_ctor)at(BIND_FDMA_DECODER.ctor_rva);
    g_new          = (fn_new)at(OPERATOR_NEW_RVA);
    g_pool_factory = at(POOL_FACTORY_RVA);
    g_acquire      = (fn_acquire)at(POOL_ACQUIRE_RVA);
    g_release      = (fn_release)at(SP_RELEASE_RVA);
    return 1;
}

/* Build a buffer pool and return its refcounted wrapper.
 *
 * Each codec instance gets its OWN pool. The codec's destructor tears the pool
 * down, so a shared pool dies with the first CLOSE and leaves every other
 * instance holding a dangling allocator — found the hard way: the next OPEN's
 * first acquire jumped through freed memory. */
static void *make_pool(void) {
    uint32_t *policy = (uint32_t *)g_new(8);
    void *cell = NULL;
    if (!policy) return NULL;
    policy[0] = (uint32_t)(g_base + ALLOC_POLICY_VTABLE_RVA);
    policy[1] = ALLOC_POLICY_BUFSIZE;
    set_op("pool factory(count=%u, bufsize=%u)", POOL_BUFFERS, ALLOC_POLICY_BUFSIZE);
    pool_factory_call(g_pool_factory, &cell, POOL_BUFFERS, policy);
    return cell;
}

/* ======================================================================== */
/* Handles                                                                   */
/* ======================================================================== */

#define MAX_HANDLES 16
typedef struct {
    int used, kind, rate;
    void *obj, *provider, *pool;
    const ClassBinding *cb;
    uint32_t frames;
} Inst;
static Inst g_inst[MAX_HANDLES];

static void **vtbl(void *obj) { return *(void ***)obj; }

/* ======================================================================== */
/* Framed stdio                                                              */
/* ======================================================================== */

static int read_exact(void *p, size_t n) {
    uint8_t *b = (uint8_t *)p;
    while (n) {
        size_t r = fread(b, 1, n, stdin);
        if (r == 0) return 0;
        b += r; n -= r;
    }
    return 1;
}
static void respond(uint8_t status, const void *payload, uint32_t n) {
    uint32_t len = n + 1;
    fwrite(&len, 1, 4, stdout);
    fwrite(&status, 1, 1, stdout);
    if (n) fwrite(payload, 1, n, stdout);
    fflush(stdout);
}
static void respond_err(const char *fmt, ...) {
    char msg[512]; va_list ap; va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
    respond(ST_ERR, msg, (uint32_t)strlen(msg));
}

/* ======================================================================== */
/* Operations                                                                */
/* ======================================================================== */

static int slot_usable(const Slot *s) {
    if (!s->ctor) return 0;
    return g_stub_mode ? (g_make_provider && g_provider_take)
                       : (g_pool_factory != NULL);
}

static const char *rate_name(int r) {
    return r == RATE_FDMA_IMBE ? "fdma-imbe" : "tdma-ambe2";
}

static void op_hello(void) {
    char b[2048]; int n = 0, k, r;
    n += snprintf(b+n, sizeof b-n, "wave-shim/1 mode=%s base=0x%08x\n",
                  g_stub_mode ? "stub" : "real", (unsigned)g_base);
    for (k = 0; k < 2; k++) for (r = 0; r < 2; r++) {
        const Slot *s = &g_slots[k][r];
        char in_desc[32];
        if (s->cb->in_bytes_alt)
            snprintf(in_desc, sizeof in_desc, "%u|%u", s->cb->in_bytes,
                     s->cb->in_bytes_alt);
        else
            snprintf(in_desc, sizeof in_desc, "%u", s->cb->in_bytes);
        n += snprintf(b+n, sizeof b-n, "%s %s %s in=%s out=%u frames=%u %s\n",
                      slot_usable(s) ? "rate" : "unavailable",
                      k == KIND_ENCODER ? "encoder" : "decoder", rate_name(r),
                      in_desc, s->cb->out_bytes, s->cb->frames_per_call,
                      s->cb->name);
    }
    if (g_bind_errors[0])
        n += snprintf(b+n, sizeof b-n, "unbound:\n%s", g_bind_errors);
    respond(ST_OK, b, (uint32_t)n);
}

static void op_open(const uint8_t *p, uint32_t n) {
    int kind, rate, i;
    Slot *s;
    if (n < 2) { respond_err("OPEN: short payload"); return; }
    kind = p[0]; rate = p[1];
    if (kind > 1 || rate > 1) { respond_err("OPEN: bad kind/rate"); return; }
    s = &g_slots[kind][rate];
    if (!slot_usable(s)) {
        respond_err("OPEN: %s %s is not bound in this build\n%s",
                    kind ? "decoder" : "encoder", rate_name(rate), g_bind_errors);
        return;
    }
    for (i = 0; i < MAX_HANDLES && g_inst[i].used; i++) {}
    if (i == MAX_HANDLES) { respond_err("OPEN: handle table full"); return; }

    if (g_stub_mode) {
        set_op("OPEN %s %s: make_provider", kind ? "decoder" : "encoder",
               rate_name(rate));
        g_inst[i].provider = g_make_provider();
        if (!g_inst[i].provider) {
            respond_err("OPEN: provider allocation failed"); return;
        }
    } else {
        g_inst[i].provider = make_pool();
        if (!g_inst[i].provider) {
            respond_err("OPEN: pool factory returned no provider"); return;
        }
        g_inst[i].pool = *(void **)g_inst[i].provider;
        if (!g_inst[i].pool) {
            respond_err("OPEN: provider has no pool at +0"); return;
        }
    }

    set_op("OPEN %s %s: operator new(%u)", kind ? "decoder" : "encoder",
           rate_name(rate), s->cb->object_size);
    g_inst[i].obj = g_new(s->cb->object_size);
    if (!g_inst[i].obj) { respond_err("OPEN: object allocation failed"); return; }
    memset(g_inst[i].obj, 0, s->cb->object_size);

    set_op("OPEN %s %s: ctor(this=%p, provider=%p)", kind ? "decoder" : "encoder",
           rate_name(rate), g_inst[i].obj, g_inst[i].provider);
    s->ctor(g_inst[i].obj, g_inst[i].provider);

    if (!*(void **)g_inst[i].obj) {
        respond_err("OPEN: ctor left no vtable pointer at object+0");
        return;
    }
    g_inst[i].used = 1; g_inst[i].kind = kind; g_inst[i].rate = rate;
    g_inst[i].cb = s->cb; g_inst[i].frames = 0;
    { uint32_t h = (uint32_t)i; respond(ST_OK, &h, 4); }
}

static Inst *handle_of(const uint8_t *p, uint32_t n) {
    uint32_t h;
    if (n < 4) return NULL;
    memcpy(&h, p, 4);
    if (h >= MAX_HANDLES || !g_inst[h].used) return NULL;
    return &g_inst[h];
}

static void op_close(const uint8_t *p, uint32_t n) {
    Inst *in = handle_of(p, n);
    if (!in) { respond_err("CLOSE: bad handle"); return; }
    set_op("CLOSE: vtable[%d] scalar deleting dtor(flags=0)", VT_DTOR);
    ((fn_dtor)vtbl(in->obj)[VT_DTOR])(in->obj, DTOR_FLAG_DESTRUCT_ONLY);
    /* The object and its pool came from the DLL's own allocator, and the dtor
     * has already torn the pool down. Leak the shell rather than risk a
     * mismatched free. */
    memset(in, 0, sizeof *in);
    respond(ST_OK, NULL, 0);
}

static void op_reset(const uint8_t *p, uint32_t n) {
    Inst *in = handle_of(p, n);
    if (!in) { respond_err("RESET: bad handle"); return; }
    set_op("RESET: vtable[%d]", VT_RESET);
    ((fn_reset)vtbl(in->obj)[VT_RESET])(in->obj);
    in->frames = 0;
    respond(ST_OK, NULL, 0);
}

static void op_process(const uint8_t *p, uint32_t n) {
    Inst *in = handle_of(p, n);
    const uint8_t *data; uint32_t len, got = 0;
    static uint8_t out[65536];

    if (!in) { respond_err("PROCESS: bad handle"); return; }
    data = p + 4; len = n - 4;

    if (len != in->cb->in_bytes &&
        !(in->cb->in_bytes_alt && len == in->cb->in_bytes_alt)) {
        respond_err("PROCESS: %s wants %u%s%u bytes, got %u",
                    in->cb->name, in->cb->in_bytes,
                    in->cb->in_bytes_alt ? " or " : "",
                    in->cb->in_bytes_alt, len);
        return;
    }

    if (g_stub_mode) {
        /* stub keeps the simple shape: caller-owned cells, output pulled back
         * out of the stub's provider */
        Desc d, *pd, **ppd;
        d.buf = (void *)data; d.byte_len = len;
        pd = &d; ppd = &pd;
        set_op("PROCESS frame %u on %s: vtable[%d] in=%u",
               in->frames, in->cb->name, VT_PROCESS, len);
        ((fn_process)vtbl(in->obj)[VT_PROCESS])(in->obj, &ppd, 0);
        if (!ppd) {
            respond_err("PROCESS: rejected (cleared *param1) on frame %u, "
                        "input %u bytes", in->frames, len);
            return;
        }
        got = g_provider_take(in->provider, out, sizeof out);
    } else {
        /* Real DLL. The cell must hold a genuine pool node going in, because
         * process() ASSIGNS the output over it and assign() releases whatever
         * was there first. Acquire -> fill -> process -> read back -> release. */
        void *cell = NULL;
        uint32_t *node, *pay;

        set_op("PROCESS frame %u: pool acquire", in->frames);
        g_acquire(in->pool, &cell);
        if (!cell) {
            respond_err("PROCESS: pool acquire returned nothing (exhausted?)");
            return;
        }

        node = (uint32_t *)cell;
        pay  = (uint32_t *)(uintptr_t)node[NODE_OFF_PAYLOAD / 4];
        if (!pay || !pay[PAY_OFF_DATA / 4]) {
            set_op("PROCESS frame %u: release after empty payload", in->frames);
            g_release(&cell);
            respond_err("PROCESS: acquired buffer has no payload");
            return;
        }
        if (len > pay[PAY_OFF_CAP / 4]) {
            g_release(&cell);
            respond_err("PROCESS: input %u exceeds pool buffer capacity %u",
                        len, pay[PAY_OFF_CAP / 4]);
            return;
        }
        memcpy((void *)(uintptr_t)pay[PAY_OFF_DATA / 4], data, len);
        pay[PAY_OFF_LEN / 4] = len;

        set_op("PROCESS frame %u on %s: vtable[%d](this=%p, &cell=%p, 0) in=%u",
               in->frames, in->cb->name, VT_PROCESS, in->obj, (void *)&cell, len);
        ((fn_process)vtbl(in->obj)[VT_PROCESS])(in->obj, (Desc ***)&cell, 0);

        if (!cell) {
            respond_err("PROCESS: rejected (cleared the cell) on frame %u, "
                        "input %u bytes", in->frames, len);
            return;
        }

        set_op("PROCESS frame %u: reading output node", in->frames);
        node = (uint32_t *)cell;
        pay  = (uint32_t *)(uintptr_t)node[NODE_OFF_PAYLOAD / 4];
        if (pay && pay[PAY_OFF_DATA / 4]) {
            got = pay[PAY_OFF_LEN / 4];
            if (got > sizeof out) got = sizeof out;
            memcpy(out, (const void *)(uintptr_t)pay[PAY_OFF_DATA / 4], got);
        }

        set_op("PROCESS frame %u: release", in->frames);
        g_release(&cell);
    }

    {
        uint32_t want = expected_out_bytes(in->cb, len);
        if (got != want)
            fprintf(stderr, "[shim] warning: frame %u produced %u bytes, "
                            "binding says %u\n", in->frames, got, want);
    }
    in->frames += expected_frames(in->cb, len);
    respond(ST_OK, out, got);
}

/* ======================================================================== */

int main(int argc, char **argv) {
    const char *dll = NULL;
    int force = 0, i;
    char hex[65]; uint64_t fsize = 0;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--force-unverified-dll")) force = 1;
        else if (argv[i][0] != '-') dll = argv[i];
    }
    if (!dll) {
        fprintf(stderr, "usage: %s <dll-path> [--force-unverified-dll]\n", argv[0]);
        return 2;
    }

    SetUnhandledExceptionFilter(crash_filter);
    AddVectoredExceptionHandler(1, crash_filter);
    SetErrorMode(SEM_NOGPFAULTERRORBOX | SEM_FAILCRITICALERRORS);
    _setmode(_fileno(stdin),  _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    setvbuf(stdout, NULL, _IOFBF, 65536);

    set_op("hashing %s", dll);
    if (!hash_file(dll, hex, &fsize)) {
        fprintf(stderr, "[shim] cannot read %s\n", dll);
        return 3;
    }

    set_op("LoadLibraryExA %s", dll);
    g_mod = LoadLibraryExA(dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!g_mod) {
        fprintf(stderr, "[shim] LoadLibraryExA failed, err=%lu\n", GetLastError());
        return 4;
    }
    g_base = (uintptr_t)g_mod;

    if (!GetProcAddress(g_mod, "stub_bindings")) {
        if (strcmp(hex, EXPECTED_SHA256) || fsize != EXPECTED_SIZE) {
            fprintf(stderr,
              "\n"
              "###############################################################\n"
              "##  WRONG DLL BUILD                                          ##\n"
              "##  expected sha256 %s\n"
              "##           size   %u\n"
              "##  got      sha256 %s\n"
              "##           size   %llu\n"
              "##  Every offset in binding.h was derived from the expected   ##\n"
              "##  build. Calling through them here would execute arbitrary  ##\n"
              "##  bytes. Re-derive the bindings; do not guess.              ##\n"
              "###############################################################\n\n",
              EXPECTED_SHA256, EXPECTED_SIZE, hex, (unsigned long long)fsize);
            if (!force) return 5;
            fprintf(stderr, "[shim] --force-unverified-dll given; continuing\n");
        } else {
            fprintf(stderr, "[shim] dll verified: %s\n", hex);
        }
    }

    set_op("resolving bindings");
    if (!resolve_bindings())
        fprintf(stderr, "[shim] some bindings unresolved:\n%s", g_bind_errors);
    fprintf(stderr, "[shim] mode=%s base=0x%08x ready\n",
            g_stub_mode ? "stub" : "real", (unsigned)g_base);
    fflush(stderr);

    for (;;) {
        uint32_t len; uint8_t op; uint8_t *buf;
        set_op("waiting for request");
        if (!read_exact(&len, 4)) break;
        if (len < 1 || len > MAX_FRAME) {
            fprintf(stderr, "[shim] bad request length %u\n", len);
            return 6;
        }
        buf = (uint8_t *)malloc(len);
        if (!buf || !read_exact(buf, len)) { free(buf); break; }
        op = buf[0];
        switch (op) {
            case OP_HELLO:   op_hello();                    break;
            case OP_OPEN:    op_open(buf + 1, len - 1);      break;
            case OP_CLOSE:   op_close(buf + 1, len - 1);     break;
            case OP_RESET:   op_reset(buf + 1, len - 1);     break;
            case OP_PROCESS: op_process(buf + 1, len - 1);   break;
            default:         respond_err("unknown op 0x%02x", op); break;
        }
        free(buf);
    }
    return 0;
}
