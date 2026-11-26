"""Apply the diagnostic Assist branch to a temporary mirrored 0.16.8 UI source tree."""
from pathlib import Path
import shutil
import sys

def replace_once(text, old, new):
    if text.count(old)!=1:
        raise ValueError(f"unexpected target: {old[:60]}")
    return text.replace(old,new,1)

ui=Path(sys.argv[1])
out=Path(__file__).resolve().parent
w=ui/"src/adapter/waked.c"
source=w.read_text()
source=replace_once(source,"static void close_audio_subscriber(", '#include "route.h"\n\nstatic void close_audio_subscriber(')
source=replace_once(source,"    if (fd == ipc->intercom_fd) {","    if (fd == fr_peer) fr_peer=-1;\n    if (fd == ipc->intercom_fd) {")
source=replace_once(source,"            start_intercom_aec(ipc, client_fd,",'''            if (intercom_peer(client_fd,"/usr/local/sbin/libreecho-esphomed",0))
                fr_peer=client_fd;
            start_intercom_aec(ipc, client_fd,''')
anchor="    size_t i;\n\n    for (i = 0; i < MAX_AUDIO_SUBSCRIBERS; ++i) {\n        int fd = ipc->audio_subscribers[i];"
source=replace_once(source,anchor,'''    size_t i;
    int16_t factory[160];
    int use_factory=count==160 && fr_frame(first_sample,factory,count);

    for (i = 0; i < MAX_AUDIO_SUBSCRIBERS; ++i) {
        int fd = ipc->audio_subscribers[i];''')
source=replace_once(source,"fd == ipc->intercom_fd ? intercom_samples : samples,",
                  "fd == ipc->intercom_fd ? intercom_samples : (use_factory && fd == fr_peer ? factory : samples),")
anchor="            close_audio_subscriber(ipc, i);\n    }\n}\n\nstatic void handle_control_client"
source=replace_once(source,anchor,"            close_audio_subscriber(ipc, i);\n        else if (use_factory && fd == fr_peer)\n            fr_delivered(first_sample + count);\n    }\n}\n\nstatic void handle_control_client")
w.write_text(source)
shutil.copyfile(out/"route.h",w.with_name("route.h"))
