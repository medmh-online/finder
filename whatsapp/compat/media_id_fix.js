'use strict';
/**
 * Scoped, in-memory compatibility fix for whatsapp-web.js v1.34.7.
 * WhatsApp Web began exposing MediaData.__x_id; spreading it into the
 * outgoing Msg model breaks ID validation on PDF/image/voice sends.
 * See upstream wwebjs/whatsapp-web.js issue #201922 / PR #201923.
 * This avoids altering node_modules, package-lock, or Chrome sessions.
 */
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');

function patchMediaUtilitySource(source) {
  if (typeof source !== 'string') throw Error('invalid injected utility source');
  const start = source.indexOf('window.WWebJS.sendMessage = async');
  const end = source.indexOf('window.WWebJS.editMessage = async', start);
  if (start < 0 || end < start) throw Error('whatsapp-web.js injected sendMessage layout changed');
  const section = source.slice(start, end);
  if (/\bdelete\s+message\.__x_id\s*;/.test(section)) {
    return { source, status: 'already_fixed' };
  }
  const opening = section.indexOf('const message = {');
  const mediaSpread = section.indexOf('...mediaOptions', opening);
  if (opening < 0 || mediaSpread < opening) {
    throw Error('Cannot safely locate outgoing media message construction');
  }
  // Anchor immediately after the outgoing Msg literal, not an unrelated object.
  const afterMedia = section.slice(mediaSpread);
  const closing = /(\r?\n[ \t]{8}\};)(\r?\n[ \t]*\/\/ Bot's won't reply if canonicalUrl is set)/.exec(afterMedia);
  if (!closing) throw Error('Cannot safely locate end of outgoing media message object');
  const insertion = start + mediaSpread + closing.index + closing[1].length;
  const eol = source.includes('\r\n') ? '\r\n' : '\n';
  return {
    source: source.slice(0, insertion) + eol + '        // MEDMH: prevent MediaData private ID from replacing the MsgKey.' +
       eol + '        delete message.__x_id;' + source.slice(insertion),
    status: 'patched_in_memory',
  };
}

function loadWhatsAppWithMediaFix() {
  const root = path.dirname(require.resolve('whatsapp-web.js/package.json'));
  const utilFile = path.resolve(root, 'src', 'util', 'Injected', 'Utils.js');
  const originalLoader = Module._extensions['.js'];
  const outcome = { status: 'not_loaded', error: '' };
  if (!fs.existsSync(utilFile)) {
    outcome.status = 'unsupported';
    outcome.error = 'Injected/Utils.js not found';
  }
  Module._extensions['.js'] = function scopedLoader(mod, filename) {
    if (path.resolve(filename) !== utilFile) return originalLoader(mod, filename);
    const source = fs.readFileSync(filename, 'utf8');
    try {
      const result = patchMediaUtilitySource(source);
      outcome.status = result.status;
      return mod._compile(result.source, filename);
    } catch (exc) {
      // Keep text messaging alive, but prevent sending unsafe media.
      outcome.status = 'unsupported';
      outcome.error = String(exc.message || exc).slice(0, 180);
      return mod._compile(source, filename);
    }
  };
  try {
    const sdk = require('whatsapp-web.js');
    if (outcome.status === 'not_loaded') {
      outcome.status = 'unsupported';
      outcome.error = 'WhatsApp injected Utils.js was not loaded';
    }
    return { sdk, mediaCompatibility: outcome };
  } finally {
    Module._extensions['.js'] = originalLoader;
  }
}
module.exports = { patchMediaUtilitySource, loadWhatsAppWithMediaFix };
