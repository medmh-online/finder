'use strict';
// Exercise the production wireMessages listener with synthetic events only.
// No Chrome session, WhatsApp network or Gemini key is required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {EventEmitter} = require('node:events');

const source = fs.readFileSync(path.join(__dirname,'..','whatsapp','server.js'),'utf8');
const begin = source.indexOf('function wireMessages(s,client) {');
const end = source.indexOf('\nfunction makeState(',begin);
assert.ok(begin>=0 && end>begin,'Expected source listener must be present');
const events=[];
const context={
  console:{log(){},warn(){},error(){}},
  isCurrent:()=>true,
  enqueue: ev=>events.push(ev),
  flush:async()=>{},
  keep:e=>String(e?.message || e),
  verifyLidAgainstAssignedPhones:async()=>{},
  recoverEarlierUnmatched:async()=>{},
  bridgePost:async()=>({}),
  setImmediate:fn=>setImmediate(fn),
  setTimeout:fn=>({unref(){},callback:fn}),
  clearTimeout:()=>{},
  crypto,
};
vm.runInNewContext(source.slice(begin,end)+'\nthis.wireMessages=wireMessages;',context);
const event = id=>({fromMe:false,from:'212612345678@c.us',body:'Hello',timestamp:123,id:{_serialized:id}});
const wait=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const client=new EventEmitter();
  const worker={workerId:'abcd1234',client,mode:'ai',status:'ready',verifiedPhones:new Map()};
  context.wireMessages(worker,client);
  assert.equal(client.listenerCount('ready'),1,'Ready hook must register before first inbound');
  client.emit('message_create',event('id001'));
  client.emit('message',event('id001'));
  await wait();
  assert.equal(events.length,1,'Same message on both hooks must enqueue once');
  assert.equal(client.listenerCount('ready'),1,'Inbound events must not register extra Ready listeners');
  assert.equal(events[0].senderPhone,'+212612345678');
  assert.equal(events[0].eventId,'id001');
  client.emit('message',event('id002'));
  await wait();
  assert.equal(events.length,2,'A distinct inbound message must enqueue');
  client.emit('message_create',event('id003'));
  await wait();
  assert.equal(events.length,3,'Fallback hook accepts inbound without message hook');
  // A WhatsApp-verified LID -> phone mapping is allowed; opaque LID digits are
  // never treated as the sender's telephone number.
  worker.verifiedPhones.set('1234567890123@lid','+212612345678');
  const lid={fromMe:false,from:'1234567890123@lid',body:'LID message',timestamp:124,
    id:{_serialized:'lid005'}};
  client.emit('message_create',lid);
  client.emit('message',lid);
  await wait();
  assert.equal(events.length,4,'LID event deduplicated');
  assert.equal(events[3].senderPhone,'+212612345678');
  client.emit('message_create',{fromMe:true,to:'212612345678@c.us',body:'From worker',timestamp:123,
    id:{_serialized:'out004'}});
  await wait();
  assert.equal(events.length,5,'Outgoing hooks still synchronize manual outgoing messages');
  assert.equal(events[4].direction,'out');
  console.log('PASS: fallback ingress, deduplication, distinct messages, and outgoing hook');
})().catch(e=>{console.error(e);process.exitCode=1;});
