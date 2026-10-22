'use strict';
/** Fresh, per-client Puppeteer callback aliases for external-Chrome attachment.
 * Prevents a leftover onAppStateHasSyncedEvent (or message event) from pointing
 * to an older whatsapp-web.js Client when Chrome remains open between attempts.
 * Does not forge a READY event: the library must finish its own setup.
 */
const MARKER = 'MEDMH_FRESH_CDP_CALLBACK_BINDINGS_V2211';
const HELPER = `
// ${MARKER}: attach each new CDP client to fresh page callback bindings.
const medmhBridgeCrypto = require('node:crypto');
async function medmhExposeFunction(page, name, fn) {
    if (!page.__medmhFreshBindings) {
        return exposeFunctionIfAbsent(page, name, fn);
    }
    // Bind with a never-reused name; a previous CDP client may have left a
    // window.on... function behind that still points to its old Node handler.
    const privateName = '__medmhBridge_' +
        medmhBridgeCrypto.randomBytes(12).toString('hex');
    const wrapped = async (...args) => {
        const isReady = name === 'onAppStateHasSyncedEvent';
        if (isReady) console.log('[MedMH bridge] Authenticated sync callback started');
        try {
            const result = await fn(...args);
            if (isReady) console.log('[MedMH bridge] Ready callback completed');
            return result;
        } catch (error) {
            console.error('[MedMH bridge] ' + name + ' failed:', error?.stack || String(error));
            throw error;
        }
    };
    await page.exposeFunction(privateName, wrapped);
    await page.evaluate((publicName, boundName) => {
        // Only replace wwebjs's own JavaScript callback, never the WhatsApp
        // Socket/AuthStore, saved session, or the actual WhatsApp page.
        const fresh = window[boundName];
        if (typeof fresh !== 'function') throw new Error('Fresh CDP callback missing');
        window[publicName] = fresh;
        if (window[publicName] !== fresh) {
            throw new Error('Cannot rebind stale CDP callback ' + publicName);
        }
    }, name, privateName);
}
`;
function patchClient(input) {
  if (input.includes(MARKER)) return { code: input, alreadyPatched: true };
  if (!input.includes('MEDMH_CDP_REUSE_EXISTING_WHATSAPP_TAB_V225')) throw Error('Missing v2.2.5 existing-tab compatibility patch.');
  if (!input.includes('MEDMH_ALREADY_SYNCED_READY_CHECK_V229')) throw Error('Missing v2.2.9 synced-event patch.');
  const anchor = "const { exposeFunctionIfAbsent } = require('./util/Puppeteer');";
  if (input.split(anchor).length !== 2) throw Error('Unsupported Client.js: helper import not found exactly once.');
  const flagAnchor = 'this.pupPage = page;';
  if (input.split(flagAnchor).length !== 2) throw Error('Unsupported Client.js: page assignment not found exactly once.');
  const matching = [...input.matchAll(/\bawait\s+exposeFunctionIfAbsent\s*\(/g)];
  if (matching.length < 6) throw Error('Unsupported Client.js: expected multiple callback registrations; found ' + matching.length);
  let code = input.replace(/\bawait\s+exposeFunctionIfAbsent\s*\(/g, 'await medmhExposeFunction(');
  code = code.replace(anchor, anchor + '\n' + HELPER);
  code = code.replace(flagAnchor, flagAnchor + `\n        // ${MARKER}: all callbacks target this attached Chrome tab's Client.\n        page.__medmhFreshBindings = Boolean(puppeteerOpts && (puppeteerOpts.browserURL || puppeteerOpts.browserWSEndpoint));`);
  return {code, alreadyPatched:false, changedBindings:matching.length};
}
module.exports={MARKER,patchClient,HELPER};
