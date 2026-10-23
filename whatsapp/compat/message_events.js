'use strict';
/** Read message IDs from both the legacy and newer WhatsApp Web shapes.
 * No guessed success: IDs still need an actual WhatsApp Message object.
 */
const crypto = require('node:crypto');
function messageIdOf(m) {
  if (!m || typeof m !== 'object') return null;
  const id=m.id;
  if (!id || typeof id !== 'object') return null;
  for (const field of ['_serialized','$1']) {
    const raw=id[field];
    if (typeof raw==='string' && raw.length>0 && raw.length<180) return raw;
  }
  const remote=id.remote?._serialized || id.remote?.$1 || id.remote;
  if (typeof remote==='string' && /^\d{9,20}@(c\.us|lid)$/.test(remote) &&
      typeof id.id==='string' && /^[A-Za-z0-9_-]{8,80}$/.test(id.id) &&
      typeof id.fromMe==='boolean') {
    return `${id.fromMe}_${remote}_${id.id}`;
  }
  return null;
}
function eventIdOf(m, chatId, direction) {
  const actual=messageIdOf(m);
  if (actual && actual.length<=150) return actual;
  // Stable fallback only for dispatch deduplication, NOT delivery confirmation.
  // Hashing also prevents unusual payloads appearing in log lines or DB IDs.
  return 'fallback-'+crypto.createHash('sha256').update(JSON.stringify([
    direction,chatId,String(m?.id?.id || ''),String(m?.timestamp || ''),String(m?.body || '')
  ])).digest('hex');
}
function outgoingMediaEchoMatches(m, target, kind, caption, isMedia) {
  if (m?.fromMe!==true || m?.to!==target || !messageIdOf(m)) return false;
  if (!isMedia) return m.hasMedia!==true && String(m.body||'')===String(caption||'');
  const type=String(m.type||'').toLowerCase();
  const kindMatches=(kind==='document'&&type==='document') ||
    (kind==='image'&&type==='image') ||
    (kind==='voice'&&(type==='ptt'||type==='audio'));
  if (!kindMatches || m.hasMedia===false) return false;
  // WhatsApp Web document bodies can be filenames, not captions. During a
  // worker-serialized send we accept a real outgoing media event for this chat,
  // but never a text echo or another recipient's message.
  if(kind==='voice')return true;
  const received=String(m.body||'');
  return !received || received===String(caption||'').slice(0,1000) ||
    (kind==='document' && /\.pdf$/i.test(received));
}
module.exports={messageIdOf,eventIdOf,outgoingMediaEchoMatches};
