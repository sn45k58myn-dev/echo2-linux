// Each branch owns its ONNX state and runs in order on an independent worker.
// Audio stays in bounded RAM. Stdout carries the same paired sample indices and a
// separate timestamp for each result, even when one branch falls behind the other.
#include "wake_engine.h"
#include <array>
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <memory>
#include <mutex>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <thread>
#include <vector>
#include <unistd.h>

namespace {
constexpr size_t hop=1280;
struct Header {
    char magic[4]; uint16_t version,flags;
    uint64_t start_sample, capture_ns, afe_ns;
};
static_assert(sizeof(Header)==32 && sizeof(float)==4,"wire record");
struct Frame {
    Header h{};
    std::array<std::array<int16_t,hop>,2> audio{};
    uint64_t received=0, started[2]{}, finished[2]{};
    float scores[2]{};
};
uint64_t now(){
    timespec ts{};
    if(clock_gettime(CLOCK_MONOTONIC,&ts))_exit(21);
    return static_cast<uint64_t>(ts.tv_sec)*1000000000+ts.tv_nsec;
}
using Engine=std::unique_ptr<le_wake_engine,decltype(&le_wake_engine_destroy)>;
}
int main(int argc,char** argv){
    if(argc!=4)return 2;
    bool solo=!strcmp(argv[2],"factory-only")||!strcmp(argv[2],"fixture-factory-only");
    bool live=!strcmp(argv[2],"live")||!strcmp(argv[2],"factory-only");
    if(!live&&!solo&&strcmp(argv[2],"fixture"))return 2;
    char* end=nullptr;
    long count=strtol(argv[3],&end,10);
    if(!end||*end||count<6||count>1125)return 2;
    rlimit r{0,0};
    if(setrlimit(RLIMIT_CORE,&r)||prctl(PR_SET_DUMPABLE,0))return 3;
    Engine normal(solo?nullptr:le_wake_engine_create(argv[1],1),le_wake_engine_destroy);
    Engine factory(le_wake_engine_create(argv[1],1),le_wake_engine_destroy);
    if((!solo&&!normal)||!factory)return 4;
    std::vector<Frame> frames(static_cast<size_t>(count));
    std::mutex m;std::condition_variable cv;
    long available=0,completed[2]{};
    auto work=[&](unsigned k,le_wake_engine* engine){
        for(long i=0;i<count;i++){
            {std::unique_lock<std::mutex> l(m);cv.wait(l,[&]{return available>i;});}
            auto& rec=frames[i];
            rec.started[k]=now();int fresh=0;
            if(le_wake_engine_feed(engine,rec.audio[k].data(),hop,&rec.scores[k],&fresh)||!fresh
                ||!std::isfinite(rec.scores[k])||rec.scores[k]<0||rec.scores[k]>1)_exit(8);
            rec.finished[k]=now();
            memset(rec.audio[k].data(),0,hop*2);
            {std::lock_guard<std::mutex> l(m);completed[k]=i+1;}cv.notify_all();
        }
    };
    std::thread a;
    if(!solo)a=std::thread(work,0,normal.get());
    std::thread b(work,1,factory.get());
    setvbuf(stdout,nullptr,_IOLBF,0);
    printf("{\"ready\":true,\"mode\":\"%s\",\"hop\":1280,\"execution\":\"%s\"}\n",
           live?"live":"fixture",solo?"factory-only":"independent");
    std::thread writer([&]{
        for(long i=0;i<count;i++){
            {std::unique_lock<std::mutex> l(m);cv.wait(l,[&]{return (solo||completed[0]>i)&&completed[1]>i;});}
            auto& rec=frames[i];auto& h=rec.h;
            if(solo){
                printf("{\"end_sample\":%llu,\"capture_end_ns\":%llu,\"factory_afe_ns\":%llu,"
                       "\"received_ns\":%llu,\"finished_ns\":%llu,\"factory_started_ns\":%llu,"
                       "\"factory_finished_ns\":%llu,\"flags\":%u,\"factory\":%.9g}\n",
                       (unsigned long long)(h.start_sample+hop),(unsigned long long)h.capture_ns,
                       (unsigned long long)h.afe_ns,(unsigned long long)rec.received,
                       (unsigned long long)rec.finished[1],(unsigned long long)rec.started[1],
                       (unsigned long long)rec.finished[1],h.flags,rec.scores[1]);
                continue;
            }
            printf("{\"end_sample\":%llu,\"capture_end_ns\":%llu,\"factory_afe_ns\":%llu,"
                   "\"received_ns\":%llu,\"finished_ns\":%llu,"
                   "\"baseline_started_ns\":%llu,\"baseline_finished_ns\":%llu,"
                   "\"factory_started_ns\":%llu,\"factory_finished_ns\":%llu,"
                   "\"flags\":%u,\"baseline\":%.9g,\"factory\":%.9g}\n",
                   (unsigned long long)(h.start_sample+hop),(unsigned long long)h.capture_ns,
                   (unsigned long long)h.afe_ns,(unsigned long long)rec.received,
                   (unsigned long long)std::max(rec.finished[0],rec.finished[1]),
                   (unsigned long long)rec.started[0],(unsigned long long)rec.finished[0],
                   (unsigned long long)rec.started[1],(unsigned long long)rec.finished[1],
                   h.flags,rec.scores[0],rec.scores[1]);
        }
    });
    for(long i=0;i<count;i++){
        auto& rec=frames[i];auto& h=rec.h;
        if(fread(&h,1,sizeof(h),stdin)!=sizeof(h)||fread(rec.audio[0].data(),2,hop,stdin)!=hop
            ||fread(rec.audio[1].data(),2,hop,stdin)!=hop)_exit(5);
        rec.received=now();
        if(solo)memset(rec.audio[0].data(),0,hop*2);
        if(memcmp(h.magic,"E2SW",4)||h.version!=1||h.flags>3||h.flags==1||h.start_sample>UINT64_MAX-hop||
            (i&&h.start_sample!=frames[i-1].h.start_sample+hop))_exit(6);
        if(live&&(!h.capture_ns||h.capture_ns>rec.received||(i&&h.capture_ns<=frames[i-1].h.capture_ns)||
            (h.afe_ns&&(h.afe_ns<h.capture_ns||h.afe_ns>rec.received))))_exit(7);
        for(auto& v:rec.audio[1])v=static_cast<int16_t>(static_cast<uint16_t>(v)&0xfffeU);
        {std::lock_guard<std::mutex> l(m);available=i+1;}cv.notify_all();
    }
    if(a.joinable())a.join();
    b.join();writer.join();return 0;
}
