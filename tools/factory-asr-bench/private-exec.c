/* Start a private rooted diagnostic as an unprivileged user. */
#define _GNU_SOURCE
#include <grp.h>
#include <stddef.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <sys/resource.h>

int main(int argc, char **argv) {
    const struct rlimit no_core = {0, 0};
    if (argc < 3 || chroot(argv[1]) || chdir("/") || setgroups(0, NULL) ||
        setgid(65534) || setuid(65534) || setrlimit(RLIMIT_CORE, &no_core) ||
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) return 121;
    if (clearenv() || setenv("ANDROID_ROOT", "/system", 1) ||
        setenv("LD_LIBRARY_PATH", "/system/lib:/system/vendor/lib", 1)) return 122;
    execv(argv[2], argv+2);
    return 123;
}
