'use strict';
/**
 * In-memory fix for whatsapp-web.js 1.34.7 media ID collision.
 * The old MEDMH hotfix required an exact comment/indentation after the outgoing
 * Msg literal. Installed builds differ, leaving mediaCompatibility unsupported
 * and all approved media HTTP 503. This implementation locates the outgoing
 * object by syntax and inserts the one-line upstream fix right after it.
 *
 * Original library files, package lock, Chrome profiles and sessions stay intact.
 */
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');

function findObjectClosingBrace(source, openingBrace, stop) {
  let depth = 0;
  let quote = '';
  let singleLineComment = false;
  let blockComment = false;
  for (let i = openingBrace; i < stop; i++) {
    const ch = source[i];
    const next = source[i + 1];
    if (singleLineComment) {
      if (ch === '\n') singleLineComment = false;
      continue;
    }
    if (blockComment) {
      if (ch === '*' && next === '/') { blockComment = false; i++; }
      continue;
    }
    if (quote) {
      if (ch === '\\') { i++; continue; }
      if (ch === quote) quote = '';
      continue;
    }
    if (ch === '/' && next === '/') { singleLineComment = true; i++; continue; }
    if (ch === '/' && next === '*') { blockComment = true; i++; continue; }
    if (ch === '\'' || ch === '"' || ch === '`') { quote = ch; continue; }
    if (ch === '{') { depth++; continue; }
    if (ch === '}') {
      depth--;
      if (depth === 0) return i;
      if (depth < 0) break;
    }
  }
  return -1;
}

function patchMediaUtilitySource(source) {
  if (typeof source !== 'string') throw Error('invalid injected utility source');
  const start = source.indexOf('window.WWebJS.sendMessage = async');
  const end = source.indexOf('window.WWebJS.editMessage = async', start);
  if (start < 0 || end < start) throw Error('whatsapp-web.js injected sendMessage layout changed');
  const section = source.slice(start, end);
  if (/\bdelete\s+message\.__x_id\s*;/.test(section)) {
    return {source, status:'already_fixed'};
  }

  const found = /\bconst\s+message\s*=\s*\{/g;
  const matches = [...section.matchAll(found)];
  if (matches.length !== 1) throw Error('Expected exactly one outgoing WhatsApp message object');
  const at = start + matches[0].index;
  const openingBrace = source.indexOf('{', at);
  const closingBrace = findObjectClosingBrace(source, openingBrace, end);
  if (closingBrace < 0) throw Error('Could not safely parse outgoing message object');

  const literal = source.slice(openingBrace + 1, closingBrace);
  if (!/\bid\s*:\s*newMsgKey\b/.test(literal) ||
      !/\.\.\.mediaOptions\s*,/.test(literal) ||
      !/\.\.\.extraOptions\s*,?/.test(literal)) {
    throw Error('Unsupported outgoing media Msg layout; refusing unsafe patch');
  }
  const semicolon = /^\s*;/.exec(source.slice(closingBrace + 1, Math.min(end, closingBrace + 20)));
  if (!semicolon) throw Error('Expected semicolon after outgoing Msg literal');

  // Use the indentation of "const message" instead of assuming a particular
  // number of spaces or requiring the following source comment to be unchanged.
  const lineStart = source.lastIndexOf('\n', at) + 1;
  const indentation = (source.slice(lineStart, at).match(/^[ \t]*/) || [''])[0];
  const insertion = closingBrace + 1 + semicolon[0].length;
  const eol = source.includes('\r\n') ? '\r\n' : '\n';
  return {
    source: source.slice(0,insertion) + eol + indentation +
      '// MEDMH: upstream media fix; private MediaData ID must not replace MsgKey.' + eol +
      indentation + 'delete message.__x_id;' + source.slice(insertion),
    status:'patched_in_memory'
  };
}

function loadWhatsAppWithMediaFix() {
  const root = path.dirname(require.resolve('whatsapp-web.js/package.json'));
  const utilFile = path.resolve(root, 'src', 'util', 'Injected', 'Utils.js');
  const originalLoader = Module._extensions['.js'];
  const outcome = {status:'not_loaded', error:''};
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
      // Keep text messaging working and report exactly why media is disabled.
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
    return {sdk, mediaCompatibility:outcome};
  } finally {
    Module._extensions['.js'] = originalLoader;
  }
}
module.exports = {patchMediaUtilitySource, loadWhatsAppWithMediaFix};
