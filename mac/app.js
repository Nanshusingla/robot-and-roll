'use strict';
const $ = id => document.getElementById(id);
let host = '', key = '', connected = false;
let activeDrive = null, motorTimer = null;
let socket = null, audioContext = null, micStream = null, micSource = null, micProcessor = null;
let nextPlaybackTime = 0, callActive = false, macMuted = false, robotMuted = false;
const RATE = 16000;
const signal = (text, isError = false) => { $('status').textContent = text; $('status').style.borderColor = isError ? '#ef7185' : '#7e70ef'; };
const url = route => `http://${host}:5000${route}`;
const api = async (route, data) => {
  const opts = {method: data === undefined ? 'GET' : 'POST', headers:{'X-Robot-Key':key}};
  if(data !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(data); }
  const response = await fetch(url(route), opts);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `HTTP ${response.status}`);
  return result;
};
function setEnabled(on) {
  document.querySelectorAll('[data-drive], #stop, #speak, #refreshMusic, #playMusic, #stopMusic, #startCall').forEach(b => b.disabled = !on);
  $('endCall').disabled = true;
}
$('connect').onclick = async () => {
  host = $('host').value.trim().replace(/^https?:\/\//, '').replace(/:\d+$/, '').replace(/\/$/, '');
  key = $('key').value.trim();
  if (!host || !key) return signal('Enter your Raspberry Pi hostname and the connection key.', true);
  try {
    stopMotors(); stopCall();
    const health = await api('/api/health');
    connected = true; setEnabled(true);
    $('stateBadge').textContent = 'Connected to Pi'; $('stateBadge').classList.add('connected');
    const img = $('live');
    img.src = url('/video?key=' + encodeURIComponent(key) + '&t=' + Date.now());
    img.hidden = false; $('videoPlaceholder').hidden = true;
    $('camBadge').textContent = health.camera ? 'Live' : 'Waiting for camera';
    img.onload = () => $('camBadge').textContent = 'Live';
    img.onerror = () => { $('camBadge').textContent = 'Video unavailable'; signal('Video failed. Stop ffmpeg/VLC capture processes and check /dev/video0.', true); };
    signal('Connected. Hold movement buttons; choose Start voice call for two-way sound.');
    await refreshMusic();
  } catch(e) {
    connected = false; setEnabled(false);
    $('stateBadge').textContent = 'Not connected'; $('stateBadge').classList.remove('connected');
    signal(`Connection failed: ${e.message}. Is the Pi server running on port 5000?`, true);
  }
};
const commands = {
  forward:[1,1], backward:[-1,-1], left:[-1,1], right:[1,-1],
  leftPlus:[1,0], leftMinus:[-1,0], rightPlus:[0,1], rightMinus:[0,-1],
  bothPlus:[1,1], bothMinus:[-1,-1]
};
function sendMotor(arr) {
  if (!connected) return;
  api('/api/motor', {left:arr[0], right:arr[1], raw: ['leftPlus','leftMinus','rightPlus','rightMinus','bothPlus','bothMinus'].includes(activeDrive)}).catch(e=>signal(`Motor: ${e.message}`,true));
}
function startMotor(name) {
  if (!connected || !commands[name]) return;
  if (activeDrive === name) return;
  stopMotors();
  activeDrive = name;
  const el = document.querySelector(`[data-drive="${name}"]`);
  if (el) el.classList.add('active');
  sendMotor(commands[name]);
  motorTimer = setInterval(() => { if (activeDrive) sendMotor(commands[activeDrive]); }, 160);
}
function stopMotors() {
  clearInterval(motorTimer); motorTimer = null;
  activeDrive = null;
  document.querySelectorAll('[data-drive]').forEach(b=>b.classList.remove('active'));
  if (connected) sendMotor([0,0]);
}
for (const b of document.querySelectorAll('[data-drive]')) {
  b.addEventListener('pointerdown', e => { if (b.disabled) return; e.preventDefault(); b.setPointerCapture(e.pointerId); startMotor(b.dataset.drive); });
  b.addEventListener('pointerup', stopMotors);
  b.addEventListener('pointercancel', stopMotors);
  b.addEventListener('lostpointercapture', stopMotors);
  b.addEventListener('contextmenu',e=>e.preventDefault());
}
$('stop').onclick = stopMotors;
const map = {ArrowUp:'forward',ArrowDown:'backward',ArrowLeft:'left',ArrowRight:'right',w:'forward',s:'backward',a:'left',d:'right'};
window.addEventListener('keydown',e=>{
  const tag = document.activeElement && document.activeElement.tagName;
  if (['INPUT','SELECT','TEXTAREA'].includes(tag)) return;
  if (map[e.key] && connected) {e.preventDefault(); startMotor(map[e.key]);}
  if (e.key==='Escape') stopMotors();
});
window.addEventListener('keyup', e => {if(map[e.key] && activeDrive===map[e.key]) stopMotors();});
window.addEventListener('blur', stopMotors);
document.addEventListener('visibilitychange', () => {if(document.hidden) stopMotors();});
window.addEventListener('pagehide',()=>{stopMotors();stopCall();});
$('speak').onclick = async () => {
  const text = $('speech').value.trim();
  if (!text) return signal('Type a message first.',true);
  try { await api('/api/speak',{text}); signal('Text-to-speech sent to the JBL GO 2.'); }
  catch(e) {signal(e.message,true);}
};
async function refreshMusic() {
  try {
    const {tracks} = await api('/api/music');
    $('tracks').replaceChildren();
    for (const name of tracks) {let opt=new Option(name,name);$('tracks').add(opt);}
    if (!tracks.length) $('tracks').add(new Option('No music files in Pi music/ folder',''));
    $('playMusic').disabled = !tracks.length;
  } catch(e) {signal('Music list: '+e.message,true);}
}
$('refreshMusic').onclick = refreshMusic;
$('playMusic').onclick = async () => {
  if(!$('tracks').value) return;
  try {await api('/api/music/play',{name:$('tracks').value});signal('Playing '+$('tracks').value+' through JBL.');}
  catch(e) {signal(e.message,true);}
};
$('stopMusic').onclick = async () => { try {await api('/api/music/stop',{});signal('Music stopped.');}catch(e){signal(e.message,true);} };
function downsample(data, inputRate, outputRate) {
  if (inputRate === outputRate) return data;
  const count = Math.round(data.length * outputRate / inputRate);
  const out = new Float32Array(count);
  for(let i=0;i<count;i++) {
    const pos=i*inputRate/outputRate, low=Math.floor(pos), high=Math.min(low+1,data.length-1), mix=pos-low;
    out[i]=data[low]*(1-mix)+data[high]*mix;
  }
  return out;
}
function playRobotMic(arrayBuffer) {
  if (robotMuted || !audioContext || !callActive) return;
  const view = new DataView(arrayBuffer);
  const count = Math.floor(view.byteLength / 2);
  if (!count) return;
  const buffer = audioContext.createBuffer(1,count,RATE);
  const output=buffer.getChannelData(0);
  for(let i=0;i<count;i++) output[i]=view.getInt16(i*2,true)/32768;
  const source=audioContext.createBufferSource();source.buffer=buffer;source.connect(audioContext.destination);
  const now=audioContext.currentTime;
  // Bound latency by dropping new chunks if browser got too far behind.
  if(nextPlaybackTime>now+0.7) return;
  nextPlaybackTime=Math.max(now+0.09,nextPlaybackTime);
  source.start(nextPlaybackTime); nextPlaybackTime+=buffer.duration;
}
async function startCall() {
  if (callActive || !connected) return;
  try {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    await audioContext.resume();
    micStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    micSource=audioContext.createMediaStreamSource(micStream);
    micProcessor=audioContext.createScriptProcessor(2048,1,1);
    micProcessor.onaudioprocess=e=>{
      e.outputBuffer.getChannelData(0).fill(0); // Don't locally play our own microphone
      if(!callActive || macMuted || !socket || socket.readyState!==WebSocket.OPEN || socket.bufferedAmount>64000) return;
      const input=downsample(e.inputBuffer.getChannelData(0),audioContext.sampleRate,RATE);
      const pcm=new Int16Array(input.length);
      for(let i=0;i<input.length;i++) {const s=Math.max(-1,Math.min(1,input[i]));pcm[i]=s<0?s*32768:s*32767;}
      socket.send(pcm.buffer);
    };
    micSource.connect(micProcessor); micProcessor.connect(audioContext.destination);
    socket=new WebSocket(`ws://${host}:8765`);
    socket.binaryType='arraybuffer';
    socket.onopen=()=>socket.send(JSON.stringify({key}));
    socket.onmessage=e=>{
      if(typeof e.data==='string') {
        const msg=JSON.parse(e.data);
        if(msg.type==='ready') {
          callActive=true;nextPlaybackTime=audioContext.currentTime+0.14;
          $('startCall').disabled=true;$('endCall').disabled=false;
          $('muteMac').disabled=false;$('muteRobot').disabled=false;
          $('callStatus').textContent='Voice call connected: Mac mic → JBL; webcam mic → Mac speakers.';
          signal('Two-way voice call connected.');
        }
      } else playRobotMic(e.data);
    };
    socket.onerror=()=>signal('Audio WebSocket connection error. Check Pi port 8765.',true);
    socket.onclose=()=> {if(callActive) signal('Call disconnected.',true);stopCall();};
    $('callStatus').textContent='Connecting two-way audio...';
  } catch(e) {signal('Microphone permission or audio error: '+e.message,true);stopCall();}
}
function stopCall() {
  callActive=false;
  if(socket){const ws=socket;socket=null;ws.onclose=null;ws.close();}
  if(micProcessor){micProcessor.disconnect();micProcessor.onaudioprocess=null;micProcessor=null;}
  if(micSource){micSource.disconnect();micSource=null;}
  if(micStream){micStream.getTracks().forEach(t=>t.stop());micStream=null;}
  if(audioContext){audioContext.close().catch(()=>{});audioContext=null;}
  $('startCall').disabled=!connected;$('endCall').disabled=true;
  $('muteMac').disabled=true;$('muteRobot').disabled=true;
  macMuted=false;robotMuted=false;
  $('muteMac').textContent='Mute my mic';$('muteRobot').textContent='Mute robot audio';
  $('callStatus').textContent='Not in a call.';
}
$('startCall').onclick=startCall;
$('endCall').onclick=stopCall;
$('muteMac').onclick=()=> {macMuted=!macMuted;$('muteMac').textContent=macMuted?'Unmute my mic':'Mute my mic';if(socket&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'talk',enabled:!macMuted}));};
$('muteRobot').onclick=()=> {robotMuted=!robotMuted;$('muteRobot').textContent=robotMuted?'Unmute robot audio':'Mute robot audio';if(socket&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'listen',enabled:!robotMuted}));};
