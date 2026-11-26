/* Supply the inherited local socket normally opened by the Android init service. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(void) {
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    strcpy(addr.sun_path, "/dev/socket/dbus");
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        perror("dbus socket");
        return 1;
    }
    char number[16];
    snprintf(number, sizeof number, "%d", fd);
    if (setenv("ANDROID_SOCKET_dbus", number, 1) < 0) return 1;
    execl("/system/bin/dbus-daemon", "dbus-daemon", "--nofork",
          "--config-file=/system/etc/dbus.conf", (char *)0);
    perror("exec dbus-daemon");
    return 1;
}
