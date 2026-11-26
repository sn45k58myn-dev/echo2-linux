// On-device diagnostic router. Audio is confined to local sockets, pipes and bounded RAM.
// The production capture mux is the only ALSA reader and forwards its original bytes to micd.
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <mutex>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include "real_return.h"
#include <vector>

using u64 = uint64_t;
using u32 = uint32_t;
using u16 = uint16_t;
using u8 = uint8_t;
const char* stage="/run/libreecho/groot-paired-diag";
const char* mirror="/run/libreecho-audio/prebeam-ref.sock";
struct Raw {u32 magic,n; u64 first,ns; u8 pcm[3456];};
struct Audio {u32 magic;u16 version, flags;u64 first;u32 n,res;};
struct Render {u32 magic;u16 version, size;u32 seq,rate;u16 channels,n;u32 mask;u64 first,ns;};
struct Factory {u64 first,ns;u32 flags,n;};
struct Score {char magic[4];u16 version,flags;u64 first,ns,res;};
static_assert(sizeof(Raw)==3480 && sizeof(Audio)==24 && sizeof(Render)==40 && sizeof(Factory)==24 && sizeof(Score)==32,"protocol");
[[noreturn]] void fail(const char* s){ dprintf(2,"router error: %s (%d)\n",s,errno);_exit(31);}
void need(bool condition,const char* s){if(!condition)fail(s);}
void exact(int fd,void* buf,size_t n,bool out=false){
    char* p=(char*)buf;
    while(n){ssize_t count=out?write(fd,p,n):read(fd,p,n);
        if(count<0&&errno==EINTR)continue;
        need(count>0,"closed pipe or socket");n-=count;p+=count;}
}
void line(int fd,char* s,size_t cap){for(size_t i=0;i+1<cap;i++){exact(fd,s+i,1);if(s[i]=='\n'){s[i+1]=0;return;}}fail("oversized reply");}
std::string path(const char* s){return std::string(stage)+"/"+s;}
sockaddr_un address(const char* p){sockaddr_un a{}; a.sun_family=AF_UNIX;need(strlen(p)<sizeof(a.sun_path),"path");strcpy(a.sun_path,p);return a;}
int open_baseline(){int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);auto a=address("/run/libreecho/wakeword.sock");
    need(fd>=0&&!connect(fd,(sockaddr*)&a,sizeof(a)),"wake socket");
    char msg[]="{\"v\":1,\"id\":1,\"cmd\":\"stream_audio\",\"args\":{}}\n";
    exact(fd,msg,sizeof(msg)-1,true);char reply[4096];line(fd,reply,sizeof(reply));
    std::string s;for(const char* p=reply;*p;p++)if(*p!=' '&&*p!='\t')s+=*p;
    need(s.find("\"ok\":true")!=s.npos&&s.find("\"sample_indexed\":true")!=s.npos,"indexed stream refused");return fd;
}
int open_render(){
    int fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_CLOEXEC,0);int capacity=262144;auto a=address(mirror);
    need(fd>=0&&!setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&capacity,sizeof(capacity)),"render socket");
    need(!bind(fd,(sockaddr*)&a,sizeof(a)),"mirror in use");struct stat st{};
    need(!lstat(mirror,&st)&&S_ISSOCK(st.st_mode),"mirror identity");
    int out=open(path("mirror-id").c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    need(out>=0,"mirror ownership record");dprintf(out,"%llu:%llu:%u\n",(unsigned long long)st.st_dev,(unsigned long long)st.st_ino,st.st_uid);close(out);return fd;
}
template<size_t N> struct Ring{
    std::array<int16_t,N> data{}; u64 tail=0,head=0;bool started=false;
    void add(u64 start,const int16_t* pcm,unsigned n){if(!started){tail=head=start;started=true;}need(head==start,"audio sample discontinuity");
        for(unsigned i=0;i<n;i++)data[(head++)%N]=pcm[i];
        if(head-tail>N)tail=head-N;}
    bool contains(u64 index,unsigned n)const{return started&&index>=tail&&index<=head&&n<=head-index;}
};
struct Store {
    std::mutex m;std::condition_variable cv;Ring<960000> baseline;Ring<2880000> ref;
    u64 ref0=0,ref0_ns=0;bool has_ref=false;
} store;
void wake_reader(int fd){for(;;){Audio h;exact(fd,&h,sizeof(h));
    need(h.magic==0x3153564c&&h.version==1&&h.n==160&&!h.flags&&!h.res,"bad wake header");
    int16_t b[160];exact(fd,b,sizeof(b));{std::lock_guard<std::mutex> l(store.m);store.baseline.add(h.first,b,160);}store.cv.notify_all();}}
