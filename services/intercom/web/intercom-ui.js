'use strict';
/* The caller button is the only path to getUserMedia. Leaving this page or
 * putting Safari in the background closes every track and the call. */
let intercomCall=null;
function intercomMessage(text){const e=document.getElementById('intercom-status');if(e)e.textContent=text;}
function stopIntercom(message='Call ended'){
  const call=intercomCall;if(!call)return;intercomCall=null;
  if(call){
    clearInterval(call.heartbeat);clearTimeout(call.deadline);
    if(call.ws){call.ws.onclose=null;call.ws.close();}
    if(call.stream)call.stream.getTracks().forEach(t=>t.stop());
    if(call.node)call.node.disconnect();if(call.source)call.source.disconnect();
    if(call.context)call.context.close().catch(()=>{});
    if(call.ticketCreated)api('/intercom/call',{method:'DELETE',body:JSON.stringify({id:call.id})}).catch(()=>{});
  }
  intercomMessage(message);
  const start=document.getElementById('intercom-start'),end=document.getElementById('intercom-end'),mute=document.getElementById('intercom-mute'),permission=document.getElementById('intercom-permission');
  if(start)start.disabled=!!permission&&!permission.checked;if(end)end.disabled=true;if(mute){mute.disabled=true;mute.textContent='Mute my microphone';}
}
async function startIntercom(){
  if(intercomCall)return;
  if(!window.isSecureContext||location.protocol!=='https:'||!navigator.mediaDevices||!window.AudioWorkletNode){
    intercomMessage('Open this speaker over trusted HTTPS in Safari to make a call.');return;
  }
  const call={context:null,stream:null,node:null,source:null,ws:null,sequence:0,receiveSequence:0,ticketCreated:false};
  intercomCall=call;
  call.deadline=setTimeout(()=>{if(intercomCall===call)stopIntercom('Call setup timed out.');},30000);
  document.getElementById('intercom-start').disabled=true;
  document.getElementById('intercom-end').disabled=false;
  intercomMessage('Requesting your microphone…');
  try{
    /* Create/resume during the explicit user gesture (required by Safari). */
    call.context=new AudioContext({latencyHint:'interactive'});
    await call.context.resume();
    if(intercomCall!==call)return;
    if(document.hidden){stopIntercom('Call ended when this page left the foreground.');return;}
    call.context.onstatechange=()=>{if(intercomCall===call&&call.context.state!=='running')stopIntercom('Call ended because phone audio was interrupted.');};
    call.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if(intercomCall!==call){call.stream.getTracks().forEach(t=>t.stop());return;}
    call.stream.getTracks().forEach(t=>{t.onended=()=>{if(intercomCall===call)stopIntercom('Phone microphone disconnected.');};});
    await call.context.audioWorklet.addModule('/js/intercom-worklet.js');
    if(intercomCall!==call)return;
    call.node=new AudioWorkletNode(call.context,'libreecho-intercom',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1]});
    call.source=call.context.createMediaStreamSource(call.stream);call.source.connect(call.node);call.node.connect(call.context.destination);
    const grant=await api('/intercom/call',{method:'POST',body:JSON.stringify({})});call.ticketCreated=true;call.id=grant.id;
    if(intercomCall!==call){api('/intercom/call',{method:'DELETE',body:JSON.stringify({id:call.id})}).catch(()=>{});return;}
    call.ws=new WebSocket('wss://'+location.host+grant.path);call.ws.binaryType='arraybuffer';
    clearTimeout(call.deadline);
    call.deadline=setTimeout(()=>{if(intercomCall===call)stopIntercom('The speaker did not accept the call. Check its microphone, privacy switch, Do Not Disturb and Home Assistant voice setting.');},5000);
    let ready=false,lastReply=Date.now();
    call.ws.onopen=()=>{call.ws.send(JSON.stringify({ticket:grant.ticket}));delete grant.ticket;intercomMessage('Connecting to the room…');};
    call.ws.onmessage=({data})=>{
      if(intercomCall!==call)return;
      lastReply=Date.now();
      if(typeof data==='string'){
        if(data==='ready'&&!ready){ready=true;clearTimeout(call.deadline);call.node.port.postMessage({type:'arm'});document.getElementById('intercom-mute').disabled=false;intercomMessage('Call connected');}
        else if(data!=='pong')stopIntercom('The speaker sent an unexpected call message.');
        return;
      }
      if(!ready||data.byteLength!==644){stopIntercom('Call audio was interrupted.');return;}
      const view=new DataView(data);
      if(view.getUint32(0)!==call.receiveSequence++){stopIntercom('Call audio lost its place.');return;}
      const pcm=data.slice(4);call.node.port.postMessage({type:'pcm',buffer:pcm},[pcm]);
    };
    call.node.port.onmessage=({data})=>{
      if(intercomCall!==call)return;
      if(data.type==='overrun'){
        const reasons = {
          'capture-stalled':'Call ended because phone microphone audio stalled.',
          'playback-stalled':'Call ended because phone playback stalled.',
          'invalid-playback':'Call ended because the phone received invalid room audio.'
        };
        stopIntercom(reasons[data.reason] || 'Call ended because the phone could not keep up with live audio.');
        return;
      }
      if(data.type!=='pcm'||!ready||call.ws.readyState!==WebSocket.OPEN)return;
      if(call.ws.bufferedAmount>4096){stopIntercom('Call ended because the connection became too slow.');return;}
      const packet=new ArrayBuffer(644);new DataView(packet).setUint32(0,call.sequence++);
      new Uint8Array(packet,4).set(new Uint8Array(data.buffer));call.ws.send(packet);call.node.port.postMessage({type:'credit'});
    };
    call.ws.onclose=()=>{if(intercomCall===call)stopIntercom('Call ended. Check the room microphone and privacy settings if it ended unexpectedly.');};
    call.ws.onerror=()=>{if(intercomCall===call)stopIntercom('Secure call connection failed. Check this speaker’s HTTPS certificate.');};
    call.heartbeat=setInterval(()=>{
      if(intercomCall!==call)return;
      if(Date.now()-lastReply>6000){stopIntercom('The speaker stopped responding.');return;}
      if(call.ws.readyState===WebSocket.OPEN)call.ws.send('ping');
    },2000);
  }catch(e){if(intercomCall===call)stopIntercom(e.message||'The call could not start.');}
}
window.addEventListener('pagehide',()=>stopIntercom());
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopIntercom('Call ended when this page left the foreground.');});
