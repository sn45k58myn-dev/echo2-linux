/* Synthetic-only Echo 2 NS6571 speech front-end bench, 32-bit Android ABI. */
typedef unsigned int uint32_t;
typedef unsigned long size_t;
typedef struct File File;
extern File *fopen(const char *, const char *);
extern size_t fread(void *, size_t, size_t, File *);
extern size_t fwrite(const void *, size_t, size_t, File *);
extern int fclose(File *);
extern int ferror(File *);
extern int dprintf(int, const char *, ...);
extern const char *getenv(const char *);
extern int strcmp(const char *, const char *);
extern int asp_init(void);
extern void *asp_create_pipeline(int type, int in_format, int in_channels, int in_rate,
                                 int out_format, int out_channels, int out_rate);
extern int asp_process(void *pipeline, const void *in, uint32_t *in_frames, void *out,
                       uint32_t *out_frames, int timing_word, int seconds, int nanoseconds);
extern void asp_destroy_pipeline(void *);
extern void asp_deinit(void);

enum { ASR = 0, SPEAKER = 3, PCM16 = 1, PCM24_PACKED = 6, MIC_FRAMES = 128, REF_FRAMES = 384 };

static int process_frame(void *pipeline, const void *in, void *out, uint32_t frames,
                         unsigned block) {
    uint32_t consumed = frames, produced = frames;
    int status = asp_process(pipeline, in, &consumed, out, &produced, 0,
                             block / 125, (int)(block % 125) * 8000000);
    if (status || consumed != frames || produced != frames) {
        dprintf(2, "AFE: asp_process status=%d input=%u output=%u expected=%u\n",
                status, consumed, produced, frames);
        return 1;
    }
    return 0;
}

int main(void) {
    dprintf(2, "AFE: entered stock Android userspace\n");
    int status = asp_init();
    dprintf(2, "AFE: asp_init=%d\n", status);
    if (status) return 2;
    void *speaker = asp_create_pipeline(SPEAKER, PCM16, 2, 48000, PCM16, 2, 48000);
    void *asr = asp_create_pipeline(ASR, PCM24_PACKED, 9, 16000, PCM16, 1, 16000);
    dprintf(2, "AFE: speaker=%d asr=%d\n", speaker != 0, asr != 0);
    if (!asr || !speaker) return 3;
    File *mics = fopen("/bench/mics.pcm", "rb");
    File *ref = fopen("/bench/ref.pcm", "rb");
    File *output = fopen("/tmp/asr.pcm", "wb");
    if (!mics || !ref || !output) return 4;
    const char *loopback = getenv("BENCH_LOOPBACK");
    int use_input = loopback && !strcmp(loopback, "speaker-input");
    int use_output = loopback && !strcmp(loopback, "speaker-output");
    short previous[2 * REF_FRAMES] = {0};
    unsigned block = 0;
    for (;;) {
        int raw[MIC_FRAMES * 9];
        unsigned char packed[3 * MIC_FRAMES * 9];
        short ref_frame[2 * REF_FRAMES], played[2 * REF_FRAMES], speech[MIC_FRAMES];
        size_t n = fread(raw, 1, sizeof raw, mics);
        size_t r = fread(ref_frame, 1, sizeof ref_frame, ref);
        if (ferror(mics) || ferror(ref)) return 5;
        if (!n && !r) break;
        if (n != sizeof raw || r != sizeof ref_frame) return 5;
        if (process_frame(speaker, ref_frame, played, REF_FRAMES, block)) return 6;
        if (use_input || use_output) {
            const short *current = use_output ? played : ref_frame;
            for (unsigned i = 0; i < MIC_FRAMES; ++i) {
                raw[9*i + 7] = 256 * previous[6*i];
                raw[9*i + 8] = 256 * previous[6*i + 1];
            }
            for (unsigned i = 0; i < 2 * REF_FRAMES; ++i) previous[i] = current[i];
        }
        for (unsigned i = 0; i < MIC_FRAMES * 9; ++i) {
            if (raw[i] > 8388607 || raw[i] < -8388608) return 5;
            unsigned v = (unsigned)raw[i];
            packed[3*i] = v;
            packed[3*i + 1] = v >> 8;
            packed[3*i + 2] = v >> 16;
        }
        if (process_frame(asr, packed, speech, MIC_FRAMES, block)) return 6;
        if (fwrite(speech, 1, sizeof speech, output) != sizeof speech) return 7;
        ++block;
    }
    if (fclose(output) || !block) return 7;
    asp_destroy_pipeline(asr);
    asp_destroy_pipeline(speaker);
    asp_deinit();
    dprintf(2, "AFE: completed %u blocks\n", block);
    return 0;
}

extern void __libc_init(void *, void *, int (*)(void), const void *);
__attribute__((used, noreturn)) void start_bionic(void *stack) {
    static const void *const constructors[3] = {0, 0, 0};
    __libc_init(stack, 0, main, constructors);
    __builtin_unreachable();
}
__asm__(".text\n.global _start\n.type _start,%function\n.arm\n_start:\nmov r0, sp\nb start_bionic\n");
