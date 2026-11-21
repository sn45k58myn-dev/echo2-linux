/* Connect the gateway's protected PCM FIFOs to a local LEIC1 room companion.
 * Neither idle room hardware nor any real PCM device is opened by this program. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define FRAME 640
static const char *mic_name = "room-mic.pcm", *speaker_name = "room-speaker.pcm";
static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }

static int64_t millis(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) abort();
    return ts.tv_sec * (int64_t)1000 + ts.tv_nsec / 1000000;
}

static bool allowed(const char *file)
{
    int fd = open(file, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return false;
    char v[3];
    ssize_t n = read(fd, v, sizeof v);
    close(fd);
    return n >= 1 && v[0] == '0' && (n == 1 || (n == 2 && v[1] == '\n'));
}

/* Only the locked, owner-only directory can contain these two endpoints. */
static int remove_fifo(int dir, const char *name)
{
    struct stat info;
    if (fstatat(dir, name, &info, AT_SYMLINK_NOFOLLOW))
        return errno == ENOENT ? 0 : -1;
    if (!S_ISFIFO(info.st_mode) || info.st_uid != geteuid()) { errno = EPERM; return -1; }
    return unlinkat(dir, name, 0);
}

static int connect_companion(const char *path)
{
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof address.sun_path) return -1;
    strcpy(address.sun_path, path);
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return -1;
    struct ucred peer;
    socklen_t size = sizeof peer;
    if (connect(fd, (struct sockaddr *)&address, sizeof address) ||
        getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) ||
        peer.uid != geteuid() || send(fd, "LEIC1", 5, MSG_NOSIGNAL) != 5) {
        close(fd); return -1;
    }
    return fd;
}

static void exchange(int input, int output, int room, const char *privacy, const char *dnd)
{
    struct pollfd fds[] = {{input, POLLIN, 0}, {room, POLLIN, 0}};
    unsigned char phone[FRAME], packet[FRAME + 1], silence[FRAME] = {0};
    size_t partial = 0;
    bool ready = false;
    int64_t start = millis(), last_phone = start, next_silence = start;
    while (running) {
        int64_t now = millis();
        if (!allowed(privacy) || !allowed(dnd) || now - last_phone > 1500 ||
            now - start > 600000 || (!ready && now - start > 2200)) return;
        // During the companion's bounded cue/Assist handshake, supply silence
        // for the gateway clock. No microphone/audio crosses before READY.
        if (!ready && now >= next_silence) {
            if (write(output, silence, sizeof silence) != FRAME) return;
            next_silence = now + 20;
        }
        int p = poll(fds, 2, 15);
        if (p < 0) { if (errno == EINTR) continue; return; }
        if ((fds[0].revents | fds[1].revents) & (POLLERR | POLLHUP | POLLNVAL)) return;
        if (!allowed(privacy) || !allowed(dnd)) return;
        if (fds[1].revents & POLLIN) {
            ssize_t n = recv(room, packet, sizeof packet, MSG_TRUNC);
            if (!ready) {
                if (n != 5 || memcmp(packet, "READY", 5)) return;
                ready = true;
            } else if (n != FRAME || write(output, packet, FRAME) != FRAME) return;
        }
        if (fds[0].revents & POLLIN) {
            ssize_t n = read(input, phone + partial, FRAME - partial);
            if (n <= 0) return;
            partial += (size_t)n;
            if (partial == FRAME) {
                last_phone = millis();
                if (ready && send(room, phone, FRAME, MSG_NOSIGNAL) != FRAME) return;
                partial = 0;
            }
        }
    }
}

static int serve(int dir, const char *room_socket, const char *privacy, const char *dnd)
{
    if (remove_fifo(dir, speaker_name) || remove_fifo(dir, mic_name) ||
        mkfifoat(dir, speaker_name, 0600) || mkfifoat(dir, mic_name, 0600)) return -1;
    int speaker = openat(dir, speaker_name, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (speaker < 0) return -1;
    int mic = -1;
    // A reader appears on the microphone FIFO only when the authenticated
    // gateway opens the room, after its own gates. Idle has no subscription.
    while (running) {
        mic = openat(dir, mic_name, O_WRONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (mic >= 0 || errno != ENXIO) break;
        poll(NULL, 0, 20);
    }
    if (mic >= 0 && running) {
        // Wait for the phone's first data to prove both FDs are open before
        // retiring their names. Later callers get a distinct pair of pipes.
        struct pollfd p = {speaker, POLLIN, 0};
        if (poll(&p, 1, 450) > 0 && (p.revents & POLLIN) &&
            !remove_fifo(dir, speaker_name) && !remove_fifo(dir, mic_name) &&
            allowed(privacy) && allowed(dnd)) {
            int room = connect_companion(room_socket);
            if (room >= 0) { exchange(speaker, mic, room, privacy, dnd); close(room); }
        }
    }
    if (mic >= 0) close(mic);
    close(speaker);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 5 && !(argc == 6 && !strcmp(argv[5], "--once"))) {
        fputs("Usage: echo2-intercom-bridge PRIVATE-FIFO-DIR COMPANION-SOCKET PRIVACY-FILE DND-FILE [--once]\n", stderr);
        return 2;
    }
    int dir = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat s;
    if (dir < 0 || fstat(dir, &s) || s.st_uid != geteuid() || (s.st_mode & 0077) ||
        flock(dir, LOCK_EX | LOCK_NB)) {
        fputs("A private directory owned by the service and unused by another bridge is required.\n", stderr);
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    int result;
    do { result = serve(dir, argv[2], argv[3], argv[4]); }
    while (running && !result && argc == 5);
    if (remove_fifo(dir, speaker_name) || remove_fifo(dir, mic_name)) result = -1;
    close(dir);
    return result != 0;
}