void render_reader(int fd){char packet[40+4096];u32 seq=0;bool first=true;
    for(;;){ssize_t bytes=recv(fd,packet,sizeof(packet),MSG_TRUNC);if(bytes<0&&errno==EINTR)continue;
        need(bytes>=40&&(size_t)bytes<=sizeof(packet),"truncated render");Render h;memcpy(&h,packet,40);
        need(h.magic==0x5241454c&&h.version==1&&h.size==40&&h.rate==48000&&h.channels==1&&
            h.n>0&&h.n<=2048&&bytes==40+2*h.n,"unexpected post-render format");
        need(first||h.seq==seq+1,"render sequence gap");seq=h.seq;first=false;
        int16_t b[2048];memcpy(b,packet+40,2*h.n);
        {std::lock_guard<std::mutex> l(store.m);
         // A track switch or other stream_index discontinuity invalidates this comparison.
         store.ref.add(h.first,b,h.n);if(!store.has_ref){store.ref0=h.first;store.ref0_ns=h.ns;store.has_ref=true;}}
        store.cv.notify_all();}}
struct Child {int in, out;pid_t pid;};
Child launch(const char* dir,const char* const* argv){int a[2],b[2];need(!pipe2(a,O_CLOEXEC)&&!pipe2(b,O_CLOEXEC),"worker pipes");
    pid_t parent=getpid(),pid=fork();need(pid>=0,"fork");
    if(!pid){prctl(PR_SET_PDEATHSIG,SIGTERM);if(getppid()!=parent||chdir(dir)||dup2(a[0],0)<0||dup2(b[1],1)<0)_exit(81);
        execv(argv[0],(char*const*)argv);_exit(82);}
    close(a[0]);close(b[1]);return {a[1],b[0],pid};}
