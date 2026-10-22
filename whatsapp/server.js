'use strict';
/** MedMH canonical WhatsApp gateway; reuses the existing WhatsApp tab.
 * Uses Chrome --remote-debugging-port for optional AI attachment to the already-open browser.
 * Each user-controlled worker owns a unique browser profile; never uses normal Chrome's profile.
 * Experimental: AI injection can still fail due to WhatsApp Web compatibility.
 */
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { Client, NoAuth } = require('whatsapp-web.js');

const host = '127.0.0.1';
const port = 8767;
const tokenPath = process.env.MEDMH_BRIDGE_TOKEN_FILE || path.resolve(__dirname, '..', 'ai_bridge_token.secret');
const token = fs.existsSync(tokenPath) ? fs.readFileSync(tokenPath,'utf8').trim() : '';
if (!token) throw Error('Missing AI bridge secret. Run INSTALL.bat first.');
const sessionRoot = path.resolve(__dirname, 'sessions');
const backupRoot = path.resolve(__dirname, 'session_backups');
const eventRoot = path.resolve(__dirname, 'event_queue');
for (const dir of [sessionRoot,eventRoot]) fs.mkdirSync(dir,{recursive:true,mode:0o700});
if (process.platform !== 'win32') for (const dir of [sessionRoot,eventRoot]) fs.chmodSync(dir,0o700);
const idRE = /^[0-9a-f]{32}$/;
const clients = new Map();
const ATTACH_TIMEOUT_MS = 120000;
const httpError = (code,message)=>Object.assign(new Error(message),{statusCode:code});
const keep = (s,n=350)=>String(s?.message || s || '').slice(0,n);
// A fresh native profile is intentionally separate from old Puppeteer profiles.
// Those existing profiles are never reset or modified by this patch.
const profileFor = id => path.join(
  process.platform === 'win32' && process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, 'MedMH', 'WhatsAppWorkers')
    : sessionRoot,
  'worker-'+id
);
const eq = (a,b)=>{
  const x=Buffer.from(String(a||'')),y=Buffer.from(String(b||''));
  return x.length===y.length && crypto.timingSafeEqual(x,y);
};
function respond(res,status,value) {
  const body=JSON.stringify(value);
  res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Content-Length':Buffer.byteLength(body)});
  res.end(body);
}
function chromePath() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const choices = process.platform==='win32' ? [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google','Chrome','Application','chrome.exe')
  ] : ['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'];
  return choices.find(p=>fs.existsSync(p)) || undefined;
}
function chromeArgs(workerId, debugPort) {
  return [
    '--user-data-dir='+profileFor(workerId),
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port='+String(debugPort),
    '--new-window','--no-first-run','--no-default-browser-check',
    '--window-size=1150,820','https://web.whatsapp.com/'
  ];
}
function freePort() {
  // Random non-zero port avoids the Chrome special-case for --remote-debugging-port=0.
  return new Promise((resolve,reject)=>{
    const s=net.createServer();
    s.once('error',reject);
    s.listen(0,'127.0.0.1',()=>{
      const port=s.address().port;
      s.close(e=>e?reject(e):resolve(port));
    });
  });
}
async function waitForDebugger(s,timeoutMs=30000) {
  const until=Date.now()+timeoutMs;
  while(Date.now()<until && isCurrent(s)) {
    try {
      const res=await fetch('http://127.0.0.1:'+s.debugPort+'/json/version',{
        signal:AbortSignal.timeout(1800)
      });
      if(res.ok) {
        const info=await res.json();
        if(typeof info.webSocketDebuggerUrl==='string' && info.webSocketDebuggerUrl.startsWith('ws://'))return;
      }
    } catch (_) { /* Chrome may still be starting. */ }
    await new Promise(r=>setTimeout(r,350));
  }
  throw Error('Native Chrome debugging endpoint did not start. Close any old Chrome worker windows and retry.');
}
async function inspectWhatsAppTab(s) {
  // Read-only Chrome CDP HTTP endpoint. No script injection or page navigation.
  if (!s?.debugPort) return 'browser_unavailable';
  try {
    const r=await fetch('http://127.0.0.1:'+s.debugPort+'/json/list', {
      signal:AbortSignal.timeout(2200)
    });
    if (!r.ok) return 'browser_unavailable';
    const tabs=await r.json();
    if (!Array.isArray(tabs)) return 'browser_unavailable';
    const wa=tabs.find(t=>t.type==='page' &&
      /^https:\/\/web\.whatsapp\.com(?:\/|\?|$)/.test(String(t.url||'')));
    if (!wa) return 'no_whatsapp_tab';
    if (/post_logout=1|logout_reason=/i.test(wa.url)) return 'logged_out';
    return 'whatsapp_tab_found';
  } catch (_) {return 'browser_unavailable';}
}
async function probeBrowser(s) {
  if (!s?.debugPort) return false;
  try {
    const r=await fetch('http://127.0.0.1:'+s.debugPort+'/json/version',{
      signal:AbortSignal.timeout(1600)
    });
    if(!r.ok)return false;
    const d=await r.json();
    return typeof d.webSocketDebuggerUrl==='string' && d.webSocketDebuggerUrl.startsWith('ws://');
  }catch(_){return false;}
}
async function closeChrome(s) {
  // Stop ONLY the worker browser by using the debugging connection this worker created.
  // Never kill Chrome by executable name or by a PID that Windows may have handed off.
  const port=s?.debugPort;
  if (!port) return;
  try {
    const r=await fetch('http://127.0.0.1:'+port+'/json/version',{
      signal:AbortSignal.timeout(2000)
    });
    if(!r.ok) return;
    const meta=await r.json();
    if(!/^ws:\/\/(?:127\.0\.0\.1|localhost):\d+\//.test(meta.webSocketDebuggerUrl||'')) {
      console.warn('[WhatsApp] Refusing non-loopback Chrome debugging connection');
      return;
    }
    const WebSocketCtor=globalThis.WebSocket || require('ws');
    await new Promise((resolve,reject)=>{
      const ws=new WebSocketCtor(meta.webSocketDebuggerUrl);
      const timeout=setTimeout(()=>{try{ws.close();}catch(_){};reject(Error('Chrome close timed out'));},4500);
      let done=false;
      const finish=e=>{if(done)return;done=true;clearTimeout(timeout);e?reject(e):resolve();};
      const opened=()=>{
        try{ws.send(JSON.stringify({id:1,method:'Browser.close'}));
        setTimeout(()=>finish(),300).unref();}catch(e){finish(e);}
      };
      if(typeof ws.addEventListener==='function'){
        ws.addEventListener('open',opened,{once:true});
        ws.addEventListener('error',()=>finish(Error('Chrome debugging socket failed')),{once:true});
        ws.addEventListener('close',()=>finish(),{once:true});
      }else{
        ws.once('open',opened);
        ws.once('error',e=>finish(e));
        ws.once('close',()=>finish());
      }
    });
  }catch(e){console.warn('[WhatsApp] Please close this worker Chrome manually:',keep(e,180));}
  s.chrome=null;s.browserOpen=false;
}
function summarize(s) {
  if (!s) return {state:'disconnected',mode:'none',browserWindowOpen:false,qr:null};
  return {state:s.status,mode:s.mode,browserWindowOpen:!!s.browserOpen,aiConnected:s.status==='ready',
    number:s.number||null,error:s.error||null,qr:null, workerId:s.workerId,
    attachStage:s.attachStage||null,tabStatus:s.tabStatus||null,
    attachInProgress:s.mode==='ai' && ['ai_starting','authenticated'].includes(s.status)};
}
async function eventPost(event) {
  const r=await fetch('http://127.0.0.1:8766/inbound',{
    method:'POST',headers:{'Content-Type':'application/json','x-bridge-secret':token},
    body:JSON.stringify(event),signal:AbortSignal.timeout(12000)
  });
  if (!r.ok) throw Error('AI gateway HTTP '+r.status);
}
// Recover WhatsApp's opaque LID identities from *verified* registrations
// of phone numbers explicitly assigned to this worker. This is bounded and
// independent of queue sending: it never messages an assigned lead.
const resolvingLids = new Set();
async function bridgePost(path,payload,timeout=12000) {
  const r=await fetch('http://127.0.0.1:8766'+path,{
    method:'POST',headers:{'Content-Type':'application/json','x-bridge-secret':token},
    body:JSON.stringify(payload),signal:AbortSignal.timeout(timeout)
  });
  if(!r.ok)throw Error('AI bridge HTTP '+r.status);
  return await r.json();
}
async function verifyLidAgainstAssignedPhones(s, lid) {
  if(!isCurrent(s)||s.status!=='ready'||!/^\d{9,20}@lid$/.test(lid))return;
  const key=s.workerId+':'+lid;
  if(resolvingLids.has(key))return;
  // Cache unresolved lookup attempts briefly, not forever: WhatsApp mappings
  // may become available when the user opens a conversation later.
  s.identityRetryAt ||= new Map();
  if((s.identityRetryAt.get(lid)||0)>Date.now())return;
  s.identityRetryAt.set(lid,Date.now()+180000);
  resolvingLids.add(key);
  try {
    const d=await bridgePost('/match-candidates',{workerId:s.workerId,chatId:lid});
    if(d.reason){console.log('[WhatsApp identity]',s.workerId.slice(0,8),d.reason);return;}
    const candidates=Array.isArray(d.phones)?d.phones:[];
    // Never guess from the digits in LID, contact name, or message text.
    for(const phone of candidates) {
      if(!isCurrent(s)||s.status!=='ready')break;
      if(!/^\+[1-9]\d{8,14}$/.test(phone))continue;
      try {
        const lookup=await Promise.race([
          s.client.getNumberId(phone.slice(1)+'@c.us'),
          new Promise((_,reject)=>setTimeout(()=>reject(Error('Registration timeout')),7000))
        ]);
        const id=String(lookup?._serialized||'');
        let verified = id===lid;
        // Some WhatsApp releases return @c.us for getNumberId. Ask WhatsApp
        // for its related LID, not the app for a guessed conversion.
        if(!verified && /^\d{9,15}@c\.us$/.test(id) &&
           typeof s.client.getContactLidAndPhone==='function') {
          const pairs=await Promise.race([
            s.client.getContactLidAndPhone([id]),
            new Promise((_,reject)=>setTimeout(()=>reject(Error('LID mapping timeout')),7000))
          ]);
          verified=Array.isArray(pairs) && pairs.some(x=>x?.lid===lid && x?.pn===id);
        }
        if(verified) {
          const linked=await bridgePost('/resolve-identity',{
            workerId:s.workerId,chatId:lid,verifiedPhone:phone
          });
          if(linked.leadId!==null && linked.leadId!==undefined) {
            s.verifiedPhones.set(lid,phone);
            s.identityRetryAt.delete(lid);
            console.log('[WhatsApp identity]',s.workerId.slice(0,8),'LID mapped to assigned lead');
            return;
          }
        }
      }catch(e){console.warn('[WhatsApp identity] lookup:',keep(e,120));}
    }
    console.log('[WhatsApp identity]',s.workerId.slice(0,8),'LID unmatched after verified checks');
  }catch(e){console.warn('[WhatsApp identity] recovery:',keep(e,140));}
  finally{resolvingLids.delete(key);}
}
async function recoverEarlierUnmatched(s){
  if(!isCurrent(s)||s.status!=='ready')return;
  try {
    const d=await bridgePost('/match-candidates',{workerId:s.workerId});
    if(!d.reason && Array.isArray(d.unmatchedChats)) {
      for(const lid of d.unmatchedChats.slice(0,8)) {
        if(!isCurrent(s)||s.status!=='ready')break;
        await verifyLidAgainstAssignedPhones(s,lid);
      }
    }
  } catch(e){console.warn('[WhatsApp identity] history recovery:',keep(e,120));}
}

