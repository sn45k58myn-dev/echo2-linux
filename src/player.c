/* A single-stream Linux PCM player. It neither opens a microphone nor sets mixer controls. */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <tinyalsa/pcm.h>

enum { PERIOD = 480, CHANNELS = 2 };

struct output {
    struct pcm *device;
    int16_t samples[PERIOD * CHANNELS];
    unsigned frames;
    unsigned gain;
};

static int flush(struct output *out)
{
    unsigned sent = 0;
    while (sent < out->frames) {
        unsigned left = out->frames - sent;
        const int16_t *data = out->samples + sent * CHANNELS;
        int count = out->device ? pcm_writei(out->device, data, left)
                                : (int)fwrite(data, CHANNELS * sizeof(*data), left, stdout);
        if (count <= 0 || (unsigned)count > left) {
            fprintf(stderr, "Audio write failed: %s\n", out->device ?
                    pcm_get_error(out->device) : strerror(errno));
            return -1;
        }
        sent += (unsigned)count;
    }
    out->frames = 0;
    return 0;
}

static int emit(struct output *out, int left, int right)
{
    unsigned i = out->frames++ * CHANNELS;
    out->samples[i] = (int16_t)(left * (int)out->gain / 100);
    out->samples[i + 1] = (int16_t)(right * (int)out->gain / 100);
    return out->frames == PERIOD ? flush(out) : 0;
}

static int sample(const unsigned char *p)
{
    int value = p[0] | ((int)p[1] << 8);
    return value >= 32768 ? value - 65536 : value;
}

/* Convert a 16 kHz mono speech interval to three 48 kHz stereo frames. */
static int speech_interval(struct output *out, int start, int end)
{
    for (int i = 0; i < 3; ++i) {
        int value = start + (end - start) * i / 3;
        if (emit(out, value, value) < 0)
            return -1;
    }
    return 0;
}

static int run(struct output *out, int speech)
{
    unsigned char buf[PERIOD * 4];
    size_t stride = speech ? 2 : 4, carry = 0;
    int previous = 0, have_previous = 0;
    for (;;) {
        size_t window = speech ? 160 * stride : sizeof(buf);
        size_t count = fread(buf + carry, 1, window - carry, stdin);
        if (count == 0)
            break;
        size_t total = carry + count, i = 0;
        for (; total - i >= stride; i += stride) {
            int left = sample(buf + i);
            if (speech) {
                if (have_previous && speech_interval(out, previous, left) < 0)
                    return -1;
                previous = left;
                have_previous = 1;
            } else if (emit(out, left, sample(buf + i + 2)) < 0) {
                return -1;
            }
        }
        carry = total - i;
        memmove(buf, buf + i, carry);
    }
    if (ferror(stdin) || carry != 0) {
        fputs("Input ended with an I/O error or a partial PCM frame.\n", stderr);
        return -1;
    }
    if (have_previous && speech_interval(out, previous, previous) < 0)
        return -1;
    if (flush(out) < 0)
        return -1;
    if (out->device) {
        if (pcm_drain(out->device) < 0) {
            fprintf(stderr, "Audio drain failed: %s\n", pcm_get_error(out->device));
            return -1;
        }
    } else if (fflush(stdout) != 0) {
        perror("stdout");
        return -1;
    }
    return 0;
}

static int number(const char *s, unsigned maximum, unsigned *result)
{
    if (!*s || strspn(s, "0123456789") != strlen(s))
        return -1;
    errno = 0;
    unsigned long value = strtoul(s, NULL, 10);
    if (errno || value > maximum)
        return -1;
    *result = (unsigned)value;
    return 0;
}

static const char usage[] =
    "echo2-player render music|speech GAIN < samples.pcm > stereo48.pcm\n"
    "echo2-player play music|speech GAIN CARD DEVICE < samples.pcm\n"
    "Input: music = 48000 Hz stereo, speech = 16000 Hz mono; signed 16-bit LE.\n"
    "GAIN is 0..100 percent. Render writes signed 16-bit LE 48000 Hz stereo.\n";

int main(int argc, char **argv)
{
    struct output out = {0};
    unsigned card = 0, device = 0;
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        fputs(usage, stdout);
        return 0;
    }
    int play = argc == 6 && !strcmp(argv[1], "play");
    int render = argc == 4 && !strcmp(argv[1], "render");
    if ((!play && !render) || (strcmp(argv[2], "speech") && strcmp(argv[2], "music")) ||
        number(argv[3], 100, &out.gain) < 0 ||
        (play && (number(argv[4], 255, &card) < 0 || number(argv[5], 255, &device) < 0))) {
        fputs(usage, stderr);
        return 2;
    }
    const uint16_t endian = 1;
    if (*(const unsigned char *)&endian != 1) {
        fputs("This player requires a little-endian target.\n", stderr);
        return 1;
    }
    if (play) {
        struct pcm_config config = {
            .channels = CHANNELS, .rate = 48000, .period_size = PERIOD,
            .period_count = 4, .format = PCM_FORMAT_S16_LE,
        };
        out.device = pcm_open(card, device, PCM_OUT, &config);
        if (!out.device || !pcm_is_ready(out.device)) {
            fprintf(stderr, "Unable to open playback PCM: %s\n",
                    out.device ? pcm_get_error(out.device) : "allocation failed");
            if (out.device)
                pcm_close(out.device);
            return 1;
        }
    }
    int result = run(&out, !strcmp(argv[2], "speech"));
    if (out.device)
        pcm_close(out.device);
    return result < 0;
}
