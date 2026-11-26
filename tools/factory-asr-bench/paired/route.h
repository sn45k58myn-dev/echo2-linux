/* Bounded diagnostic. The containing directory is a private root-owned tmpfs
 * directory. The normal intercom, other wake consumers and hardware capture
 * follow their original paths. Keep the original presentation sample clock. */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#ifndef FR_DIR
#define FR_DIR "/run/libreecho/groot-paired-diag"
#define FR_MUTE "/sys/devices/platform/amz_privacy/privacy_state"
#define FR_DND "/data/libreecho/config/do-not-disturb"
#endif
#define FR_DELAY 3840u
#define FR_SLOTS 128u
struct fr_packet { uint64_t first; int16_t samples[128]; };
static struct fr_packet fr_slots[FR_SLOTS];
static unsigned char fr_present[FR_SLOTS];
static int fr_sock=-1, fr_peer=-1, fr_active;
static uint64_t fr_sent,fr_missing,fr_tick,fr_last_source;
static uint64_t fr_ingress_end,fr_transport_gaps,fr_late,fr_cursor;
static uint64_t fr_pending_source;
static unsigned fr_pending_sent, fr_pending_missing;
static int fr_value(const char *path,const char *value,int optional){
    char b[24];int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0)return optional&&errno==ENOENT;
    ssize_t n=read(fd,b,sizeof(b)-1);close(fd);if(n<=0)return 0;
    if(b[n-1]=='\n')--n;
    b[n]=0;return strcmp(b,value)==0;
}
static int fr_can_route(void){
    struct stat s;time_t now=time(NULL);
    return access(FR_DIR "/armed",F_OK)==0 && lstat(FR_DIR "/lease",&s)==0 &&
        S_ISREG(s.st_mode)&&s.st_uid==0&&now>=s.st_mtime&&now-s.st_mtime<=2 &&
        fr_value(FR_MUTE,"0",0)&&fr_value(FR_DND,"off",1) &&
        access("/run/libreecho/intercom.lease",F_OK)!=0 &&
        access("/run/libreecho/intercom-assist.lease",F_OK)!=0 &&
        access("/data/libreecho/update/install.lock",F_OK)!=0 &&
        access("/data/libreecho/update/pending",F_OK)!=0;
}
static int fr_bind(void){
    struct stat s;
    if(lstat(FR_DIR,&s)||!S_ISDIR(s.st_mode)||s.st_uid||(s.st_mode&0777)!=0700)return -1;
    const char *path=FR_DIR "/factory-ha.sock";
    if(mkfifo(path,0600) && errno!=EEXIST)return -1;
    int fd=open(path,O_RDWR|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0)return -1;
    if(fstat(fd,&s)||!S_ISFIFO(s.st_mode)||s.st_uid||(s.st_mode&0777)!=0600){close(fd);return -1;}
    (void)fcntl(fd,F_SETPIPE_SZ,65536);
    return fd;
}
static void fr_report(uint64_t presentation){
    int out=open(FR_DIR "/factory-ha-counts.tmp",O_CREAT|O_TRUNC|O_WRONLY|O_NOFOLLOW|O_CLOEXEC,0600);
    if(out<0)return;
    dprintf(out,"{\"verified_peer\":%d,\"routed_samples\":%llu,\"gap_samples\":%llu,\"last_source_end\":%llu,\"last_presentation_end\":%llu,\"delay_samples\":%u,\"transport_gap_samples\":%llu,\"late_samples\":%llu,\"pipe_bytes\":%d}\n",
         fr_peer>=0,(unsigned long long)fr_sent,(unsigned long long)fr_missing,
         (unsigned long long)fr_last_source,(unsigned long long)presentation,FR_DELAY,
         (unsigned long long)fr_transport_gaps,(unsigned long long)fr_late,fcntl(fr_sock,F_GETPIPE_SZ));
    close(out);
    (void)rename(FR_DIR "/factory-ha-counts.tmp",FR_DIR "/factory-ha-counts");
}
static void fr_drain(int allow){
    if(fr_sock<0)fr_sock=fr_bind();
    if(fr_sock<0)return;
    for(int i=0;i<320;++i){
        struct fr_packet p;
        ssize_t n=read(fr_sock,&p,sizeof(p));
        if(n<=0)break;
        if(!allow||n!=sizeof(p)||p.first%128)continue;
        if(fr_ingress_end&&p.first>fr_ingress_end)fr_transport_gaps+=p.first-fr_ingress_end;
        fr_ingress_end=p.first+128;
        if(fr_cursor>p.first)fr_late+=fr_cursor>=p.first+128?128:fr_cursor-p.first;
        unsigned which=(unsigned)((p.first/128)%FR_SLOTS);
        fr_slots[which]=p;fr_present[which]=1;
    }
}
static int fr_frame(uint64_t first,int16_t *prepared,size_t count){
    int enabled=fr_peer>=0&&fr_can_route();
    if(enabled!=fr_active){
        memset(fr_present,0,sizeof(fr_present));memset(fr_slots,0,sizeof(fr_slots));fr_active=enabled;
        fr_ingress_end=fr_cursor=0;
        fr_drain(0);
    }
    fr_drain(enabled);
    if(!enabled)return 0;
    fr_pending_sent=fr_pending_missing=0;
    fr_pending_source=fr_last_source;
    memset(prepared,0,sizeof(int16_t)*count);
    for(size_t j=0;j<count;j++){
        if(first+j<FR_DELAY)continue;
        uint64_t at=first+j-FR_DELAY;
        unsigned k=(unsigned)((at/128)%FR_SLOTS);
        if(fr_present[k]&&fr_slots[k].first==at-at%128){
            prepared[j]=fr_slots[k].samples[at%128];
            fr_slots[k].samples[at%128]=0;
            ++fr_pending_sent;fr_pending_source=at+1;
        }else{++fr_pending_missing;}
    }
    fr_cursor=first+count>=FR_DELAY?first+count-FR_DELAY:0;
    return 1;
}
static void fr_delivered(uint64_t presentation){
    fr_sent+=fr_pending_sent;fr_missing+=fr_pending_missing;
    fr_last_source=fr_pending_source;
    if(fr_tick++%10==0)fr_report(presentation);
}