struct Calibration {
    int64_t offset=0;
    unsigned matches=0;
    u64 latest=0;
    unsigned resets=0;
    bool locked()const{return matches>=2;}
    void find(const Raw& r,unsigned block){
        if(locked() || block%8 || store.ref.head<=store.ref.tail+384)return;
        // Look around this mic block's original clock timestamp even if another
        // consumer fell behind. The independent DAC index survives that backlog.
        long double predicted=(long double)store.ref0+((long double)r.ns-8000000-store.ref0_ns)*48000/1000000000;
        u64 begin=(u64)std::max<long double>(store.ref.tail,std::min<long double>(store.ref.head,predicted-16000));
        u64 stop=(u64)std::max<long double>(begin,std::min<long double>(store.ref.head,predicted+16000));
        if(stop-begin<384)return;
        std::vector<int16_t> v(stop-begin);
        for(size_t i=0;i<v.size();i++)v[i]=store.ref.data[(begin+i)%store.ref.data.size()];
        int p=rr_find(r.pcm,128,v.data(),nullptr,v.size());
        if(p<0)return;
        int64_t candidate=(int64_t)(begin+p)-(int64_t)(3*r.first);
        matches=matches && r.first>=latest+128 && candidate==offset ? matches+1 : 1;
        offset=candidate;latest=r.first;
        if(locked())dprintf(2,"router measured_offset48=%lld proven_blocks=2 sample=%llu\n",(long long)offset,(unsigned long long)latest);
    }
    bool valid(const Raw& r,u64 at) {
        unsigned bad=0;
        for(unsigned i=0;i<128;i++){
            const auto* p=r.pcm+27*i;int actual=rr_board_sample(p);
            int want=store.ref.data[(at+3*i)%store.ref.data.size()];
            if(p[21]||p[24]||p[25]||abs(actual-want)>1||(actual!=want&&p[26]!=128))++bad;
        }
        if(bad>2){
            matches=0;++resets;
            dprintf(2,"router lost_measured_alignment mismatches=%u sample=%llu\n",bad,(unsigned long long)r.first);
            return false;
        }
        return true;
    }
};
int main(int argc,char** argv){
    if(argc!=5&&(argc!=6||strcmp(argv[5],"factory-only"))){dprintf(2,"router <factory-ram-dir> <model-dir> <shadow-worker> <hops> [factory-only]\n");return 2;}
    char* end;long hops=strtol(argv[4],&end,10);need(*end==0&&hops>=6&&hops<=725,"hops");
    umask(077);alarm(135);rlimit limits{0,0};need(!setrlimit(RLIMIT_CORE,&limits)&&!prctl(PR_SET_DUMPABLE,0),"core limit");signal(SIGPIPE,SIG_IGN);
    const char* fa[]={"/bin/sh","session.sh","stream",nullptr};const char* sc[]={argv[3],argv[2],argc==6?"factory-only":"live",argv[4],nullptr};
    auto factory=launch(argv[1],fa),score=launch("/",sc);
    int assist_fd=-1;
    u64 assist_ok=0,assist_full=0,assist_open_wait=0;
    struct AssistBlock {u64 first; int16_t pcm[128];};
    static_assert(sizeof(AssistBlock)==264,"Assist wire");
    char text[4096],ready[8];exact(factory.out,ready,sizeof(ready));need(!memcmp(ready,"E2ASRDY\n",8),"native factory readiness");
    line(score.out,text,sizeof(text));need(strstr(text,"\"ready\":true")!=nullptr,"native score readiness");
    int baseline=open_baseline(),ref=open_render();
    int raw=open(path("raw").c_str(),O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);struct stat st{};
    need(raw>=0&&!fstat(raw,&st)&&S_ISFIFO(st.st_mode)&&st.st_uid==0,"private raw pipe");
    std::thread(wake_reader,baseline).detach();std::thread(render_reader,ref).detach();
    std::thread output([&]{for(long i=0;i<hops;i++){char row[1024];line(score.out,row,sizeof(row));need(strstr(row,"\"end_sample\":")!=nullptr,"bad score");exact(1,row,strlen(row),true);}});
    exact(1,text,strlen(text),true);
    pollfd waitraw{raw,POLLIN,0};
    need(poll(&waitraw,1,30000)>0&&(waitraw.revents&POLLIN),"first guarded capture unavailable");
    need(fcntl(raw,F_SETFL,fcntl(raw,F_GETFL)&~O_NONBLOCK)==0,"blocking raw reader");
    FILE* armed=fopen(path("armed").c_str(),"r");unsigned seconds=0;
    need(armed&&fscanf(armed,"%u",&seconds)==1&&seconds>=1&&seconds<=60,"armed duration");
    fclose(armed);u64 received_frames=0;
    struct Pending {Score h{};std::array<int16_t,1280> factory{};};
    std::vector<Pending> queue(static_cast<size_t>(hops));
    std::mutex pending_lock;std::condition_variable pending_cv;u64 queued=0;
    std::thread deliver([&]{
        for(long j=0;j<hops;j++){
            {std::unique_lock<std::mutex> l(pending_lock);pending_cv.wait(l,[&]{return queued>(u64)j;});}
            auto &item=queue[j];u64 first=item.h.first;
            std::array<int16_t,1280> stock{};
            {std::unique_lock<std::mutex> l(store.m);auto t=std::chrono::steady_clock::now();
             bool arrived=store.cv.wait_for(l,std::chrono::milliseconds(2200),[&]{return store.baseline.head>=first+1280;});
             auto waited=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t).count();
             bool available=arrived&&store.baseline.contains(first,1280);
             if(!available||j<2||waited>=160)
                 dprintf(2,"router baseline_%s hop=%ld first=%llu wake_tail=%llu wake_head=%llu wait_ms=%lld\n",
                    available?"paired":"unavailable",j,(unsigned long long)first,
                    (unsigned long long)store.baseline.tail,(unsigned long long)store.baseline.head,(long long)waited);
             need(available,"indexed stock baseline unavailable");
             for(unsigned i=0;i<1280;i++)stock[i]=store.baseline.data[(first+i)%store.baseline.data.size()];}
            exact(score.in,&item.h,sizeof(item.h),true);exact(score.in,stock.data(),sizeof(stock),true);
            exact(score.in,item.factory.data(),sizeof(item.factory),true);item.factory.fill(0);
        }
        close(score.in);
    });
    std::array<int16_t,1280>f{};unsigned blocks=0;u64 origin=0,cap0=0;
    u64 next=0,ref_count=0,lock_count=0;bool aligned=false;Calibration calibration;
    u16 flags=3;u64 nblocks=0;bool have_raw=false;
    while(blocks<(unsigned)hops*10){
        Raw r;exact(raw,&r,sizeof(r));++received_frames;
        need(r.magic==0x31574152&&r.n==128&&r.ns>8000000&&(!have_raw||r.first==next),"bad raw sequence");have_raw=true;next=r.first+128;
        if(!aligned){
            std::unique_lock<std::mutex> l(store.m);
            store.cv.wait_for(l,std::chrono::milliseconds(70),[&]{return store.baseline.started;});
            if(!store.baseline.started||store.baseline.tail>r.first)continue;
            origin=r.first;cap0=r.ns-8000000;aligned=true;
            dprintf(2,"router start_sample=%llu capture_start_bound_ns=%llu wake_tail=%llu wake_head=%llu\n",
                    (unsigned long long)origin,(unsigned long long)cap0,
                    (unsigned long long)store.baseline.tail,(unsigned long long)store.baseline.head);}
        int16_t reference[768]{};u32 ff=0;
        {std::unique_lock<std::mutex> l(store.m);
            calibration.find(r,blocks);
            if(calibration.locked()){
                int64_t absolute=(int64_t)(3*r.first)+calibration.offset;
                if(absolute>=0){
                    u64 measured=(u64)absolute, pos=measured+384;
                    store.cv.wait_for(l,std::chrono::milliseconds(40),[&]{return store.ref.head>=pos+384;});
                    if(store.ref.contains(measured,768)&&calibration.valid(r,measured)){
                        // Use only independently received, already-rendered samples.
                        for(unsigned i=0;i<384;i++)reference[2*i]=reference[2*i+1]=store.ref.data[(pos+i)%store.ref.data.size()];
                        rr_normalize(r.pcm,128);ff=2;++ref_count;
                    }
                }
            }}
        Factory h{r.first,r.ns,ff,128};exact(factory.in,&h,sizeof(h),true);exact(factory.in,r.pcm,sizeof(r.pcm),true);
        if(ff)exact(factory.in,reference,sizeof(reference),true);
        Factory result;exact(factory.out,&result,sizeof(result));need(result.first==h.first&&result.ns==h.ns&&result.n==128&&
            (result.flags==ff||(ff==2&&result.flags==3)),"ASP timing/source");
        unsigned offset=128*(blocks%10);exact(factory.out,f.data()+offset,256);
        AssistBlock ab{};ab.first=r.first;
        for(unsigned i=0;i<128;i++)ab.pcm[i]=static_cast<int16_t>(static_cast<uint16_t>(f[offset+i])&0xfffeU);
        // A slow Assist client must not delay reference synchronization or intercom.
        if(assist_fd<0) {
            assist_fd=open("/run/libreecho/groot-paired-diag/factory-ha.sock",O_WRONLY|O_CLOEXEC|O_NONBLOCK|O_NOFOLLOW);
            if(assist_fd>=0) {
                struct stat info{};
                need(!fstat(assist_fd,&info)&&S_ISFIFO(info.st_mode)&&info.st_uid==0&&(info.st_mode&0777)==0600,"invalid Assist FIFO");
            }
        }
        if(assist_fd<0)++assist_open_wait;
        else{
            ssize_t written;
            do{written=write(assist_fd,&ab,sizeof(ab));}while(written<0&&errno==EINTR);
            if(written==sizeof(ab))++assist_ok;
            else if(written<0&&(errno==EAGAIN||errno==EWOULDBLOCK))++assist_full;
            else{close(assist_fd);assist_fd=-1;++assist_open_wait;}
        }
        flags&=result.flags;if(result.flags==3)++lock_count;
        if(++blocks%10)continue;
        u64 first=r.first+128-1280;
        timespec ts{};need(!clock_gettime(CLOCK_MONOTONIC,&ts),"factory timestamp");
        u64 done=(u64)ts.tv_sec*1000000000+ts.tv_nsec;
        auto &item=queue[nblocks];item.h=Score{{'E','2','S','W'},1,flags,first,r.ns,done};item.factory=f;
        {std::lock_guard<std::mutex> l(pending_lock);queued=++nblocks;}pending_cv.notify_one();flags=3;
    }
    if(assist_fd>=0)close(assist_fd);
    dprintf(2,"router assist FIFO sent=%llu full=%llu unavailable=%llu\n",
        (unsigned long long)assist_ok,(unsigned long long)assist_full,(unsigned long long)assist_open_wait);
    close(factory.in);
    // Keep the single DMA mirror reader until the armed capture naturally closes.
    // No extra audio is scored, retained, or attached to either branch.
    need(received_frames<=seconds*125,"trial exceeds armed window");
    while(received_frames<seconds*125){
        Raw r;exact(raw,&r,sizeof(r));
        need(r.magic==0x31574152&&r.n==128&&r.first==next,"tail discontinuity");
        next+=128;++received_frames;
    }
    close(raw);deliver.join();output.join();int s1=0,s2=0;waitpid(score.pid,&s1,0);waitpid(factory.pid,&s2,0);
    need(WIFEXITED(s1)&&WEXITSTATUS(s1)==0&&WIFEXITED(s2)&&WEXITSTATUS(s2)==0,"worker exit");
    dprintf(2,"router complete hops=%llu render_frames=%llu vendor_locked_frames=%llu\n",(unsigned long long)nblocks,(unsigned long long)ref_count,(unsigned long long)lock_count);
    close(ref);unlink(mirror);_exit(0);
}
