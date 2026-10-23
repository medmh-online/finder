'use strict';
const assert = require('node:assert/strict');
const EventEmitter=require('node:events');
const {sendWithServerAck}=require('./server_ack');
const chat='212612345671@c.us';
function msg(id,kind='chat',caption='Hello',ack=0){return {id:{_serialized:id},to:chat,fromMe:true,body:caption,type:kind,hasMedia:kind!=='chat',ack};}
async function run(){
  // A locally-created text with a valid ID is NOT a server-confirmed send.
  {const c=new EventEmitter();const r=await sendWithServerAck(c,{chatId:chat,text:'Hello',hasMedia:false,
     timeoutMs:15,send:async()=>{c.emit('message_create',msg('local'));return msg('local');}});
   assert.equal(r.sent,false);assert.equal(r.reason,'server_ack_timeout');assert.equal(c.listenerCount('message_ack'),0);}
  // ACK1 may arrive before the asynchronous SDK send resolves.
  {const c=new EventEmitter();const r=await sendWithServerAck(c,{chatId:chat,text:'Hello',hasMedia:false,
     timeoutMs:30,send:async()=>{const m=msg('confirmed');c.emit('message_create',m);c.emit('message_ack',m,1);return m;}});
   assert.equal(r.sent,true);assert.equal(r.confirmedBy,'server_ack');}
  // An unrelated ACK from another chat or another ID must never confirm.
  {const c=new EventEmitter();const r=await sendWithServerAck(c,{chatId:chat,text:'Hello',hasMedia:false,
     timeoutMs:20,send:async()=>{const m=msg('expected');c.emit('message_ack',{...msg('other'),to:'212611111111@c.us'},2);return m;}});
   assert.equal(r.sent,false);}
  // PDF message_create alone is insufficient; document ACK confirms.
  {const c=new EventEmitter();const r=await sendWithServerAck(c,{chatId:chat,text:'Voici le document demandé 😊',kind:'document',hasMedia:true,
     timeoutMs:40,send:async()=>{const m=msg('pdf','document','document.pdf');c.emit('message_create',m);setTimeout(()=>c.emit('message_ack',m,1),4);return m;}});
   assert.equal(r.sent,true);assert.equal(r.messageId,'pdf');}
  // An explicit server failure must not be marked sent.
  {const c=new EventEmitter();const r=await sendWithServerAck(c,{chatId:chat,text:'Hello',hasMedia:false,
     timeoutMs:30,send:async()=>{const m=msg('reject');setTimeout(()=>c.emit('message_ack',m,-1),4);return m;}});
   assert.equal(r.sent,false);assert.equal(r.reason,'server_rejected');}
  console.log('5 WhatsApp server ACK integrity tests passed');
}
run().catch(err=>{console.error(err);process.exitCode=1;});
