/* Receive Android audio diagnostics inside the isolated synthetic test guest. */
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(void) {
    int fd = socket(AF_UNIX, SOCK_DGRAM, 0);
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    strcpy(address.sun_path, "/dev/socket/logdw");
    if (fd < 0 || bind(fd, (struct sockaddr *)&address, sizeof address))
        return 1;
    unsigned char packet[8192];
    while (1) {
        ssize_t bytes = recv(fd, packet, sizeof packet - 1, 0);
        if (bytes < 1) return 1;
        /* logger_header_t on this build has an 11-byte packed header. */
        if (bytes < 13) continue;
        packet[bytes] = 0;
        unsigned priority = packet[11];
        if (priority < 4 || priority > 7) continue;
        char *tag = (char *)packet + 12;
        char *end = memchr(tag, 0, bytes - 12);
        if (end && end + 1 < (char *)packet + bytes) {
            fprintf(stderr, "stock[%s] %s\n", tag, end + 1);
            fflush(stderr);
        }
    }
}
