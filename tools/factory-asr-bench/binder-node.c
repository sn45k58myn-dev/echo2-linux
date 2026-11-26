/* Allocate one private Binder node inside the throwaway ARM guest's binderfs. */
#include <stdint.h>
#include <stdio.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(void) {
    struct binderfs_device { char name[256]; uint32_t major, minor; };
    struct binderfs_device node = {.name = "binder"};
    int fd = open("/dev/binderfs/binder-control", O_RDONLY);
    if (fd < 0 || (ioctl(fd, _IOWR('b', 1, struct binderfs_device), &node) < 0 && errno != EEXIST)) {
        perror("guest binderfs");
        return 1;
    }
    close(fd);
    return symlink("/dev/binderfs/binder", "/dev/binder") < 0;
}
