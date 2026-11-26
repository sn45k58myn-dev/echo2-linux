/* A single authorized local transport for an already captured RAM window. */
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv) {
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (argc != 2 || strncmp(argv[1], "replay-", 7) ||
        strspn(argv[1]+7, "0123456789abcdef") != 16 || strlen(argv[1]) != 23) return 31;
    if (!getcwd(address.sun_path, sizeof address.sun_path) ||
        strlen(address.sun_path)+1+strlen(argv[1]) >= sizeof address.sun_path) return 32;
    strcat(address.sun_path, "/");
    strcat(address.sun_path, argv[1]);
    int srv = socket(AF_UNIX, SOCK_STREAM, 0);
    umask(077);
    if (srv < 0 || bind(srv, (struct sockaddr *)&address, sizeof address) || listen(srv, 1))
        return 33;
    puts("SOCKET_READY");
    fflush(stdout);
    struct pollfd pollfd = {.fd=srv, .events=POLLIN};
    int client = poll(&pollfd, 1, 15000) > 0 ? accept(srv, NULL, NULL) : -1;
    close(srv);
    unlink(address.sun_path);
    if (client < 0 || dup2(client, STDIN_FILENO)<0 || dup2(client, STDOUT_FILENO)<0)
        return 34;
    close(client);
    execl("/bin/sh", "sh", "session.sh", "stream", (char *)0);
    return 35;
}