function enqueue(event) {
  const filename=path.join(eventRoot,crypto.createHash('sha256').update(event.workerId+':'+event.eventId).digest('hex')+'.json');
  if (fs.existsSync(filename)) return;
  const temp=filename+'.'+crypto.randomUUID()+'.tmp';
  try {fs.writeFileSync(temp,JSON.stringify(event),{flag:'wx'});fs.renameSync(temp,filename);}
  finally {try {fs.unlinkSync(temp);} catch (_) {}}
}
let flushing=false;
async function flush() {
  if (flushing) return;
  flushing=true;
  try {
    for (const f of fs.readdirSync(eventRoot).filter(x=>x.endsWith('.json')).slice(0,80)) {
      const filename=path.join(eventRoot,f);
      try {await eventPost(JSON.parse(fs.readFileSync(filename,'utf8')));fs.unlinkSync(filename);}
      catch (e) {console.error('[event queue]',keep(e,200));break;}
    }
  } finally {flushing=false;}
}
setInterval(()=>flush().catch(e=>console.error('[event queue]',keep(e))),5000).unref();

function isCurrent(s) {return s.active && clients.get(s.workerId)===s;}
async function detachClient(client) {
  if(!client)return;
  // CRITICAL: client.destroy() calls browser.close(). With browserURL this would
  // terminate the user's ordinary Chrome window. Disconnect CDP instead.
  try {client.removeAllListeners();} catch (_) {}
  // The AI now owns the EXISTING WhatsApp tab: never close it on detach.
  try {client.pupBrowser?.disconnect();}catch(e){console.warn('[WhatsApp] Detaching CDP:',keep(e,150));}
}
function wireMessages(s,client) {
  const valid=()=>isCurrent(s) && s.client===client && s.mode==='ai';
  client.on('qr',()=>{if(valid())console.warn('[WhatsApp]',s.workerId.slice(0,8),'AI found unpaired session; scan in Chrome window.');});
  client.on('authenticated',()=>{if(valid()){s.status='authenticated';s.attachStage='authenticated';console.log('[WhatsApp]',s.workerId.slice(0,8),'authenticated');}});
  client.on('ready',()=>{
    if (!valid()) return;
    s.status='ready';s.attachStage='ready';s.error=null;s.browserOpen=true;
    if(s.attachWatchdog){clearTimeout(s.attachWatchdog);s.attachWatchdog=null;}s.number=client.info?.wid?.user || null;
    console.log('[WhatsApp]',s.workerId.slice(0,8),'AI ready',s.number);
    setTimeout(()=>recoverEarlierUnmatched(s).catch(e=>console.warn('[WhatsApp identity]',keep(e))),3500).unref();
  });
  client.on('auth_failure',e=>{if(valid()){s.status='auth_failure';s.attachStage='authentication_failed';s.error=keep(e);}});
  client.on('disconnected',e=>{if(valid()){s.status='ai_disconnected';s.attachStage='disconnected';s.error='WhatsApp disconnected: '+keep(e);s.browserOpen=!!client.pupBrowser?.isConnected?.();}});
  client.on('message',m=>{
    if (!valid() || m.fromMe || !m.from || !/@(?:c\.us|lid)$/.test(m.from)) return;
    // The chat can be a WhatsApp opaque @lid ID, which is NOT a telephone
    // number. Only trust a contact phone if WhatsApp exposes a real @c.us ID.
    void (async()=>{
      let senderPhone=s.verifiedPhones.get(m.from)||null;
      if (/^\d{9,15}@c\.us$/.test(m.from)) {
        senderPhone='+'+m.from.split('@')[0];
      } else if (m.from.endsWith('@lid')) {
        try {
          const contact=await Promise.race([
            m.getContact(),
            new Promise((_,reject)=>setTimeout(()=>reject(Error('contact lookup timed out')),3500))
          ]);
          const phoneId=String(contact?.id?._serialized||'');
          if (/^\d{9,15}@c\.us$/.test(phoneId))senderPhone='+'+phoneId.split('@')[0];
        } catch(e) {
          console.warn('[WhatsApp] LID contact lookup:',keep(e,100));
        }
        // WhatsApp's opaque LID is not a phone number. Use its native LID->PN
        // mapping where available, never infer identity from LID digits or names.
        if (!senderPhone) {
          try {
            const lookup=async()=>{
              if(typeof client.getContactLidAndPhone==='function'){
                try {
                  const list=await client.getContactLidAndPhone([m.from]);
                  if(/^\d{9,15}@c\.us$/.test(String(list?.[0]?.pn||'')))return list[0].pn;
                }catch(_) {/* Try WhatsApp Store's direct resolver below. */}
              }
              return await client.pupPage.evaluate(async lid=>{
                const factory=window.Store?.WidFactory;
                const utils=window.Store?.LidUtils;
                if(!factory || !utils?.getPhoneNumber)return '';
                const phone=await utils.getPhoneNumber(factory.createWid(lid));
                return String(phone?._serialized||'');
              },m.from);
            };
            const pn=await Promise.race([lookup(),new Promise((_,reject)=>
              setTimeout(()=>reject(Error('LID phone lookup timed out')),4500))]);
            if (/^\d{9,15}@c\.us$/.test(pn))senderPhone='+'+pn.split('@')[0];
          } catch(e) { console.warn('[WhatsApp] LID phone mapping unavailable:',keep(e,120)); }
        }
      }
      if(!valid())return;
      if(senderPhone && s.verifiedPhones.size<2500)s.verifiedPhones.set(m.from,senderPhone);
      const ev={workerId:s.workerId,eventId:m.id?._serialized || `${m.from}:${m.timestamp}:${crypto.randomUUID()}`,
        chatId:m.from,text:String(m.body||'').slice(0,3000),sentAt:m.timestamp};
      if(senderPhone)ev.senderPhone=senderPhone;
      enqueue(ev);flush().catch(console.error);
      if(!senderPhone && m.from.endsWith('@lid')) {
        setImmediate(()=>verifyLidAgainstAssignedPhones(s,m.from).catch(e=>
          console.warn('[WhatsApp identity] sender:',keep(e))));
      }
    })().catch(e=>console.error('[inbound]',keep(e)));
  });
  client.on('message_create',m=>{
    if(!valid() || !m.fromMe || !m.to || !/@(?:c\.us|lid)$/.test(m.to)) return;
    const ev={workerId:s.workerId,eventId:m.id?._serialized || `${m.to}:${m.timestamp}:${crypto.randomUUID()}`,
      chatId:m.to,text:String(m.body||'').slice(0,3000),sentAt:m.timestamp,direction:'out'};
    // A manual outgoing conversation can later identify the same LID chat.
    void (async()=>{
      if(m.to.endsWith('@lid')){
        try {
          const resolve=async()=>{
            if(typeof client.getContactLidAndPhone==='function'){
              try {
                const data=await client.getContactLidAndPhone([m.to]);
                if(/^\d{9,15}@c\.us$/.test(String(data?.[0]?.pn||'')))return data[0].pn;
              }catch(_) {/* Try the active WhatsApp Store. */}
            }
            return client.pupPage.evaluate(async lid=>{
              const f=window.Store?.WidFactory,u=window.Store?.LidUtils;
              return String((f&&u?.getPhoneNumber?await u.getPhoneNumber(f.createWid(lid)):null)?._serialized||'');
            },m.to);
          };
          const pn=await Promise.race([resolve(),new Promise((_,reject)=>setTimeout(()=>reject(Error('Timeout')),4500))]);
          if(/^\d{9,15}@c\.us$/.test(pn))ev.senderPhone='+'+pn.split('@')[0];
        }catch(e){console.warn('[WhatsApp] Outgoing LID mapping unavailable:',keep(e,100));}
      }
      if(valid()){enqueue(ev);flush().catch(console.error);}
    })().catch(e=>console.error('[outbound]',keep(e)));
  });
}
function makeState(id) {
  return {workerId:id,status:'browser_starting',mode:'manual',error:null,number:null,
    chrome:null,chromeExit:null,debugPort:null,browserOpen:false,client:null,active:true,task:null,
    verifiedPhones:new Map(),
    attachStage:null,tabStatus:null,attachWatchdog:null,attachAttempt:0};
}
async function launchBrowser(s) {
  if(!isCurrent(s))return;
  s.mode='manual';s.status='browser_starting';s.attachStage='launching_chrome';s.browserOpen=false;
  const id=s.workerId.slice(0,8);
  try {
    const binary=chromePath();
    if(!binary)throw Error('Google Chrome not found. Install Chrome or set CHROME_PATH.');
    fs.mkdirSync(profileFor(s.workerId),{recursive:true,mode:0o700});
    s.debugPort=await freePort();
    if(!isCurrent(s))return;
    // Direct Chrome process. No Puppeteer.launch(), automation flags, or QR interception.
    const child=spawn(binary,chromeArgs(s.workerId,s.debugPort),{
      stdio:'ignore',windowsHide:false,detached:process.platform==='win32'
    });
    if(process.platform==='win32')child.unref();
    s.chrome=child;
    child.once('error',e=>{
      s.chromeExit=true;
      if(isCurrent(s)){s.status='error';s.error='Chrome launch failed: '+keep(e);s.browserOpen=false;}
    });
    child.once('exit',(code,signal)=>{
      s.chromeExit={code,signal};
      // Chrome's first executable can hand the window to another process and exit 0.
      // The remote debugging endpoint (not the original PID) determines whether it is running.
      console.log('[WhatsApp]',id,'Chrome launcher exited',code,signal,
        '- checking the actual browser through CDP');
    });
    await waitForDebugger(s);
    if(!isCurrent(s))return;
    s.status='browser_open';s.attachStage='waiting_for_sign_in';s.browserOpen=true;s.error=null;
    s.tabStatus=await inspectWhatsAppTab(s);
    console.log('[WhatsApp]',id,'native Chrome open on local debugging port',s.debugPort, 'profile',profileFor(s.workerId),
      '- scan native QR, then click Start AI');
  }catch(e) {
    if(isCurrent(s)) {s.status='error';s.attachStage='launch_error';s.error='Native Chrome startup: '+keep(e);s.browserOpen=false;}
    console.error('[WhatsApp]',id,'native browser failed:',keep(e));
  }
}
async function openBrowser(id) {
  if(!idRE.test(id))throw Error('Invalid worker ID');
  const prev=clients.get(id);
  if(prev?.active) {
    if(prev.browserOpen || ['browser_starting','ai_starting','authenticated','ready','browser_only_error'].includes(prev.status)) {
      return summarize(prev); // Don't open a second window for the same worker.
    }
    await stop(id,false);
  }
  const s=makeState(id);
  clients.set(id,s);
  s.task=launchBrowser(s).catch(e=>{
    if(isCurrent(s)){s.status='error';s.error=keep(e);}
  });
  return summarize(s);
}
async function activateAI(id) {
  if(!idRE.test(id))throw httpError(400,'Invalid worker ID');
  const s=clients.get(id);
  if(!s || !s.active) throw httpError(409,'Open this worker’s Chrome window first.');
  // Streamlit reruns and double clicks must NEVER start a second CDP client.
  if(s.mode==='ai' && ['ai_starting','authenticated','ready'].includes(s.status))
    return summarize(s);
  if(s.status==='browser_starting')return summarize(s);
  if(s.mode==='ai')throw httpError(409,
    'Previous AI connection is not ready. Stop this worker (keeping its login) and reopen it before retrying.');
  if(!s.debugPort || !(await probeBrowser(s))){
    s.status='browser_closed';s.browserOpen=false;s.attachStage='browser_missing';
    throw httpError(409,'This worker’s Chrome debugging window is not running. Open native WhatsApp Chrome first.');
  }
  s.browserOpen=true;
  s.tabStatus=await inspectWhatsAppTab(s);
  if(s.tabStatus==='logged_out')throw httpError(409,
    'WhatsApp Web has redirected to a logged-out page. Sign in again in the same worker Chrome window before attaching AI.');
  if(s.tabStatus!=='whatsapp_tab_found')throw httpError(409,
    'Could not find an active WhatsApp Web tab in this worker Chrome. Open web.whatsapp.com in that window and wait for it to load.');
  if(!['browser_open','browser_only_error'].includes(s.status))
    throw httpError(409,`Worker currently ${s.status}. Refresh status before attaching.`);
  s.mode='ai';s.status='ai_starting';s.attachStage='attaching_to_existing_tab';s.error=null;
  const attempt=++s.attachAttempt;
  // Never close Chrome on failure. Only detach our debugging connection.
  s.attachWatchdog=setTimeout(async()=>{
    if(!isCurrent(s)||s.mode!=='ai'||s.attachAttempt!==attempt||s.status==='ready')return;
    const client=s.client;s.client=null;s.mode='manual';s.status='browser_only_error';
    s.attachStage='timed_out';
    s.error='AI connection did not become ready within two minutes. Chrome is still available. Do not repeatedly click Attach; check the terminal for the original error.';
    s.browserOpen=await probeBrowser(s);
    await detachClient(client);
    console.error('[WhatsApp]',id.slice(0,8),'AI attach timed out, retained normal Chrome window');
  },ATTACH_TIMEOUT_MS);
  s.attachWatchdog.unref?.();
  s.task=(async()=>{
    let client;
    try {
      // v2.2.5 patches whatsapp-web.js to REUSE the existing WhatsApp page.
      client=new Client({
        authStrategy:new NoAuth(),authTimeoutMs:120000,qrMaxRetries:0,
        userAgent:false,
        puppeteer:{browserURL:'http://127.0.0.1:'+s.debugPort,defaultViewport:null,
          protocolTimeout:120000}
      });
      s.client=client;
      wireMessages(s,client);
      await client.initialize();
      if(!isCurrent(s)||s.mode!=='ai'||s.attachAttempt!==attempt){
        await detachClient(client);return;
      }
      try {await client.pupPage?.bringToFront();}catch(_){}
      if(s.status!=='ready')s.attachStage='waiting_for_ready_event';
      console.log('[WhatsApp]',id.slice(0,8),'AI init returned; state',s.status);
    }catch(e){
      const msg=keep(e,400);
      console.error('[WhatsApp]',id.slice(0,8),'AI attach failed:',msg);
      await detachClient(client);
      if(!isCurrent(s)||s.attachAttempt!==attempt||s.mode!=='ai')return;
      s.client=null;s.mode='manual';s.status='browser_only_error';
      s.attachStage='library_attach_failed';
      s.browserOpen=await probeBrowser(s);
      s.tabStatus=await inspectWhatsAppTab(s);
      s.error='AI library failed to attach: '+msg +
        (s.tabStatus==='logged_out'?' (WhatsApp redirected to its logout page)':'');
    }finally{
      if(isCurrent(s)&&s.attachAttempt===attempt && s.attachWatchdog &&
          (s.mode!=='ai'||s.status==='ready')) {
        clearTimeout(s.attachWatchdog);s.attachWatchdog=null;
      }
    }
  })();
  return summarize(s);
}
async function stop(id,logout=false) {
  const s=clients.get(id);if(!s)return;
  s.active=false;s.mode='stopping';s.status='stopping';
  if(s.attachWatchdog){clearTimeout(s.attachWatchdog);s.attachWatchdog=null;}
  clients.delete(id);
  if(logout && s.client) {
    try {await s.client.logout();}catch(e){console.warn('[WhatsApp] logout:',keep(e,120));}
  }
  await detachClient(s.client);
  await closeChrome(s);
}
async function backupAndResetPairing(id) {
  await stop(id,false);
  const src=profileFor(id);
  if(!fs.existsSync(src))return {reset:true,backup:null};
  fs.mkdirSync(backupRoot,{recursive:true,mode:0o700});
  const dst=path.join(backupRoot,'native-'+id+'-'+new Date().toISOString().replace(/[:.]/g,'-'));
  fs.renameSync(src,dst);
  return {reset:true,backup:dst};
}
async function handler(req,res) {
  try {
    if(!eq(req.headers['x-bridge-secret'],token))return respond(res,403,{error:'forbidden'});
    if(req.method==='GET' && req.url==='/health')return respond(res,200,{ready:true,mode:'browser-first'});
    if(req.method!=='POST')return respond(res,404,{error:'not found'});
    let body='';for await (const chunk of req) {body+=chunk.toString();if(body.length>8000)throw Error('Request too large');}
    const data=JSON.parse(body||'{}');const id=String(data.workerId||'');
    if(!idRE.test(id))return respond(res,400,{error:'invalid worker ID'});
    if(req.url==='/open-browser' || req.url==='/connect')return respond(res,200,await openBrowser(id));
    if(req.url==='/activate')return respond(res,200,await activateAI(id));
    if(req.url==='/status'){
      const current=clients.get(id);
      if(current && current.browserOpen && !(await probeBrowser(current))){
        current.browserOpen=false;
        if(current.status!=='stopping'){
          current.status='browser_closed';
          current.error='Chrome debugging endpoint is no longer active. Close any stale worker window and reopen this worker.';
        }
      }
      if(current && current.browserOpen) current.tabStatus=await inspectWhatsAppTab(current);
      return respond(res,200,summarize(current));
    }
    if(req.url==='/stop') {await stop(id,false);return respond(res,200,{stopped:true});}
    if(req.url==='/logout') {
      const s=clients.get(id);
      if(s?.mode==='manual')return respond(res,409,{error:'To log out a browser-only session, use WhatsApp Web menu or your phone Linked Devices.'});
      await stop(id,true);return respond(res,200,{stopped:true});
    }
    if(req.url==='/reset-pairing') {
      if(data.confirmation!=='BACKUP_AND_RESET_PAIRING')return respond(res,400,{error:'Explicit confirmation required'});
      return respond(res,200,await backupAndResetPairing(id));
    }
    if(req.url==='/recover-identities') {
      const s=clients.get(id);
      if(!s || !s.active || s.mode!=='ai' || s.status!=='ready')
        return respond(res,409,{error:'WhatsApp worker must be READY'});
      if(s.recoveryRunning)return respond(res,200,{running:true});
      if(s.lastManualRecoveryAt && Date.now()-s.lastManualRecoveryAt<120000)
        return respond(res,200,{cooldown:true});
      s.lastManualRecoveryAt=Date.now();
      // Manual troubleshooting may recheck a recent unresolved mapping immediately.
      s.identityRetryAt?.clear();
      s.recoveryRunning=true;
      setImmediate(() => {
        recoverEarlierUnmatched(s)
          .catch(e=>console.warn('[WhatsApp identity] manual recovery:',keep(e,140)))
          .finally(()=>{s.recoveryRunning=false;});
      });
      return respond(res,200,{started:true});
    }
    if(req.url==='/verify-number') {
      const s=clients.get(id);
      if(!s || !s.active || s.mode!=='ai' || s.status!=='ready')
        return respond(res,409,{error:'Worker must be READY to verify WhatsApp registration'});
      const number=String(data.number||'');
      if(!/^\+[1-9]\d{8,14}$/.test(number))return respond(res,400,{error:'E.164 number required'});
      const chatId=number.slice(1)+'@c.us';
      const lookup=await Promise.race([
        s.client.getNumberId(chatId),
        new Promise((_,reject)=>setTimeout(()=>reject(httpError(503,'WhatsApp check timed out')),9000))
      ]);
      // A negative result is a registration check, not an opt-in.
      return respond(res,200,{registered:!!lookup?._serialized,
        chatId:lookup?._serialized||null});
    }
    if(req.url==='/send') {
      const s=clients.get(id);
      if(!s || !s.active || s.mode!=='ai' || s.status!=='ready')return respond(res,409,{error:'Worker AI not connected'});
      const chat=String(data.chatId||'');const message=String(data.text||'');
      if(!/^\d{9,20}@(c\.us|lid)$/.test(chat)||!message.trim()||message.length>3000)
        return respond(res,400,{error:'invalid chat or message'});
      // Typing state is optional; failure must never prevent a legitimate reply.
      // The call is serialized per worker so two replies never type/send together.
      if(s.sending)return respond(res,409,{error:'Worker already sending a message'});
      s.sending=true;
      let chatObject=null;
      try {
        if(data.reply===true) {
          const lo=Math.max(0,Math.min(60,Number(data.delayMin)||0));
          const hi=Math.max(lo,Math.min(60,Number(data.delayMax)||lo));
          const waitSeconds=lo+Math.floor(Math.random()*(hi-lo+1));
          try {
            chatObject=await s.client.getChatById(chat);
            if(chatObject && typeof chatObject.sendStateTyping==='function')
              await chatObject.sendStateTyping();
          }catch(e){console.warn('[WhatsApp] Typing indicator unavailable:',keep(e,100));}
          if(waitSeconds>0)await new Promise(resolve=>setTimeout(resolve,waitSeconds*1000));
          if(!s.active || s.status!=='ready')throw httpError(409,'Worker disconnected before send');
        }
        // Some WhatsApp Web releases show the outgoing message even when
        // sendMessage() returns a Message without its serialized ID.
        // Listen BEFORE sending for a real outgoing message_create event.
        // If neither source provides an ID, report uncertainty; NEVER retry.
        let acknowledgeEcho;
        const echo = new Promise(resolve => { acknowledgeEcho = resolve; });
        const messageIdOf = m => {
          const id = m?.id?._serialized;
          return typeof id === 'string' && id.length > 0 ? id : null;
        };
        const onOutgoing = m => {
          if (m?.fromMe === true && m.to === chat && m.body === message) {
            const confirmedId = messageIdOf(m);
            if (confirmedId) acknowledgeEcho(confirmedId);
          }
        };
        s.client.on('message_create',onOutgoing);
        let messageId = null;
        let confirmedBy = null;
        try {
          const sent = await s.client.sendMessage(chat,message,{sendSeen:false});
          messageId = messageIdOf(sent);
          if (messageId) confirmedBy = 'sendMessage';
          if (!messageId) {
            messageId = await Promise.race([
              echo,
              new Promise(resolve => setTimeout(() => resolve(null), 4500))
            ]);
            if (messageId) confirmedBy = 'message_create';
          }
        } finally {
          s.client.off('message_create',onOutgoing);
        }
        console.log('[WhatsApp] send outcome',id.slice(0,8),messageId ? 'confirmed' : 'uncertain', confirmedBy || 'no-id');
        return respond(res,200,{sent:Boolean(messageId),messageId,confirmedBy});
      } finally {
        s.sending=false;
        try {await chatObject?.clearState?.();}catch(_){}
      }
    }
    respond(res,404,{error:'unknown endpoint'});
  }catch(e){console.error('[HTTP]',keep(e,240));respond(res,e.statusCode||500,{error:keep(e,250)});}
}
process.on('unhandledRejection',e=>{
  // Older whatsapp-web.js builds sometimes reject inject() during internal navigation.
  // Log this for diagnostics; never mark another worker connected or alter another session.
  console.error('[WhatsApp] Background rejection:',keep(e,420));
});
if(require.main===module) {
  http.createServer(handler).listen(port,host,()=>console.log(`WhatsApp gateway: http://${host}:${port} [native Chrome per worker]`));
  flush().catch(console.error);
}
module.exports={profileFor,chromeArgs,summarize,openBrowser,activateAI,stop,handler,clients,inspectWhatsAppTab};
