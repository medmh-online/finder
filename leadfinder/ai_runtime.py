"""Gemini REST integration, local-only bridge authentication, and worker registry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib import request, error
from cryptography.fernet import Fernet

from .ai_workers import WorkerDB
from .config import app_paths


def secret_file(data, filename, create):
    root = Path(data)
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    if target.is_symlink():
        raise RuntimeError('Secrets cannot be symlinks')
    if not target.exists():
        if not create:
            raise FileNotFoundError(f'Missing {filename}; start setup first')
        payload = Fernet.generate_key() if filename.endswith('.key') else os.urandom(32).hex().encode('ascii')
        try:
            with target.open('xb') as f:
                f.write(payload)
            if os.name != 'nt': target.chmod(0o600)
        except FileExistsError:
            pass
    return target.read_bytes().strip().decode('ascii')


def bridge_token(data, create=True):
    return secret_file(data,'ai_bridge_token.secret',create)


def crypt(data):
    return Fernet(secret_file(data,'ai_encryption.key',True).encode('ascii'))


def encrypt_key(data,plaintext):
    return crypt(data).encrypt(plaintext.encode('utf-8')).decode('ascii')


def decrypt_key(data,ciphertext):
    return crypt(data).decrypt(ciphertext.encode('ascii')).decode('utf-8')


class Registry:
    """Single local workspace worker registry, no user system."""
    def __init__(self,base_data):
        self.base=Path(base_data)
        self.base.mkdir(parents=True,exist_ok=True)
        self.path=self.base/'ai_worker_registry.sqlite3'
        with sqlite3.connect(self.path) as c:
            c.execute('CREATE TABLE IF NOT EXISTS worker_registry (worker_id TEXT PRIMARY KEY)')

    def register(self,worker_id,account=None):
        if not re.fullmatch('[0-9a-f]{32}',str(worker_id)):
            raise ValueError('Invalid worker ID')
        db=self._db()
        if not db.worker(worker_id): raise ValueError('Unknown local worker')
        with sqlite3.connect(self.path) as c:
            c.execute('INSERT OR IGNORE INTO worker_registry(worker_id) VALUES(?)',(worker_id,))

    def _db(self):
        from .storage import Repository
        base=app_paths()
        Repository(base.database,config=None).initialize()
        return WorkerDB(base.database).initialize()

    def resolve(self,worker_id):
        db=self._db()
        if not db.worker(worker_id):raise PermissionError('Unknown local worker')
        return db,{'mode':'local'}


def http_json(url,payload=None,headers=None,timeout=12,method=None):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
    req=request.Request(url,data=data,method=method or ('POST' if data is not None else 'GET'),
        headers={'Content-Type':'application/json','User-Agent':'MedMH-AI-Workers/1.0',**(headers or {})})
    try:
        with request.urlopen(req,timeout=timeout) as response:
            return json.loads(response.read(2_000_000).decode('utf-8'))
    except error.HTTPError as exc:
        detail=exc.read(2048).decode('utf-8','replace')
        raise RuntimeError(f'HTTP {exc.code}: {detail[:600]}') from exc


GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta'

def gemini_request(api_key,endpoint,payload=None,timeout=25):
    return http_json(GEMINI_URL+endpoint,payload,{'x-goog-api-key':api_key},timeout)


def test_models(api_key, wanted_models=None):
    """Discover candidate generateContent models, then really invoke each once."""
    url=None if wanted_models is not None else '/models?pageSize=100'
    candidates=list(wanted_models or [])
    while url and len(candidates)<100:
        response=gemini_request(api_key,url,timeout=15)
        for m in response.get('models',[]):
            if 'generateContent' in m.get('supportedGenerationMethods',[]):
                name=m.get('name','').replace('models/','')
                # Do not use special embedding/realtime/non-text endpoints.
                if name and not any(x in name for x in ('embedding','imagen','veo','aqa','tts','image-generation')):
                    candidates.append(name)
        token=response.get('nextPageToken')
        url='/models?pageSize=100&pageToken='+token if token else None
    # Testing every provider model on every key can consume substantial quota.
    # Prioritize lightweight text models; users can retest as models change.
    unique=list(dict.fromkeys(candidates))
    unique.sort(key=lambda name:(0 if 'flash-lite' in name else 1 if 'flash' in name else 2,
                                 1 if 'preview' in name else 0,name))
    results=[]
    for name in unique[:16]:
        try:
            result=gemini_request(api_key,'/models/'+name+':generateContent',{
                'contents':[{'role':'user','parts':[{'text':'Reply with OK only.'}]}],
                'generationConfig':{'maxOutputTokens':32,'temperature':0}},timeout=22)
            text=''.join(p.get('text','') for c in result.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))
            good=bool(text.strip())
            results.append({'model':name,'working':good,'reason':'' if good else 'No text returned'})
        except Exception as exc:
            results.append({'model':name,'working':False,'reason':str(exc)[:220]})
    return results


def generate(api_key,model,system_prompt,dialogue,max_tokens=300):
    body={'systemInstruction':{'parts':[{'text':system_prompt}]},
      'contents':dialogue,
      'generationConfig':{'temperature':0.65,'maxOutputTokens':max_tokens}}
    result=gemini_request(api_key,'/models/'+model+':generateContent',body,timeout=45)
    texts=[p['text'] for c in result.get('candidates',[]) for p in c.get('content',{}).get('parts',[]) if 'text' in p]
    answer='\n'.join(texts).strip()
    if not answer:
        raise RuntimeError('Gemini returned no text response: '+str(result.get('promptFeedback',{}))[:200])
    usage=result.get('usageMetadata',{})
    return answer,usage


def node_api(endpoint,payload=None,timeout=30):
    return http_json('http://127.0.0.1:8767'+endpoint,payload,
                     {'x-bridge-secret':bridge_token(app_paths().data,False)},timeout)
