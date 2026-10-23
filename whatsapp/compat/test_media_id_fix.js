'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {patchMediaUtilitySource} = require('./media_id_fix');

function fixture({indent = '        ', crlf = false, comment = '/* random downstream change */'}={}) {
  const src = `exports.LoadUtils = () => {
    window.WWebJS.sendMessage = async (chat, content, options = {}) => {
${indent}const newMsgKey = { _serialized: 'true_123456@c.us_A1' };
${indent}const mediaOptions = {__x_id: 'BROKEN_MEDIA_ID', mimetype:'application/pdf', toJSON(){return {filehash:'test'};}};
${indent}const extraOptions = {caption:'test'};
${indent}const message = {
${indent}    id: newMsgKey,
${indent}    ack: 0,
${indent}    ...mediaOptions,
${indent}    ...(mediaOptions.toJSON ? mediaOptions.toJSON() : {}),
${indent}    ...extraOptions,
${indent}};
${indent}${comment}
${indent}return message;
    };
    window.WWebJS.editMessage = async () => {};
};`;
  return crlf ? src.replace(/\n/g,'\r\n') : src;
}

function runPatched(src) {
  const context = {exports:{},window:{WWebJS:{}}};
  vm.runInNewContext(src,context,{timeout:2000});
  context.exports.LoadUtils();
  return context.window.WWebJS.sendMessage(null,null,{});
}
async function main() {
  for (const [caseName, src] of [
    ['original shape',fixture()],
    ['changed source comment',fixture({comment:'const nextStep = true;'})],
    ['different indentation',fixture({indent:'            '})],
    ['Windows CRLF',fixture({crlf:true})],
    ['nested options and comments',fixture({comment:'// trailing code',indent:'  '}).replace("ack: 0,","ack: (() => ({ nested: {count:1}, text:'}' }))(),")],
  ]) {
    const result = patchMediaUtilitySource(src);
    assert.equal(result.status,'patched_in_memory',caseName);
    assert.equal(await runPatched(result.source).then(msg=>msg.id._serialized),'true_123456@c.us_A1',caseName);
    const message = await runPatched(result.source);
    assert.equal(Object.prototype.hasOwnProperty.call(message,'__x_id'),false,caseName);
    assert.equal(message.mimetype,'application/pdf',caseName);
    assert.equal(message.filehash,'test',caseName);
    assert.equal(patchMediaUtilitySource(result.source).status,'already_fixed',caseName);
    assert.equal(patchMediaUtilitySource(result.source).source,result.source,caseName);
  }
  assert.throws(()=>patchMediaUtilitySource('const x=3'),/layout changed/);
  assert.throws(()=>patchMediaUtilitySource(fixture().replace('...mediaOptions,','')),/Unsupported outgoing/);
  assert.throws(()=>patchMediaUtilitySource(fixture().replace('id: newMsgKey,','id: 123,')),/Unsupported outgoing/);
  assert.throws(()=>patchMediaUtilitySource(fixture().replace('...extraOptions,','')),/Unsupported outgoing/);
  assert.throws(()=>patchMediaUtilitySource(fixture().replace(/\n        };\n        \/\* random downstream change \*\//,'\n        } // no semicolon')),/Expected semicolon/);
  const raw = await runPatched(fixture());
  assert.equal(raw.__x_id,'BROKEN_MEDIA_ID');
  console.log('Media source-layout and runtime regression tests passed (5 layouts, 5 safety checks).');
}
main().catch(err=>{console.error(err);process.exitCode=1;});
