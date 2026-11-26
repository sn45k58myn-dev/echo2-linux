/* Synthetic inputs only. Run in a disposable, network-disabled container. */
#define _GNU_SOURCE
#define FR_DIR "/tmp/factory-ha-route"
#define FR_MUTE "/tmp/factory-ha-route/privacy"
#define FR_DND "/tmp/factory-ha-route/dnd"
#include "route.h"
#include <assert.h>
#include <stdlib.h>

static void put(const char *file,const char *value){
    int fd=open(file,O_CREAT|O_TRUNC|O_WRONLY,0600);
    assert(fd>=0 && write(fd,value,strlen(value))==(ssize_t)strlen(value));close(fd);
}
static void send_block(int fd,uint64_t first) {
    struct fr_packet block={.first=first};
    for(unsigned i=0;i<128;i++)block.samples[i]=(int16_t)(first+i);
    assert(write(fd,&block,sizeof(block))==sizeof(block));
}
static void zeros(const int16_t *a,size_t n){for(size_t i=0;i<n;i++)assert(a[i]==0);}
int main(void){
    assert(geteuid()==0 && access("/.dockerenv",F_OK)==0);
    assert(mkdir(FR_DIR,0700)==0);
    put(FR_MUTE,"0\n");put(FR_DIR "/lease","");put(FR_DIR "/armed","1");
    struct stat st;
    int16_t out[160];
    assert(!fr_frame(3800,out,160)); /* unknown peer */
    assert(!stat(FR_DIR "/factory-ha.sock",&st)&&S_ISFIFO(st.st_mode)&&(st.st_mode&0777)==0600);
    int sender=open(FR_DIR "/factory-ha.sock",O_WRONLY|O_NONBLOCK);assert(sender>=0);
    fr_peer=4;assert(fr_frame(FR_DELAY,out,160));zeros(out,160);
    fr_delivered(FR_DELAY+160);
    send_block(sender,128);send_block(sender,256);
    assert(fr_frame(FR_DELAY+160,out,160));assert(fr_sent==0);
    for(unsigned i=0;i<160;i++)assert(out[i]==(int16_t)(160+i));
    fr_delivered(FR_DELAY+320);
    assert(fr_sent==160&&fr_last_source==320&&fr_missing==160);
    send_block(sender,514); /* invalid sample alignment */
    assert(fr_frame(FR_DELAY+320,out,160));
    for(unsigned i=0;i<64;i++)assert(out[i]==(int16_t)(320+i));
    zeros(out+64,96);fr_delivered(FR_DELAY+480);assert(fr_missing==256&&fr_sent==224);
    put(FR_MUTE,"1");assert(!fr_frame(4500,out,160));
    send_block(sender,512);put(FR_MUTE,"0");assert(fr_frame(FR_DELAY+512,out,160));zeros(out,160);
    put(FR_DND,"on");assert(!fr_frame(4500,out,160));put(FR_DND,"off");
    assert(!mkdir("/run/libreecho",0700)||errno==EEXIST);
    put("/run/libreecho/intercom.lease","1");assert(!fr_frame(4500,out,160));
    assert(!unlink("/run/libreecho/intercom.lease"));
    assert(fr_frame(FR_DELAY+672,out,160));
    struct timespec stale[2]={{0,0},{0,0}};
    assert(!utimensat(AT_FDCWD,FR_DIR "/lease",stale,0));assert(!fr_frame(5000,out,160));
    put(FR_DIR "/lease","1");assert(fr_frame(5100,out,160));
    assert(!unlink(FR_DIR "/armed"));assert(!fr_frame(5200,out,160));
    put(FR_DIR "/armed","1");assert(fr_frame(FR_DELAY+3968,out,160));
    uint64_t expected=4096, before=fr_sent;
    for(int i=0;i<24;i++)send_block(sender,expected+128*i);
    for(int i=0;i<19;i++){
        assert(fr_frame(FR_DELAY+expected,out,160));
        for(int j=0;j<160;j++)assert(out[j]==(int16_t)(expected+j));
        fr_delivered(FR_DELAY+expected+160);expected+=160;
    }
    assert(fr_sent-before==3040);
    puts("passed private FIFO transport with 24-packet bursts, crossed frame boundaries, delivery receipts, missing-data silence, stale-buffer disposal, mute, DND, intercom, lease and disarm");
    return 0;
}
