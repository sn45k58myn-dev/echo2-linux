/* Radar physical mono playback return. Call rr_find() on 128 or more
 * current capture frames against an older contiguous 48 kHz mono render
 * window. This code consumes only the board data and independent renderer.
 * No resynthesis from software samples is allowed in rr_normalize(). */
#ifndef REAL_RETURN_H
#define REAL_RETURN_H
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <limits.h>

static inline int rr_board_sample(const unsigned char *frame) {
    int upper=(int16_t)((unsigned)frame[22]|((unsigned)frame[23]<<8));
    unsigned frac=frame[26];
    int out=upper+(frac>128 || (frac==128 && (upper&1)));
    return out>32767?32767:out;
}

static inline void rr_normalize(unsigned char *raw,size_t frames) {
    for(size_t i=0;i<frames;i++) {
        unsigned char *p=raw+27*i;
        uint16_t v=(uint16_t)(int16_t)rr_board_sample(p);
        p[21]=p[24]=0;
        p[22]=p[25]=(unsigned char)v;
        p[23]=p[26]=(unsigned char)(v>>8);
    }
}

/* A successful match returns ref-local frame j: raw[t] matches ref[j+3*t].
 * The native caller must observe >=2 disjoint matches separated in time
 * agreeing on absolute ref/mic clock offset before normalizing any sample.
 * Pass only fully present reference frames, or a zero/one validity mask.
 * Returns -1 for silence, ambiguity, loss of agreement, unrelated fractions. */
static inline int rr_find(const unsigned char *raw,size_t frames,const int16_t *ref,
                          const unsigned char *valid,size_t ref_frames) {
    if(frames<128 || frames>2048 || ref_frames<3*(frames-1)+1 || ref_frames>144000)return -1;
    int min_val=32767,max_val=-32768,exciting=0,unique_fractional=0;
    int prev=rr_board_sample(raw);
    for(size_t t=0;t<frames;t++) {
        const unsigned char *p=raw+27*t;
        if(p[21] || p[24] || p[25])return -1;
        int v=rr_board_sample(p);
        if(v<min_val)min_val=v;
        if(v>max_val)max_val=v;
        if(v>prev+2 || v<prev-2)exciting++;
        if(p[26]>128)unique_fractional++;
        prev=v;
    }
    if(max_val-min_val<32 || (unsigned)exciting<frames/6 || (unsigned)unique_fractional<frames/10)return -1;
    int winner=-1;
    for(size_t j=0;j+3*(frames-1)<ref_frames;j++) {
        /* Quick integer comparison at scattered landmarks prunes most offsets. */
        int failed=0;
        for(size_t k=0;k<7;k++) {
            size_t t=(frames-1)*k/6, at=j+3*t;
            int d=rr_board_sample(raw+27*t)-ref[at];
            if((valid && !valid[at]) || d < -1 || d>1) {failed=1;break;}
        }
        if(failed)continue;
        unsigned different=0;
        for(size_t t=0;t<frames;t++) {
            size_t at=j+3*t;
            int d=rr_board_sample(raw+27*t)-ref[at];
            if((valid && !valid[at]) || d < -1 || d>1 ||
                    (d && raw[27*t+26]!=128)) {failed=1;break;}
            different+=(d!=0);
        }
        if(failed || different>frames/100+1)continue;
        if(winner>=0)return -1;
        winner=(int)j;
    }
    return winner;
}
#endif
