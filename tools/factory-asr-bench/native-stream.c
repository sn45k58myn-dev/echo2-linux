/* Owner-local Echo 2 ARMv7 factory speech worker; binary I/O only. */
#include <stdarg.h>

typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { long sec, ns; } stamp;
struct frame { u64 sample, end_ns; u32 flags, n; };
_Static_assert(sizeof(struct frame) == 24, "wire header");
extern int asp_init(void);
extern void asp_deinit(void);
extern void *asp_create_pipeline(int, int, int, int, int, int, int);
extern int asp_process(void *, const void *, u32 *, void *, u32 *, int, int, int);
extern void asp_destroy_pipeline(void *);
extern int read(int, void *, u32);
extern int write(int, const void *, u32);
extern void *memset(void *, int, u32);
extern int *__errno(void);
extern int dprintf(int, const char *, ...);
extern int clock_gettime(int, stamp *);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, u32);
extern char *strstr(const char *, const char *);
extern int vsnprintf(char *, u32, const char *, va_list);
extern int __android_log_vprint(int, const char *, const char *, va_list);

/* Capture the unmodified factory matcher's own events synchronously. */
static unsigned int lock_state, locks, losses;
int __android_log_print(int priority, const char *tag, const char *format, ...) {
    va_list args;
    va_start(args, format);
    if (tag && !strcmp(tag, "ASP/ER")) {
        char message[256];
        va_list copy;
        va_copy(copy, args);
        vsnprintf(message, sizeof message, format, copy);
        va_end(copy);
        if (!strncmp(message, "Synchronized.", 13)) {
            __atomic_store_n(&lock_state, 1, __ATOMIC_RELAXED);
            __atomic_add_fetch(&locks, 1, __ATOMIC_RELAXED);
        } else if (strstr(message, "Out of sync") || strstr(message, "out of sync") ||
                   strstr(message, "Cannot synchronize") || strstr(message, "No match found")) {
            if (__atomic_exchange_n(&lock_state, 0, __ATOMIC_RELAXED))
                __atomic_add_fetch(&losses, 1, __ATOMIC_RELAXED);
        }
    }
    int result = __android_log_vprint(priority, tag, format, args);
    va_end(args);
    return result;
}

/* Return 0 for clean EOF before a record, -1 for truncated/failed I/O. */
static int transfer(int reading, void *buf, u32 len) {
    unsigned char *b = buf;
    u32 done = 0;
    while (done < len) {
        int n = reading ? read(0, b + done, len - done) : write(1, b + done, len - done);
        if (n < 0 && *__errno() == 4) continue;
        if (n <= 0) return !n && reading && !done ? 0 : -1;
        done += (u32)n;
    }
    return 1;
}

static int process(void *pipe, void *in, void *out, u32 frames, const struct frame *hdr) {
    u32 ni = frames, no = frames;
    stamp end = {(long)(hdr->end_ns / 1000000000), (long)(hdr->end_ns % 1000000000)};
    int rc = asp_process(pipe, in, &ni, out, &no, 0, end.sec, end.ns);
    return rc || ni != frames || no != frames;
}

static long long ns(stamp t) { return (long long)t.sec * 1000000000 + t.ns; }

int main(void) {
    if (asp_init()) return 11;
    void *speaker = asp_create_pipeline(3, 1, 2, 48000, 1, 2, 48000);
    void *asr = asp_create_pipeline(0, 6, 9, 16000, 1, 1, 16000);
    if (!speaker || !asr) return 12;
    stamp start, end;
    clock_gettime(2, &start);
    char ready[] = "E2ASRDY\n";
    if (transfer(0, ready, 8) != 1) return 13;
    unsigned int blocks = 0;
    u64 next = 0;
    for (;;) {
        struct frame h;
        int r = transfer(1, &h, sizeof h);
        if (r == 0) break;
        if (r == 1 && h.n == 0 && h.flags == 0 && h.end_ns == 0 && h.sample == next) break;
        if (r < 0 || h.n != 128 || (h.flags & ~2u) || (blocks && h.sample != next))
            return 14;
        unsigned char mic[3456];
        short ref[768], played[768], speech[128];
        if (transfer(1, mic, sizeof mic) != 1) return 15;
        if (h.flags & 2u) {
            if (transfer(1, ref, sizeof ref) != 1) return 16;
        } else {
            memset(ref, 0, sizeof ref);
        }
        if (process(speaker, ref, played, 384, &h) || process(asr, mic, speech, 128, &h))
            return 17;
        if ((h.flags & 2u) && __atomic_load_n(&lock_state, __ATOMIC_RELAXED)) h.flags |= 1u;
        if (transfer(0, &h, sizeof h) != 1 || transfer(0, speech, sizeof speech) != 1)
            return 18;
        next = h.sample + 128;
        ++blocks;
    }
    clock_gettime(2, &end);
    dprintf(2, "factory blocks=%u locked_events=%u lost_events=%u process_cpu_ms=%lld\n",
            blocks, locks, losses, (ns(end) - ns(start)) / 1000000);
    asp_destroy_pipeline(asr);
    asp_destroy_pipeline(speaker);
    asp_deinit();
    return 0;
}

extern void __libc_init(void *, void *, int (*)(void), const void *);
__attribute__((used, noreturn)) void start_bionic(void *stack) {
    static const void *const constructors[3] = {0};
    __libc_init(stack, 0, main, constructors);
    __builtin_unreachable();
}
__asm__(".text\n.global _start\n.type _start,%function\n.arm\n_start:\nmov r0, sp\nb start_bionic\n");
