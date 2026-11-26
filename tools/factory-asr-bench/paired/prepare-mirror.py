"""Apply the temporary render-reference mirror to a fresh, pinned 0.16.8 tree."""
from pathlib import Path
import sys

src = Path(sys.argv[1]) / "src" / "adapter"


def replace(path, before, after):
    content = path.read_text()
    if content.count(before) != 1:
        raise ValueError(f"unexpected 0.16.8 source at {path}: {before}")
    path.write_text(content.replace(before, after))


header = src / "voice_reference.h"
replace(header, "#include <stdint.h>", "#include <stdint.h>\n#include <sys/un.h>")
replace(header, "    char socket_path[108];", """    char socket_path[108];
    struct sockaddr_un mirror;
    size_t mirror_address_bytes;
    unsigned int mirror_delivered;
    unsigned int mirror_dropped;""")
replace(header, "int le_voice_reference_drain(", """int le_voice_reference_mirror_to(struct le_voice_reference *reference,
                                const char *path);
int le_voice_reference_drain(""")

source = src / "voice_reference.c"
replace(source, "int le_voice_reference_drain(", """int le_voice_reference_mirror_to(struct le_voice_reference *reference,
                                const char *path)
{
    if (!reference || reference->fd < 0 || !path || !path[0] ||
        strlen(path) >= sizeof(reference->mirror.sun_path) ||
        !strcmp(path, reference->socket_path)) {
        errno = EINVAL;
        return -1;
    }
    reference->mirror.sun_family = AF_UNIX;
    strcpy(reference->mirror.sun_path, path);
    reference->mirror_address_bytes = offsetof(struct sockaddr_un, sun_path)
                                    + strlen(path) + 1U;
    return 0;
}

int le_voice_reference_drain(""")
replace(source, """        if (process_packet(reference, &packet, (size_t)received) == 0)
            ++processed;""", """        if (process_packet(reference, &packet, (size_t)received) == 0) {
            ++processed;
            /* Mirror only validated render PCM. Keep normal capture running
             * when the optional microphone-side consumer is absent or slow. */
            if (reference->mirror_address_bytes) {
                ssize_t n = sendto(reference->fd, &packet, (size_t)received,
                    MSG_DONTWAIT | MSG_NOSIGNAL,
                    (const struct sockaddr *)&reference->mirror,
                    (socklen_t)reference->mirror_address_bytes);
                if (n == received)
                    ++reference->mirror_delivered;
                else
                    ++reference->mirror_dropped;
            }
        }""")

waked = src / "waked.c"
replace(waked, "    if (le_voice_aec_init(&aec,", """    if (le_voice_reference_mirror_to(&reference,
            "/run/libreecho-audio/prebeam-ref.sock") < 0) {
        perror("waked: prebeam render mirror");
        goto out;
    }
    if (le_voice_aec_init(&aec,""")
