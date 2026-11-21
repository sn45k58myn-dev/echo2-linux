/* A clocked, output-only virtual playback endpoint for Linux development. */
#define _POSIX_C_SOURCE 200809L
#include "audio_sink.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

enum { SLICE_FRAMES = 240, SLICE_US = 5000 };
static volatile sig_atomic_t stopping;
struct totals { uint64_t frames, nonzero; int peak; };

static void stop(int signo) { (void)signo; stopping = 1; }

static int64_t now_us(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) < 0) return -1;
    return (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
}

static int playback_tick(struct le_audio_sink *sink, int fd, int64_t time_us, struct totals *t) {
    struct le_audio_sink_progress p;
    int16_t buffer[SLICE_FRAMES * 2];
    uint64_t first;
    if (le_audio_sink_get_progress(sink, &p) || !p.active) return 0;
    size_t frames = le_audio_sink_render(sink, buffer, SLICE_FRAMES, &first);
    if (frames) {
        if (fd >= 0) {
            size_t length = frames * sizeof(int16_t) * 2, sent = 0;
            while (sent < length) {
                ssize_t n = write(fd, (const char *)buffer + sent, length - sent);
                if (n < 0 && errno == EINTR) continue;
                if (n <= 0) return -1;
                sent += (size_t)n;
            }
        }
        if (le_audio_sink_commit(sink, p.epoch, p.generation, frames) ||
            le_audio_sink_note_playhead(sink, p.epoch, p.generation, first + frames)) return -1;
        t->frames += frames;
        for (size_t i = 0; i < frames * 2; ++i) {
            int magnitude = abs((int)buffer[i]);
            if (magnitude) ++t->nonzero;
            if (magnitude > t->peak) t->peak = magnitude;
        }
    }
    /* The finish belongs to the last frame handed to the DAC. Frames still
       in the socket's software queue have no bearing on that timestamp. */
    return frames ? le_audio_sink_note_timing(sink, p.epoch, p.generation,
                         LE_AUDIO_SINK_PROGRESS_TIMING_VALID, time_us) : 0;
}

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3 || !strcmp(argv[1], "--help")) {
        fprintf(stderr, "Usage: echo2-sendspin-sink SOCKET [NEW-OUTPUT.pcm]\n"
                        "Clocked virtual DAC, stereo 48 kHz S16_LE. Output is optional.\n");
        return argc == 2 && !strcmp(argv[1], "--help") ? 0 : 2;
    }
    int fd = -1;
    if (argc == 3 && (fd = open(argv[2], O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600)) < 0) {
        perror("open output"); return 1;
    }
    int result = 1;
    struct totals totals = {0};
    uint64_t last_report = 0;
    struct le_audio_sink_config config = {.socket_path = argv[1]};
    struct le_audio_sink *sink = le_audio_sink_create(&config);
    if (!sink) { perror("create sink"); goto out; }
    struct sigaction sa = {.sa_handler = stop};
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    int64_t next = now_us();
    if (next < 0) goto out;
    while (!stopping) {
        if (le_audio_sink_service(sink) < 0) goto out;
        int64_t now = now_us();
        if (now < 0) goto out;
        if (now >= next) {
            /* Catch up small host scheduling delays against the DAC clock. */
            if (now - next > 250000) next = now;
            if (playback_tick(sink, fd, next, &totals) < 0) goto out;
            if (totals.frames - last_report >= 48000) {
                fprintf(stderr, "virtual-dac frames=%" PRIu64 " nonzero=%" PRIu64 " peak=%d\n",
                        totals.frames, totals.nonzero, totals.peak);
                last_report = totals.frames;
            }
            next += SLICE_US;
            continue;
        }
        struct pollfd fds[2] = {{.fd=le_audio_sink_listen_fd(sink), .events=POLLIN},
                                {.fd=le_audio_sink_peer_fd(sink), .events=POLLIN}};
        if (poll(fds, 2, (int)((next-now+999)/1000)) < 0 && errno != EINTR) goto out;
    }
    result = 0;
out:
    fprintf(stderr, "virtual-dac final frames=%" PRIu64 " nonzero=%" PRIu64 " peak=%d\n",
            totals.frames, totals.nonzero, totals.peak);
    if (sink) le_audio_sink_destroy(sink);
    if (fd >= 0 && close(fd) != 0) result = 1;
    return result;
}
