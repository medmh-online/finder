'use strict';
/** Minimal reversible source adjustment for whatsapp-web.js 1.34.2 / 1.34.7.
 * Existing-browser mode must attach to the existing working WhatsApp tab.
 * By default these versions create a SECOND WA tab and reload it, which caused
 * Chromium/WhatsApp local-database problems for the project under test.
 * This does NOT attempt to bypass WhatsApp policies or account restrictions.
 */
const MARKER='MEDMH_CDP_REUSE_EXISTING_WHATSAPP_TAB_V225';
function replaceOne(text,rx,replacement,name){
  const all=[...text.matchAll(new RegExp(rx.source,rx.flags.includes('g')?rx.flags:rx.flags+'g'))];
  if(all.length!==1)throw Error(`Unsupported whatsapp-web.js layout: expected one ${name}, found ${all.length}. Nothing changed.`);
  return text.replace(rx,replacement);
}
function patchWebjs(code){
  if(code.includes(MARKER))return {code,alreadyPatched:true};
  let out=replaceOne(code,
    /browser\s*=\s*await puppeteer\.connect\(puppeteerOpts\);\s*page\s*=\s*await browser\.newPage\(\);/,
    `browser = await puppeteer.connect(puppeteerOpts);\n            // ${MARKER}\n            const medmhPages = await browser.pages();\n            page = medmhPages.find(p => /^https:\\/\\/web\\.whatsapp\\.com(?:\\/|\\?|$)/.test(p.url()));\n            if (!page) throw new Error('MedMH: No WhatsApp tab found. Open and sign in to WhatsApp Web in the worker Chrome first.');`,
    'browser connect/newPage');
  out=replaceOne(out,
    /await page\.goto\(WhatsWebURL,\s*\{\s*waitUntil:\s*['"]load['"],\s*timeout:\s*0,\s*referer:\s*['"]https:\/\/whatsapp\.com\/['"],?\s*\}\);/,
    `if (!(puppeteerOpts && (puppeteerOpts.browserURL || puppeteerOpts.browserWSEndpoint) && /^https:\\/\\/web\\.whatsapp\\.com(?:\\/|\\?|$)/.test(page.url()))) {\n            await page.goto(WhatsWebURL, {waitUntil:'load',timeout:0,referer:'https://whatsapp.com/'});\n        }`,
    'initial forced WhatsApp navigation');
  // On 1.34.2 only, unconditional setUserAgent(false) disrupts a normal browser UA.
  out=out.replace(/(?<!if\s*\([^)]*)await page\.setUserAgent\(this\.options\.userAgent\);/g,
    (s,offset,whole) => {
      const preceding=whole.slice(Math.max(0,offset-100),offset);
      return /if\s*\(this\.options\.userAgent\s*!==\s*false\)\s*\{\s*$/.test(preceding)
        ?s:'if (this.options.userAgent !== false) await page.setUserAgent(this.options.userAgent);';
    });
  return {code:out,alreadyPatched:false};
}
module.exports={MARKER,patchWebjs};
