/* Short on-device measurement for a Radar/Puffin development boot.
 * Uses the 0.16.8 micd transport contract. Requires an already routed capture
 * path. Prints aggregates only. The physical privacy latch is read-only here.
 */
#define _POSIX_C_SOURCE 200809L
#include <tinyalsa/pcm.h>
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

enum { RATE = 16000, LANES = 9, MICS = 7, PERIOD = 640, FRAME = 3 * LANES };
static const char privacy_path[] = "/sys/devices/platform/amz_privacy/privacy_state";
static const char model_path[] = "/proc/device-tree/idme/device_type_id/value";

static int is_radar(void)
{
    static const char model[] = "A7WXQPH584YP";
    char bytes[sizeof(model) + 1];
    FILE *file = fopen(model_path, "rb");
    if (!file) return 0;
    size_t count = fread(bytes, 1, sizeof(bytes), file);
    int ok = !ferror(file) && (count == sizeof(model) - 1 ||
             (count == sizeof(model) && bytes[count - 1] == '\0')) &&
             memcmp(bytes, model, sizeof(model) - 1) == 0;
    fclose(file);
    return ok;
}

static int privacy(void)
{
    char s[4] = {0};
    FILE *file = fopen(privacy_path, "rb");
    if (!file) return -1;
    size_t n = fread(s, 1, sizeof(s), file);
    int valid = !ferror(file) && (n == 1 || (n == 2 && s[1] == '\n')) &&
                (s[0] == '0' || s[0] == '1');
    fclose(file);
    return valid ? s[0] - '0' : -1;
}

static int64_t now_ms(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) return -1;
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int latch_check(void)
{
    int first = privacy(), last = first, changed = 0;
    int64_t start = now_ms();
    if (first < 0 || start < 0) return 1;
    printf("Initial privacy latch %d. Press mute once, then again to restore.\n", first);
    fflush(stdout);
    for (;;) {
        int64_t current = now_ms();
        if (current < 0 || current - start >= 45000) return 1;
        int value = privacy();
        if (value < 0) return 1;
        if (value != last) {
            printf("Physical privacy latch %d\n", value);
            fflush(stdout);
            if (changed && value == first) return 0;
            changed = 1;
            last = value;
        }
        (void)poll(NULL, 0, 25);
    }
}

/* The top 16 bits in each packed 24-bit word carry the useful samples. */
static int sample(const unsigned char *p)
{
    unsigned v = p[1] | ((unsigned)p[2] << 8);
    return v < 32768 ? (int)v : (int)v - 65536;
}

static void accumulate(uint64_t energy[MICS], const unsigned char *data, int frames)
{
    for (int f = 0; f < frames; f++, data += FRAME)
        for (int m = 0; m < MICS; m++) {
            int s = sample(data + 3 * m);
            energy[m] += (uint64_t)((int64_t)s * s);
        }
}

static int meter(void)
{
    const unsigned goal = RATE * 2;
    struct pcm_config c = {.channels=LANES, .rate=RATE, .period_size=PERIOD,
                           .period_count=4, .format=PCM_FORMAT_S24_3LE};
    unsigned char period[PERIOD * FRAME];
    uint64_t energy[MICS] = {0};
    unsigned frames = 0;
    int result = 1;
    if (privacy() != 0) {
        fputs("Capture gated before opening PCM (muted or unavailable).\n", stderr);
        return 2;
    }
    struct pcm *pcm = pcm_open(0, 24, PCM_IN | PCM_NONBLOCK | PCM_NORESTART, &c);
    if (!pcm || !pcm_is_ready(pcm)) goto done;
    int64_t start = now_ms();
    if (start < 0 || privacy() != 0 || pcm_start(pcm) < 0) goto done;
    struct pollfd fd = {.fd=pcm_get_poll_fd(pcm), .events=POLLIN};
    while (frames < goal) {
        if (privacy() != 0) {
            result = 2;
            goto done;
        }
        int64_t current = now_ms();
        if (current < 0 || current - start > 5000) goto done;
        int ready = poll(&fd, 1, 10);
        if ((ready < 0 && errno == EINTR) || ready == 0) continue;
        if (ready < 0 || !(fd.revents & POLLIN) ||
            (fd.revents & (POLLERR | POLLNVAL | POLLHUP))) goto done;
        unsigned want = goal - frames;
        if (want > PERIOD) want = PERIOD;
        int count = pcm_readi(pcm, period, want);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count <= 0 || (unsigned)count > want) goto done;
        if (privacy() != 0) {
            result = 2;
            goto done;
        }
        accumulate(energy, period, count);
        frames += (unsigned)count;
    }
    if (privacy() != 0) { result = 2; goto done; }
    fputs("Two seconds, active mic lanes 0..6, RMS (signed 16-bit units):", stdout);
    for (int m = 0; m < MICS; m++)
        printf(" %.1f", sqrt((double)energy[m] / frames));
    putchar('\n');
    result = 0;
done:
    if (pcm) pcm_close(pcm);
    if (result == 1) fputs("No complete microphone measurement. Check route, FPGA and PCM availability.\n", stderr);
    if (result == 2) fputs("Privacy latch closed or unavailable. Measurement discarded.\n", stderr);
    return result;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--self-test")) {
        unsigned char frame[FRAME] = {0};
        uint64_t e[MICS] = {0};
        frame[0] = 255; frame[1] = 3; frame[5] = 128;
        frame[6 * 3 + 1] = 255; frame[6 * 3 + 2] = 127;
        frame[7 * 3 + 2] = 127;
        accumulate(e, frame, 1);
        return !(e[0] == 9 && e[1] == 1073741824 && e[2] == 0 &&
                 e[3] == 0 && e[4] == 0 && e[5] == 0 && e[6] == 1073676289);
    }
    if (argc != 2 || (strcmp(argv[1], "meter") && strcmp(argv[1], "latch"))) {
        fputs("Usage: radar-mic-check meter|latch|--self-test\n", stderr);
        return 1;
    }
    if (!is_radar()) { fputs("Radar identity unavailable.\n", stderr); return 1; }
    return !strcmp(argv[1], "meter") ? meter() : latch_check();
}
