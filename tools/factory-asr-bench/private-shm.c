/* The standalone speech worker has no shared memory peer. Report ENOSYS as
 * ordinary SysV callers expect when this optional transport is unavailable. */
typedef unsigned int size_t;
extern int *__errno(void);

static int unavailable(void) {
    *__errno() = 38;
    return -1;
}
int shmget(int key, size_t size, int flags) {
    (void)key; (void)size; (void)flags;
    return unavailable();
}
void *shmat(int id, const void *addr, int flags) {
    (void)id; (void)addr; (void)flags;
    unavailable();
    return (void *)-1;
}
int shmctl(int id, int cmd, void *buf) {
    (void)id; (void)cmd; (void)buf;
    return unavailable();
}
int shmdt(const void *addr) {
    (void)addr;
    return unavailable();
}
int shm_open(const char *name, int flags, unsigned int mode) {
    (void)name; (void)flags; (void)mode;
    return unavailable();
}
int shm_unlink(const char *name) {
    (void)name;
    return unavailable();
}
int shm_get_size(int fd) {
    (void)fd;
    return unavailable();
}
int shm_set_size(int fd, int size) {
    (void)fd; (void)size;
    return unavailable();
}
int get_file_id(int fd) {
    (void)fd;
    return unavailable();
}
