"""Local AI/WhatsApp event gateway + persistent reply scheduler.

Run this alongside whatsapp/server.js. No remote HTTP interface: it only binds
127.0.0.1 and requires a random per-installation bearer secret.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import hmac
import json
from threading import Thread
import time
from leadfinder.ai_runtime import Registry,bridge_token
from leadfinder.ai_agent import process_registry
from leadfinder.config import app_paths
from leadfinder.ai_outreach import GlobalContactLedger
from leadfinder.ai_workers import WorkerDB
from leadfinder.config import app_paths
from pathlib import Path
import os

base=app_paths().data
secret=bridge_token(base)
registry=Registry(base)
ledger=GlobalContactLedger(base)

class Handler(BaseHTTPRequestHandler):
    def answer(self,status,body):
        data=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path=='/health':
            self.answer(200,{'ready':True});return
        self.answer(404,{'error':'not found'})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get('x-bridge-secret',''),secret):
            self.answer(403,{'error':'forbidden'});return
        if self.path not in ('/inbound','/match-candidates','/resolve-identity'):
            self.answer(404,{'error':'not found'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=16000:
                self.answer(413,{'error':'invalid size'});return
            data=json.loads(self.rfile.read(size))
            worker=str(data.get('workerId',''))
            chat=str(data.get('chatId',''))
            if self.path=='/match-candidates':
                db,_=registry.resolve(worker)
                phones, reason=db.assigned_phone_candidates(worker)
                chats=db.unresolved_chat_ids(worker)
                self.answer(200,{'phones':phones,'unmatchedChats':chats,'reason':reason})
                return
            if not chat.endswith(('@c.us','@lid')) or len(chat)>100:
                raise ValueError('invalid chat id')
            if self.path=='/resolve-identity':
                db,_=registry.resolve(worker)
                phone=str(data.get('verifiedPhone') or '')
                result=db.resolve_verified_lid(worker,chat,phone)
                print('[WhatsApp identity]',worker[:8],
                      'verified and linked' if result is not None else 'verified but no unique assigned match',flush=True)
                self.answer(200,{'leadId':result})
                return
            event=str(data.get('eventId',''))
            if len(event)>150 or not event:
                raise ValueError('invalid event id')
            body=str(data.get('text',''))[:3000]
            sender_phone=str(data.get('senderPhone') or '')[:32]
            # Outbound events are accepted only after a real WhatsApp server ACK.
            # Old persisted 'message_create' events have no ack and are ignored.
            if data.get('direction')=='out' and not (
                    type(data.get('ack')) is int and 1 <= data['ack'] <= 4):
                self.answer(200,{'stored':False,'reason':'outbound_not_server_acknowledged'})
                return
            db,_=registry.resolve(worker)
            lead_id=(db.outgoing_sync(worker,event,chat,body,sender_phone=sender_phone) if data.get('direction')=='out'
                     else db.inbound(worker,event,chat,body,sender_phone=sender_phone))
            print('[WhatsApp event]', 'out' if data.get('direction')=='out' else 'in', worker[:8],
                  'matched lead' if lead_id is not None else 'unmatched chat',
                  str(lead_id) if lead_id is not None else chat.rsplit('@',1)[-1], flush=True)
            if lead_id is not None:
                phone=db.contact_phone(lead_id)
                if phone:
                    if data.get('direction')=='out':ledger.observe_sent(phone,worker,lead_id)
                    # Inbound explicit opt-outs are persisted by db.inbound().
                    if phone in db.opted_out_phones():ledger.block(phone)
            self.answer(200,{'stored':True,'leadId':lead_id})
        except (ValueError,PermissionError) as exc:
            self.answer(400,{'error':str(exc)[:150]})
        except Exception as exc:
            print('[gateway] ',type(exc).__name__,str(exc)[:200],flush=True)
            self.answer(503,{'error':'temporary storage error'})

    def log_message(self,fmt,*args):
        if '/health' not in (args[0] if args else ''):
            print('[gateway]',fmt%args,flush=True)



def queue_loop():
    """Run standalone number batches independently of CRM/Gemini processing.

    process_registry() includes slow HTTP requests and may legitimately block
    while a worker processes an incoming conversation. No queue polling should
    depend on completing one of those operations. One dedicated daemon is the
    *only* sender for standalone queues.
    """
    from leadfinder.message_queue import tick
    path=Path(app_paths().database)
    print('[queue dispatcher] independent scheduler started', flush=True)
    while True:
        try:
            # Do not create an empty database before the dashboard initializes it.
            if path.is_file():
                tick(WorkerDB(path), ledger)
        except Exception as exc:
            print('[queue dispatcher] error:', type(exc).__name__, str(exc)[:320], flush=True)
        time.sleep(3)


def loop():
    while True:
        try:
            process_registry(registry)
        except Exception as exc:
            print('[agent loop]',type(exc).__name__,str(exc)[:200],flush=True)
        time.sleep(2)

if __name__=='__main__':
    Thread(target=loop,daemon=True,name='medmh-ai-loop').start()
    if os.environ.get('MEDMH_QUEUE_EXTERNAL') != '1':
        Thread(target=queue_loop,daemon=True,name='medmh-queue-loop').start()
    print('AI gateway bound to 127.0.0.1:8766',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
