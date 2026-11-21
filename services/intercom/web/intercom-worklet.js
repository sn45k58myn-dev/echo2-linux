'use strict';
/* 16 kHz mono PCM, 20 ms packets. No recording, growing queues or network. */
const PACKET_SAMPLES = 320;
const CAPTURE_CREDITS = 2;
const PLAYBACK_PRIME = 2 * PACKET_SAMPLES;
const PLAYBACK_LIMIT = 6 * PACKET_SAMPLES;
const STALL_SECONDS = 0.4;
const CAPTURE_STALL_PACKETS = 20; // 400 ms at 20 ms per packet.
const WIRE_RATE = 16000;
const CAPTURE_PHASES = 32;

// Band-limit before converting the phone's native sample clock to wire PCM.
// The symmetric Blackman-windowed sinc adds about 0.75 ms at 44.1/48 kHz.
// Fractional phases avoid alternating two- and three-sample box averages on
// a 44.1 kHz input. Coefficients are built once, outside the render callback.
class CaptureFilter {
  constructor(rate) {
    const half=Math.min(256,Math.ceil(rate*.00075));
    this.length=2*half+1;
    this.coefficients=new Float64Array((CAPTURE_PHASES+1)*this.length);
    this.history=new Float32Array(2*this.length);
    this.write=0;
    const cutoff=.44*Math.min(rate,WIRE_RATE)/rate;
    for (let phase=0;phase<=CAPTURE_PHASES;phase++) {
      const base=phase*this.length;
      let sum=0;
      for (let i=0;i<this.length;i++) {
        const x=i-half+phase/CAPTURE_PHASES;
        const angle=Math.PI*i/half;
        const window=.42-.5*Math.cos(angle)+.08*Math.cos(2*angle);
        const sinc=x===0 ? 2*cutoff : Math.sin(2*Math.PI*cutoff*x)/(Math.PI*x);
        this.coefficients[base+i]=window*sinc;
        sum+=this.coefficients[base+i];
      }
      for (let i=0;i<this.length;i++) this.coefficients[base+i]/=sum;
    }
  }
  push(value) {
    // Mirror the fixed delay line to keep each convolution contiguous.
    this.history[this.write]=value;
    this.history[this.write+this.length]=value;
    this.write++;
    if (this.write===this.length) this.write=0;
  }
  sample(fraction) {
    const base=Math.round(fraction*CAPTURE_PHASES)*this.length;
    let value=0;
    for (let i=0;i<this.length;i++) value+=this.history[this.write+i]*this.coefficients[base+i];
    return value;
  }
}

class LibreEchoIntercom extends AudioWorkletProcessor {
  constructor() {
    super();
    this.armed=false; this.credit=0; this.capture=new Int16Array(PACKET_SAMPLES); this.used=0;
    this.capturePhase=0;
    this.captureFilter=sampleRate===WIRE_RATE ? null : new CaptureFilter(sampleRate);
    this.ring=new Float32Array(4096); this.read=0; this.count=0; this.playPhase=0;
    this.playing=false;
    this.overrun=false;
    this.captureLoss=0;
    this.renderedSamples=0;
    this.playbackOverflowAt=null;
    this.port.onmessage=({data})=>{
      if(this.overrun)return;
      if(data.type==='arm'){this.armed=true;this.credit=CAPTURE_CREDITS;}
      else if(data.type==='credit')this.credit=Math.min(CAPTURE_CREDITS,this.credit+1);
      else if(data.type==='pcm'){
        const pcm=new Int16Array(data.buffer);
        if(pcm.length!==PACKET_SAMPLES){this.fail('invalid-playback');return;}
        const overflow=this.count+pcm.length-PLAYBACK_LIMIT;
        if(overflow>0){
          if(this.playbackOverflowAt===null)this.playbackOverflowAt=this.renderedSamples;
          if(this.renderedSamples-this.playbackOverflowAt>=STALL_SECONDS*sampleRate){
            this.fail('playback-stalled');
            return;
          }
          // Evict stale samples, keeping the current interpolation phase.
          this.read=(this.read+overflow)&4095;
          this.count-=overflow;
        }else this.playbackOverflowAt=null;
        for(let i=0;i<pcm.length;i++)this.ring[(this.read+this.count++)&4095]=pcm[i]/32768;
        // Keep one packet buffered when the next arrives on time.
        if(this.count>=PLAYBACK_PRIME)this.playing=true;
      }
    };
  }
  fail(reason){
    if(this.overrun)return;
    this.overrun=true;
    this.armed=false;
    this.count=0;
    this.port.postMessage({type:'overrun',reason});
  }
  process(inputs,outputs){
    const output=outputs[0][0],input=inputs[0]&&inputs[0][0];
    this.renderedSamples+=output.length;
    for(let i=0;i<output.length;i++){
      output[i]=0;
      if(this.playing && this.count>1){
        const a=this.ring[this.read],b=this.ring[(this.read+1)&4095];
        output[i]=a+(b-a)*this.playPhase;
        this.playPhase+=16000/sampleRate;
        const step=Math.floor(this.playPhase);
        if(step){this.read=(this.read+step)&4095;this.count-=step;this.playPhase-=step;}
      }else if(this.playing){
        // Refill after starvation. Drop the leftover interpolation sample
        // so it cannot be spliced into fresh audio after a long gap.
        this.playing=false;this.count=0;this.playPhase=0;
      }
      if(!this.armed)continue;
      const sample=input?input[i]:0;
      if(this.captureFilter)this.captureFilter.push(sample);
      this.capturePhase+=WIRE_RATE;
      while(this.capturePhase>=sampleRate && this.armed){
        this.capturePhase-=sampleRate;
        const filtered=this.captureFilter ? this.captureFilter.sample(this.capturePhase/WIRE_RATE) : sample;
        const v=Math.max(-1,Math.min(1,filtered));
        this.capture[this.used++]=v<0?v*32768:v*32767;
        if(this.used===PACKET_SAMPLES){
          this.used=0;
          if(!this.credit){
            // This packet was never sent, so discard it without creating a
            // gap in the stream's network sequence or accumulating a queue.
            if(++this.captureLoss>=CAPTURE_STALL_PACKETS)this.fail('capture-stalled');
            continue;
          }
          this.captureLoss=0;
          this.credit--;this.port.postMessage({type:'pcm',buffer:this.capture.buffer},[this.capture.buffer]);
          this.capture=new Int16Array(PACKET_SAMPLES);
        }
      }
    }
    return true;
  }
}
registerProcessor('libreecho-intercom',LibreEchoIntercom);
