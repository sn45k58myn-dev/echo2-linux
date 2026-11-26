/* One-shot acoustic comparison on Radar. Original capture goes to micd.
 * A second, sample-indexed copy is available only to the local indicated
 * test via its private pipe. No audio files or network listeners. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define DIR "/run/libreecho/groot-paired-diag"
#define MIRROR "/run/libreecho-audio/prebeam-ref.sock"
#define MUTE "/sys/devices/platform/amz_privacy/privacy_state"
#define DND "/data/libreecho/config/do-not-disturb"
#define FRAMES 128u
#define FRAME_BYTES 27u
#define MAX_SECONDS 75u
#define TRANSFER_GRACE 48u
struct Raw {
    uint32_t magic, count;
    uint64_t first, device_ns;
    unsigned char audio[FRAMES * FRAME_BYTES];
};
_Static_assert(sizeof(struct Raw) == 3480, "raw wire format");
static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static int is_value(const char *path, const char *value, int optional) {
    char buf[16] = {0};
    int fd = open(path, O_RDONLY|O_CLOEXEC);
    if (fd == -1) return optional && errno == ENOENT;
    ssize_t n = read(fd, buf, sizeof(buf)-1);
    close(fd);
    if (n < 1 || n >= (ssize_t)sizeof(buf)-1) return 0;
    if (buf[n-1] == '\n') buf[n-1] = 0;
    return strcmp(buf,value)==0;
}
static int guard(void) {
    struct stat st;
    return is_value(MUTE,"0",0) && is_value(DND,"off",1)
        && lstat(DIR "/lease",&st)==0 && S_ISREG(st.st_mode) && st.st_uid==0
        && time(NULL) >= st.st_mtime && time(NULL)-st.st_mtime <= 2
        && access("/run/libreecho/intercom.lease",F_OK)!=0
        && access("/run/libreecho/intercom-assist.lease",F_OK)!=0
        && access("/data/libreecho/update/install.lock",F_OK)!=0;
}
static int send_all(int fd, const void *data, size_t len) {
    const char *p = data;
    while (len) {
        ssize_t n = write(fd,p,len);
        if (n<0 && errno==EINTR) continue;
        if (n<=0) return -1;
        p+=n; len-=(size_t)n;
    }
    return 0;
}
static void report(const char *cause,size_t received,size_t sent,size_t high_water) {
    int fd=open(DIR "/result",O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd>=0) {
        dprintf(fd,"reason=%s captured=%u sent=%u max_queued=%u\n",
                cause,(unsigned)received,(unsigned)sent,(unsigned)high_water);
        close(fd);
    }
}
static unsigned goal_blocks(void) {
    unsigned seconds=0;
    FILE *fp=fopen(DIR "/armed","r");
    if(!fp)return 0;
    int ok=fscanf(fp,"%u",&seconds)==1 && seconds>=4 && seconds<=60;
    fclose(fp);
    return ok ? seconds*125u : 0;
}
static int reference(void) {
    struct sockaddr_un sa={.sun_family=AF_UNIX};
    struct stat st;
    if (!guard() || !lstat(MIRROR,&st) || errno!=ENOENT) return 2;
    int s=socket(AF_UNIX,SOCK_DGRAM|SOCK_CLOEXEC,0), capacity=262144;
    if (s<0) return 2;
    strcpy(sa.sun_path,MIRROR);
    (void)setsockopt(s,SOL_SOCKET,SO_RCVBUF,&capacity,sizeof(capacity));
    if (bind(s,(struct sockaddr *)&sa,sizeof(sa))) {close(s);return 2;}
    int result=0;
    unsigned char data[40+4096];
    const size_t capacity_bytes=12*1024*1024;
    unsigned char *queue=malloc(capacity_bytes);
    int flags=fcntl(STDOUT_FILENO,F_GETFL);
    if(!queue || flags<0 || fcntl(STDOUT_FILENO,F_SETFL,flags|O_NONBLOCK)) {
        free(queue);close(s);unlink(MIRROR);return 2;
    }
    size_t used=0,sent=0;
    uint64_t begin=now_ns(), stop_collecting_at=0;
    while (now_ns()-begin < (MAX_SECONDS+TRANSFER_GRACE)*1000000000ULL && guard()) {
        uint64_t now=now_ns();
        if(!stop_collecting_at && access(DIR "/raw-collected",F_OK)==0)
            stop_collecting_at=now+160000000;
        int collecting=now-begin < MAX_SECONDS*1000000000ULL &&
            (!stop_collecting_at || now<stop_collecting_at);
        if(!collecting && used==sent) break;
        struct pollfd fds[2]={{s, collecting?POLLIN:0,0},{STDOUT_FILENO,used>sent?POLLOUT:0,0}};
        if(poll(fds,2,40)<=0) continue;
        if(fds[0].revents&POLLIN) {
            for(unsigned i=0;i<32;i++) {
                ssize_t n=recv(s,data,sizeof(data),MSG_TRUNC|MSG_DONTWAIT);
                if(n<0 && (errno==EAGAIN||errno==EINTR)) break;
                if(n<40 || (size_t)n>sizeof(data) || used+(size_t)n>capacity_bytes) {result=2;break;}
                memcpy(queue+used,data,(size_t)n);used+=(size_t)n;
            }
        }
        if(result || !guard())break;
        if(fds[1].revents&(POLLERR|POLLHUP))break;
        if(used>sent) {
            ssize_t n=write(STDOUT_FILENO,queue+sent,used-sent);
            if(n<0 && errno!=EAGAIN&&errno!=EINTR){result=1;break;}
            if(n>0){memset(queue+sent,0,(size_t)n);sent+=(size_t)n;}
        }
    }
    explicit_bzero(queue+sent,used-sent);free(queue);
    close(s);unlink(MIRROR);return result;
}
static void stop_indicator(void) {
    struct sockaddr_un sa={.sun_family=AF_UNIX};
    const char command[]="{\"v\":1,\"id\":1,\"cmd\":\"pattern\",\"args\":{\"name\":\"stop\",\"owner\":\"paired-acoustic-test\"}}\n";
    strcpy(sa.sun_path,"/run/libreecho/led.sock");
    int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
    if(fd>=0) {
        if(!connect(fd,(struct sockaddr *)&sa,sizeof(sa))) (void)send_all(fd,command,sizeof(command)-1);
        close(fd);
    }
}
int main(int argc,char **argv) {
    struct stat st;
    if (lstat(DIR,&st) || !S_ISDIR(st.st_mode) || (st.st_mode&0777)!=0700 || st.st_uid!=0) return 2;
    signal(SIGPIPE,SIG_IGN);
    if (argc==2 && strcmp(argv[1],"--clock")==0) {printf("%llu\n",(unsigned long long)now_ns());return 0;}
    if (argc==2 && strcmp(argv[1],"--stop-indicator")==0) {stop_indicator();return 0;}
    if (argc==2 && strcmp(argv[1],"--reference")==0) return reference();
    if (argc<2 || strcmp(argv[1],"--")) return 2;
    int p[2];
    if (pipe2(p,O_CLOEXEC)) return 2;
    pid_t parent=getpid(), child=fork();
    if (child==0) {
        prctl(PR_SET_PDEATHSIG,SIGTERM);
        if (getppid()!=parent || dup2(p[1],STDOUT_FILENO)<0) _exit(1);
        close(p[0]);close(p[1]);argv[0]="/sbin/tinycap";
        execv(argv[0],argv);_exit(1);
    }
    if (child<0) return 2;
    close(p[1]);
    struct Raw rec={.magic=0x31574152,.count=FRAMES};
    unsigned char data[FRAMES*FRAME_BYTES];
    size_t fill=0;
    uint64_t until=0;
    int out=-1, spent=0;
    struct Raw *queue=NULL;
    size_t captured=0,sent=0,goal=0,high_water=0;
    const char *reason=NULL;
    for (;;) {
        ssize_t n=read(p[0],data,sizeof(data));
        if(n<0 && errno==EINTR) continue;
        if(n<=0 || send_all(STDOUT_FILENO,data,(size_t)n)) break;
        const unsigned char *s=data;
        size_t available=(size_t)n;
        while(available) {
            size_t take=sizeof(rec.audio)-fill;
            if(take>available) take=available;
            memcpy(rec.audio+fill,s,take);
            fill+=take;s+=take;available-=take;
            if(fill!=sizeof(rec.audio)) continue;
            fill=0;rec.device_ns=now_ns();
            if(!spent && access(DIR "/armed",F_OK)==0 && guard()) {
                out=open(DIR "/raw",O_WRONLY|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
                if(out>=0) {
                    spent=1;goal=goal_blocks();
                    until=rec.device_ns+(goal*8000000ULL)+TRANSFER_GRACE*1000000000ULL;
                    if(fstat(out,&st) || !S_ISFIFO(st.st_mode) || st.st_uid!=0) {close(out);out=-1;}
                    else if(!goal || !(queue=calloc(goal,sizeof(*queue)))) {
                        report("array-allocation-or-duration",0,0,0);close(out);out=-1;
                    } else (void)fcntl(out,F_SETPIPE_SZ,262144);
                }
            }
            if(out>=0) {
                if(rec.device_ns>=until) reason="transfer-deadline";
                else if(!guard()) reason="privacy-or-lease";
                if(!reason && captured<goal) {
                    queue[captured++]=rec;
                    if(captured-sent>high_water)high_water=captured-sent;
                    if(captured==goal) {
                        int endfd=open(DIR "/raw-collected",O_CREAT|O_WRONLY|O_NOFOLLOW|O_CLOEXEC,0600);
                        if(endfd>=0)close(endfd);
                    }
                }
                /* Drain at most 32 records per 8 ms tick. EAGAIN is normal
                 * under Wi-Fi jitter and must never block micd or drop data. */
                for(unsigned j=0;!reason && sent<captured && j<32;j++) {
                    ssize_t w=write(out,queue+sent,sizeof(rec));
                    if(w<0 && (errno==EAGAIN||errno==EWOULDBLOCK||errno==EINTR)) break;
                    if(w!=(ssize_t)sizeof(rec)) {reason="reader-disconnected";break;}
                    memset(queue+sent,0,sizeof(rec));sent++;
                }
                if(sent==goal) reason="complete";
                if(reason) {
                    report(reason,captured,sent,high_water);
                    explicit_bzero(queue+sent,(captured-sent)*sizeof(rec));free(queue);queue=NULL;
                    close(out);out=-1;
                }
            }
            rec.first+=FRAMES;
        }
    }
    if(out>=0) {report("codec-ended",captured,sent,high_water);close(out);}
    if(queue) {explicit_bzero(queue+sent,(captured-sent)*sizeof(rec));free(queue);}
    close(p[0]);kill(child,SIGTERM);while(waitpid(child,NULL,0)<0 && errno==EINTR){}
    return 0;
}
