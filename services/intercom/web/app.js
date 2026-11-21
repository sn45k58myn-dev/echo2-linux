'use strict';
async function api(path, options={}) {
  const response=await fetch('/api/v1'+path,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json'}});
  if(response.status===401) throw Error('Sign in again to call.');
  if(response.status===409) throw Error('Another call is already using the room.');
  if(!response.ok) throw Error('The room could not accept that request.');
  return (await response.json()).data;
}
const start=document.querySelector('#intercom-start');
const mute=document.querySelector('#intercom-mute');
const status=document.querySelector('#intercom-status');
start.onclick=startIntercom;
document.querySelector('#intercom-end').onclick=()=>stopIntercom();
mute.onclick=()=>{
  const tracks=intercomCall?.stream?.getAudioTracks();
  if(!tracks?.length)return;
  const next=!tracks[0].enabled;
  tracks.forEach(t=>{t.enabled=next;});
  mute.textContent=next?'Mute my microphone':'Unmute my microphone';
};
async function refresh() {
  try {
    const room=await api('/intercom');
    const probe=room.backend==='probe';
    document.querySelector('#room-title').textContent=probe?'Linux test room':'Room intercom';
    document.querySelector('#instructions').textContent=probe?
      'Listen for a low note, say “testing”, then pause. Two higher notes mean the Linux room heard you.':
      'Keep this page open while you talk with the room.';
    start.disabled=!!intercomCall||!room.enabled||room.busy;
    if(!room.enabled&&!intercomCall)status.textContent='Calls are disabled on this room.';
    const m=room.metrics;
    document.querySelector('#levels').textContent=probe&&m.to_phone?
      `Phone sent ${m.from_phone} packets · room sent ${m.to_phone} · voice confirmations ${m.probe_replies}`:'';
  } catch(error) { if(!intercomCall){start.disabled=true;status.textContent=error.message;} }
}
refresh();
setInterval(()=>{if(!document.hidden)refresh();},1500);
