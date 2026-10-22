#!/usr/bin/env python3
"""Read-only MedMH WhatsApp/AI reply diagnostics. Prints no messages, numbers, keys or tokens.
Run from any directory with the project's own Python interpreter.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib import request

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from leadfinder.config import app_paths


def p(msg=''):
    print(msg,flush=True)


def instant(v):
    if not v:return 'none'
    try:return datetime.fromtimestamp(float(v),timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError,OverflowError,TypeError):return 'invalid'


def age(v):
    if not v:return 'none'
    try:
        delta=time.time()-float(v)
        return f'{max(0,round(delta))}s ago' if delta<3600 else f'{delta/3600:.1f}h ago'
    except (TypeError,ValueError):return '?'


def http_health(port):
    try:
        with request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as r:
            return r.status==200
    except Exception:return False


def node_status(worker_id,token_path):
    try:
        token=token_path.read_text(encoding='ascii').strip()
        payload=json.dumps({'workerId':worker_id}).encode()
        req=request.Request('http://127.0.0.1:8767/status',payload,
            {'x-bridge-secret':token,'Content-Type':'application/json'})
        with request.urlopen(req,timeout=4) as r:return json.load(r).get('state','unknown')
    except Exception:return 'unreachable'


def tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def cols(c, name):
    return {r['name'] for r in c.execute(f'PRAGMA table_info({name})')}


def scalar(c,sql,args=()):return c.execute(sql,args).fetchone()[0]


def report(db,token_path,lead_id=None):
    if not db.exists():
        p('ERROR: leads.db does not exist at the configured location. Check LEADFINDER_DB_PATH.');return 2
    uri='file:'+str(db).replace('\\','/')+'?mode=ro'
    c=sqlite3.connect(uri,uri=True,timeout=4)
    c.row_factory=sqlite3.Row
    ts=tables(c)
    required={'ai_workers','ai_assignments','ai_messages','ai_threads','ai_unmatched','leads'}
    absent=required-ts
    if absent:
        p('ERROR: Expected AI tables missing: '+', '.join(sorted(absent)));return 2
    p('MedMH AI REPLY DIAGNOSTICS — READ ONLY')
    p('--------------------------------------------------------')
    p('Local gateways: Python 8766='+('UP' if http_health(8766) else 'DOWN')+
      ' | WhatsApp 8767='+('UP' if http_health(8767) else 'DOWN'))
    p('Database: exists; read-only connection successful')
    queue_dir=ROOT/'whatsapp'/'event_queue'
    pending=len(list(queue_dir.glob('*.json'))) if queue_dir.is_dir() else 0
    p('Unforwarded WhatsApp event files: '+str(pending))
    p('Worker registrations: '+str(scalar(c,'SELECT COUNT(*) FROM ai_worker_registry.worker_registry') if False else 'checked below'))
    registry=app_paths().data/'ai_worker_registry.sqlite3'
    registered=set()
    if registry.is_file():
        try:
            reg=sqlite3.connect('file:'+str(registry).replace('\\','/')+'?mode=ro',uri=True)
            registered={r[0] for r in reg.execute('SELECT worker_id FROM worker_registry')}
            reg.close()
        except sqlite3.Error:p('Warning: worker registry cannot be read')
    else:p('WARNING: worker registry database missing')
    now=time.time()
    worker_columns=cols(c,'ai_workers')
    for w in c.execute('SELECT * FROM ai_workers ORDER BY created_at'):
        wid=w['id']; short=wid[:8]
        count=scalar(c,'SELECT COUNT(*) FROM ai_assignments WHERE worker_id=?',(wid,))
        goodkeys=0
        if 'ai_keys' in ts:
            for r in c.execute('SELECT test_results FROM ai_keys WHERE worker_id=?',(wid,)):
                try:
                    tests=json.loads(r[0] or '[]')
                    if any(x.get('working') and x.get('model')==w['model_name'] for x in tests):goodkeys+=1
                except (ValueError,TypeError):pass
        p(f'\nWorker ID prefix {short} | registered={wid in registered} | WhatsApp={node_status(wid,token_path)}'
          f' | AI_enabled={bool(w["enabled"])} | assigned_leads={count}')
        p('  Model selected='+('yes' if w['model_name'] else 'no')+
          f' | tested matching keys={goodkeys}'+
          (' | token_cap='+str(w['daily_token_limit']) if 'daily_token_limit' in worker_columns else ''))
        recent_in=scalar(c,"SELECT COUNT(*) FROM ai_messages WHERE worker_id=? AND direction='in' AND created_at>?",(wid,now-3600))
        old_in=scalar(c,"SELECT COUNT(*) FROM ai_messages WHERE worker_id=? AND direction='in'",(wid,))
        unmatched=scalar(c,"SELECT COUNT(*) FROM ai_unmatched WHERE worker_id=? AND body NOT LIKE '[Outgoing] %'",(wid,))
        unlid=scalar(c,"SELECT COUNT(*) FROM ai_unmatched WHERE worker_id=? AND chat_id LIKE '%@lid' AND body NOT LIKE '[Outgoing] %'",(wid,))
        outbound=scalar(c,"SELECT COUNT(*) FROM ai_messages WHERE worker_id=? AND direction='out' AND status IN ('queued','queued_reply','sending')",(wid,))
        p(f'  Inbound matched: last hour={recent_in}, total={old_in} | unmatched={unmatched} (opaque IDs={unlid}) | pending outbound={outbound}')
        if 'ai_usage' in ts:
            recent_error=c.execute("SELECT error, recorded_at FROM ai_usage WHERE worker_id=? AND error!='' ORDER BY id DESC LIMIT 1",(wid,)).fetchone()
            if recent_error:
                # Only report the type; do not dump provider responses, potentially containing identifiers.
                err=(recent_error['error'] or '').lower()
                typ=('quota/rate limit' if '429' in err or 'quota' in err else
                    'API key/permission' if any(x in err for x in ('403','401','api key')) else
                    'transport/service' if any(x in err for x in ('timeout','503','502','500')) else
                    'other Gemini failure')
                p('  Last Gemini error category='+typ+' ('+age(recent_error['recorded_at'])+')')
        assigned=c.execute('SELECT lead_id FROM ai_assignments WHERE worker_id=? ORDER BY assigned_at DESC LIMIT 5',(wid,)).fetchall()
        for entry in assigned:
            lid=entry['lead_id']
            if lead_id is not None and lid!=lead_id:continue
            lead=c.execute('SELECT archived,do_not_contact FROM leads WHERE id=?',(lid,)).fetchone()
            state=c.execute('SELECT * FROM ai_threads WHERE lead_id=?',(lid,)).fetchone()
            ctrl=(c.execute('SELECT * FROM ai_lead_control WHERE lead_id=?',(lid,)).fetchone()
                  if 'ai_lead_control' in ts else None)
            trace=(c.execute('SELECT stage,detail,updated_at FROM ai_reply_trace WHERE lead_id=?',(lid,)).fetchone()
                   if 'ai_reply_trace' in ts else None)
            p(f'  Lead #{lid}: archived={bool(lead["archived"]) if lead else "missing"}, blocked={bool(lead["do_not_contact"]) if lead else "?"}, '
              f'paused={bool(ctrl["paused"]) if ctrl else False}, handed_off={bool(ctrl["handoff"]) if ctrl and "handoff" in ctrl.keys() else False}')
            if not state:
                p('    No ai_threads row: incoming events have not been matched to this lead.');continue
            last=scalar(c,"SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction='in'",(lid,)) or 0
            p(f'    Last inbound={age(state["last_inbound"])} | latest_inbound_id={last} | replied_through={state["replied_through"]}')
            p(f'    Generation in_progress={bool(state["in_progress"])} | retry_after={instant(state["retry_after"]) if "retry_after" in state.keys() else "none"}')
            if trace:
                st=str(trace['stage'])[:40]
                detail=str(trace['detail'])[:150]
                p(f'    Trace={st} | detail={detail} | {age(trace["updated_at"])}')
            if last > (state['replied_through'] or 0):
                if state['last_inbound'] and now-state['last_inbound']>86400:
                    p('    BLOCKER: inbound is older than 24h; scheduler will not process it')
                elif ctrl and (ctrl['paused'] or ('handoff' in ctrl.keys() and ctrl['handoff'])):
                    p('    BLOCKER: lead paused or handed off')
                elif (state['in_progress'] and state['lease_at'] and now-state['lease_at']<180):
                    p('    WAITING: generation lease still held')
                elif 'retry_after' in state.keys() and (state['retry_after'] or 0)>now:
                    p('    WAITING: retry cooldown')
                else:p('    PENDING: new inbound not replied to; examine scheduler/limits')
            elif last:p('    Handled or queued: look for outbound status and trace')
            status=c.execute("SELECT status,kind FROM ai_messages WHERE lead_id=? AND direction='out' ORDER BY id DESC LIMIT 1",(lid,)).fetchone()
            if status:p('    Latest outbound status='+str(status['status'])+', kind='+str(status['kind']) if 'kind' in status.keys() else '    Latest outbound status='+str(status['status']))
    p('\nDIAGNOSIS GUIDE')
    p('No matched and no unmatched incoming -> WhatsApp event listener/gateway delivery.')
    p('Unmatched incoming -> verified phone/LID mapping or lead assignment.')
    p('Matched incoming, pending and no Gemini usage -> AI scheduler or worker limits.')
    p('Trace retry_scheduled -> Gemini generation issue.')
    p('Queued outbound -> send queue or WhatsApp bridge issue.')
    p('No phone numbers, messages or API secrets were printed.')
    return 0


def main():
    parser=argparse.ArgumentParser(description='Read-only MedMH AI reply check; no messages or secrets printed.')
    parser.add_argument('--lead',type=int,help='Optional numeric lead ID for more focused output')
    args=parser.parse_args()
    ap=app_paths()
    try:return report(ap.database,ap.data/'ai_bridge_token.secret',args.lead)
    except (sqlite3.Error,OSError,ValueError) as e:
        p('Diagnostic failed: '+type(e).__name__+': '+str(e)[:180]);return 2

if __name__=='__main__':raise SystemExit(main())
