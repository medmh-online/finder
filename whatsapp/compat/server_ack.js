'use strict';
/** Confirm outbound WhatsApp messages using a server ACK, not message_create.
 * An ID or a locally-created outgoing message is NOT proof the server accepted it.
 * This helper registers listeners BEFORE sendMessage, preserving early ACKs.
 */
const { messageIdOf, outgoingMediaEchoMatches } = require('./message_events');

async function sendWithServerAck(client, { chatId, text, kind, hasMedia, send, timeoutMs=40000 }) {
  const candidates = new Set();
  const acknowledgments = new Map();
  let notify;
  const result = new Promise(resolve => { notify=resolve; });
  let finished=false;
  const check = () => {
    if (finished) return;
    for (const id of candidates) {
      const ack=acknowledgments.get(id);
      if (typeof ack==='number' && ack>=1) {
        finished=true;
        notify({sent:true,messageId:id,confirmedBy:'server_ack',ack});
        return;
      }
      if (ack===-1) {
        finished=true;
        notify({sent:false,messageId:id,confirmedBy:null,reason:'server_rejected',ack});
        return;
      }
    }
  };
  const onCreate = m => {
    if (!outgoingMediaEchoMatches(m,chatId,kind,text,hasMedia)) return;
    candidates.add(messageIdOf(m));
    check();
  };
  const onAck = (m,ack) => {
    // The SDK may expose the recipient as a LID alias on ACK even when the
    // requested chat was a phone ID. Match strictly by REAL message ID below.
    if (m?.fromMe!==true) return;
    const id=messageIdOf(m);
    if (!id || !Number.isInteger(ack)) return;
    acknowledgments.set(id,ack);
    check();
  };
  client.on('message_create',onCreate);
  client.on('message_ack',onAck);
  let timer;
  try {
    const msg=await send();
    const returnedId=messageIdOf(msg);
    if (returnedId) {
      candidates.add(returnedId);
      if (Number.isInteger(msg.ack)) acknowledgments.set(returnedId,msg.ack);
    }
    check();
    if (!finished) {
      const timeout=new Promise(resolve=>{
        timer=setTimeout(()=>resolve({sent:false,messageId:returnedId||[...candidates][0]||null,
          confirmedBy:null,reason:'server_ack_timeout'}),timeoutMs);
      });
      return await Promise.race([result,timeout]);
    }
    return await result;
  } finally {
    clearTimeout(timer);
    client.off('message_create',onCreate);
    client.off('message_ack',onAck);
  }
}
module.exports={sendWithServerAck};
