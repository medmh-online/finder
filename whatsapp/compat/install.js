'use strict';
/** Apply narrowly scoped, version-checked compatibility fixes after npm install.
 * No runtime credentials or Chrome profiles are changed.
 */
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const {patchWebjs,MARKER:CDP}=require('./reuse_existing_tab');
const {patchClient:patchReady,MARKER:READY}=require('./patch_ready_event');
const {patchClient:patchBinding,MARKER:BIND}=require('./patch_bindings');
const root=path.resolve(__dirname,'..','..');
const target=path.join(root,'whatsapp','node_modules','whatsapp-web.js','src','Client.js');
if(!fs.existsSync(target))throw Error('Run npm install inside whatsapp/ first');
const pkg=require('../node_modules/whatsapp-web.js/package.json');
if(!['1.34.7','1.34.2'].includes(pkg.version))throw Error('Unverified whatsapp-web.js '+pkg.version+'; refusing to alter library source');
let before=fs.readFileSync(target,'utf8');let updated=before;
try{
 if(!updated.includes(CDP))updated=patchWebjs(updated).code;
 if(!updated.includes(READY))updated=patchReady(updated).code;
 if(!updated.includes(BIND))updated=patchBinding(updated).code;
 if(![CDP,READY,BIND].every(x=>updated.includes(x)))throw Error('Missing compatibility marker after patch');
 const check=path.join(path.dirname(target),'.medmh-check-'+process.pid+'.js');
 try{fs.writeFileSync(check,updated);const r=spawnSync(process.execPath,['--check',check],{encoding:'utf8'});if(r.status!==0)throw Error('Syntax check: '+r.stderr);} finally{fs.rmSync(check,{force:true});}
 if(before===updated){console.log('WhatsApp compatibility already installed');process.exit(0)}
 const backup=path.join(root,'whatsapp','compat','backups');fs.mkdirSync(backup,{recursive:true});
 const name=Date.now()+'-'+crypto.randomBytes(4).toString('hex')+'.js';fs.writeFileSync(path.join(backup,name),before,{flag:'wx'});
 const tmp=target+'.medmh-tmp';fs.writeFileSync(tmp,updated);fs.renameSync(tmp,target);
 console.log('Compatibility installed for whatsapp-web.js '+pkg.version+'; original saved at whatsapp/compat/backups/'+name);
}catch(err){console.error('WhatsApp compatibility setup FAILED: '+err.message);process.exit(1)}
