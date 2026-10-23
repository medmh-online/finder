'use strict';
const assert = require('node:assert/strict');
const { patchMediaUtilitySource } = require('./media_id_fix');
const fixture = `exports.LoadUtils = () => {
    window.WWebJS.sendMessage = async (chat, content, options = {}) => {
        const newMsgKey = {};
        const mediaOptions = {__x_id: 'bad'};
        const message = {
            id: newMsgKey,
            ...mediaOptions,
            ...extraOptions,
        };
        // Bot's won't reply if canonicalUrl is set (linking)
        if (botOptions) delete message.canonicalUrl;
    };
    window.WWebJS.editMessage = async () => {};
};`;
const patched = patchMediaUtilitySource(fixture);
assert.equal(patched.status,'patched_in_memory');
assert.match(patched.source,/delete message\.__x_id;/);
assert.ok(patched.source.indexOf('delete message.__x_id;') < patched.source.indexOf("// Bot's won't reply"));
const again = patchMediaUtilitySource(patched.source);
assert.equal(again.status,'already_fixed');
assert.equal(again.source,patched.source);
assert.throws(() => patchMediaUtilitySource(fixture.replace('...mediaOptions,','')), /safely/);
assert.throws(() => patchMediaUtilitySource('const x=3'), /layout changed/);
console.log('4 media ID compatibility tests passed');
