'use strict';
/** Compatibility fix for attaching whatsapp-web.js 1.34.x to an *already synced*
 * WhatsApp Web tab, specifically the existing native Chrome/CDP integration.
 *
 * The original code only listens for the NEXT 'change:hasSynced' event. When
 * WhatsApp has already synced before an AI worker attaches, this event may never
 * happen and the gateway stays at 'waiting_for_ready_event'.
 *
 * Install the listener first, then check the current state, and invoke the
 * original authentication/injection/ready path once for a synced tab. No direct
 * READY emission, no bypass of login checks and no session/profile edits.
 */
const MARKER = 'MEDMH_ALREADY_SYNCED_READY_CHECK_V229';
const ORIGINAL = /window\s*\.require\(\s*['"]WAWebSocketModel['"]\s*\)\s*\.Socket\s*\.on\(\s*['"]change:hasSynced['"]\s*,\s*\(\)\s*=>\s*\{\s*window\s*\.onAppStateHasSyncedEvent\(\s*\)\s*;?\s*\}\s*\)\s*;/g;
const REPLACEMENT = `// ${MARKER}: handle attaching to WhatsApp Web after its initial sync.\n` +
`const medmhSyncedSocket = window.require('WAWebSocketModel').Socket;\n`+
`let medmhSyncedHandlerStarted = false;\n`+
`const medmhCheckInitialSync = () => {\n`+
`    if (medmhSyncedHandlerStarted || medmhSyncedSocket.hasSynced !== true) return;\n`+
`    medmhSyncedHandlerStarted = true;\n`+
`    Promise.resolve().then(() => window.onAppStateHasSyncedEvent()).catch((err) => {\n`+
`        medmhSyncedHandlerStarted = false;\n`+
`        console.error('[MedMH] WhatsApp ready callback failed:', err);\n`+
`    });\n`+
`};\n`+
`medmhSyncedSocket.on('change:hasSynced', medmhCheckInitialSync);\n`+
`medmhCheckInitialSync();`;
function patchClient(code) {
  if (code.includes(MARKER)) return {code, alreadyPatched:true};
  if (!code.includes('MEDMH_CDP_REUSE_EXISTING_WHATSAPP_TAB_V225')) {
    throw Error('Missing v2.2.5 existing-tab compatibility patch. Apply the working Chrome/CDP patch first; no files changed.');
  }
  const matches=[...code.matchAll(ORIGINAL)];
  if (matches.length!==1) {
    throw Error(`Unsupported whatsapp-web.js Client.js layout: expected one sync listener, found ${matches.length}. No files changed.`);
  }
  const patched=code.replace(ORIGINAL,REPLACEMENT);
  return {code:patched,alreadyPatched:false};
}
module.exports={MARKER,patchClient,REPLACEMENT};
