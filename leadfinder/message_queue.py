"""Standalone, manually supplied recipient queue.

Never creates or updates Leads/CRM, assignments, permissions, AI threads, or
lead messages. Uses workers only for WhatsApp transport and Gemini number
normalization. Explicit global stop requests still take precedence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import random
import re
import time
import uuid

from .ai_runtime import app_paths, node_api

COUNTRY_DIAL = {'MA':'212','FR':'33','BE':'32','ES':'34','GB':'44','US':'1','DZ':'213','TN':'216'}


def parse_numbers(raw, limit=100):
    items=[x.strip() for x in re.split(r'[\n,;]+',str(raw or '')) if x.strip()]
    if not items:raise ValueError('Enter one or more phone numbers.')
    if len(items)>limit:raise ValueError(f'Maximum {limit} numbers per batch.')
    return items


def validate_normalized(candidate, original, default_cc='212'):
    """Exactly mirrors the v4 sender's non-hallucinating validation.

    Gemini can remove punctuation and standard local prefixes, but it cannot
    add, replace or invent any of the user's significant digits.
    """
    initial=re.sub(r'\D','',str(original or ''))
    expected=initial[2:] if initial.startswith('00') else initial
    if expected.startswith('0'):
        expected=default_cc+expected[1:]
    elif len(expected)==9 and default_cc=='212' and re.match(r'^[567]',expected):
        expected=default_cc+expected
    output=re.sub(r'\D','',str(candidate or ''))
    if re.fullmatch(r'\d{8,15}',output) and output==expected and not output.startswith('0'):
        return '+'+output
    return ''


def _extract_json(response):
    raw=str(response or '').strip()
    if raw.startswith('```'):
        raw=re.sub(r'^```(?:json)?\s*','',raw,flags=re.I)
        raw=re.sub(r'\s*```$','',raw)
    try:return json.loads(raw)
    except ValueError as ex:raise ValueError('Gemini returned invalid JSON. Nothing was queued.') from ex


def normalize_with_gemini(db,worker,items,country='MA'):
    from .ai_agent import gemini_with_fallback
    dial=COUNTRY_DIAL.get(str(country).upper())
    if not dial:raise ValueError('Choose a supported country.')
    prompt=(
        f'Normalize WhatsApp phone numbers. Default country calling code {dial}. '
        'Clean punctuation/spaces and leading 00/local 0 prefixes only. '
        'Never guess missing digits. Reply ONLY with JSON exactly shaped '
        '{"numbers":[{"normalized":"digits only or empty string"}]} in the original order. '
        'INPUT: '+json.dumps(items,ensure_ascii=False)
    )
    # Same one-batch Gemini normalization as WhatsApp-Gemini-Queue v4.
    result,_=gemini_with_fallback(db,worker,app_paths().data,
        'Output valid JSON only; never invent phone digits.',
        [{'role':'user','parts':[{'text':prompt}]}],purpose='queue_normalize',max_tokens=min(3600,max(500,len(items)*37)))
    parsed=_extract_json(result)
    rows=parsed.get('numbers') if isinstance(parsed,dict) else None
    if not isinstance(rows,list) or len(rows)!=len(items):
        raise ValueError('Gemini number count mismatch. Nothing was queued.')
    return [validate_normalized(row.get('normalized') if isinstance(row,dict) else '',raw,dial)
            for raw,row in zip(items,rows)]


def init_queue(db):
    db.initialize()
    with db.connect(True) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS standalone_message_batches(
            id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL REFERENCES ai_workers(id) ON DELETE CASCADE,
            message TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'paused',
            country TEXT NOT NULL DEFAULT 'MA',
            min_gap INTEGER NOT NULL,
            max_gap INTEGER NOT NULL,
            next_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_polled_at REAL NOT NULL DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS standalone_message_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL REFERENCES standalone_message_batches(id) ON DELETE CASCADE,
            raw_number TEXT NOT NULL,
            e164 TEXT NOT NULL DEFAULT '',
            verified_chat_id TEXT NOT NULL DEFAULT '',
            occurrence INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            reason TEXT NOT NULL DEFAULT '',
            attempted_at REAL,
            completed_at REAL,
            updated_at REAL NOT NULL)''')
        # Older queue databases must migrate without losing queued/sent items.
        cols={row['name'] for row in c.execute('PRAGMA table_info(standalone_message_batches)')}
        if 'last_polled_at' not in cols:
            c.execute('ALTER TABLE standalone_message_batches ADD COLUMN last_polled_at REAL NOT NULL DEFAULT 0')
        item_cols={row['name'] for row in c.execute('PRAGMA table_info(standalone_message_items)')}
        if 'verified_chat_id' not in item_cols:
            c.execute("ALTER TABLE standalone_message_items ADD COLUMN verified_chat_id TEXT NOT NULL DEFAULT ''")
        c.execute('''CREATE INDEX IF NOT EXISTS standalone_items_batch
                  ON standalone_message_items(batch_id,status,id)''')
        c.execute('''CREATE INDEX IF NOT EXISTS standalone_batches_worker
                  ON standalone_message_batches(worker_id,state,created_at)''')
    return db


def register_queue_worker(worker_id):
    """Ensure the background AI gateway visits workers used only by this page."""
    from .ai_runtime import Registry
    Registry(app_paths().data).register(worker_id)


def prepare(db,worker_id,raw_numbers,message,country='MA',min_gap=5,max_gap=30):
    init_queue(db)
    worker=db.worker(worker_id)
    if not worker:raise ValueError('Choose a worker.')
    if not worker.get('model_name'):raise ValueError('Choose a tested Gemini model for this worker.')
    items=parse_numbers(raw_numbers)
    text=str(message or '').strip()
    if not text or len(text)>3000:raise ValueError('Message must be 1–3000 characters.')
    lo,hi=int(min_gap),int(max_gap)
    if not 1<=lo<=hi<=3600:raise ValueError('Choose a delay of 1–3600 seconds, with max >= min.')
    normalized=normalize_with_gemini(db,worker,items,country)
    batch_id=uuid.uuid4().hex
    t=time.time()
    seen={}
    # Register standalone-only workers too: no Leads page visit is required.
    register_queue_worker(worker_id)
    with db.connect(True) as c:
        c.execute('''INSERT INTO standalone_message_batches
                  (id,worker_id,message,state,country,min_gap,max_gap,created_at,updated_at)
                  VALUES(?,?,?,'paused',?,?,?,?,?)''',
                  (batch_id,worker_id,text,country,lo,hi,t,t))
        for raw,phone in zip(items,normalized):
            seen[phone]=seen.get(phone,0)+1 if phone else 0
            c.execute('''INSERT INTO standalone_message_items
                      (batch_id,raw_number,e164,occurrence,status,reason,updated_at)
                      VALUES(?,?,?,?,?,?,?)''',
                      (batch_id,raw,phone,seen[phone] if phone else 0,
                       'pending' if phone else 'skipped',
                       'Waiting for worker dispatcher' if phone else 'Invalid or ambiguous phone number',t))
    return batch_id


def batches(db,limit=50):
    init_queue(db)
    with db.connect() as c:
        return [dict(r) for r in c.execute('''SELECT b.*,w.name worker_name,
            COUNT(i.id) total,
            SUM(i.status='pending') pending,
            SUM(i.status='checking') checking,
            SUM(i.status='sending') sending,
            SUM(i.status='sent') sent,
            SUM(i.status='skipped') skipped,
            SUM(i.status='uncertain') uncertain,
            SUM(i.status='cancelled') cancelled
            FROM standalone_message_batches b JOIN ai_workers w ON w.id=b.worker_id
            LEFT JOIN standalone_message_items i ON i.batch_id=b.id
            GROUP BY b.id ORDER BY b.created_at DESC LIMIT ?''',(int(limit),))]


def entries(db,batch_id):
    with db.connect() as c:
        return [dict(r) for r in c.execute('''SELECT * FROM standalone_message_items
            WHERE batch_id=? ORDER BY id''',(batch_id,))]


def control(db,batch_id,action):
    if action not in ('start','pause','resume','cancel'):raise ValueError('Invalid queue action')
    init_queue(db)
    with db.connect() as c:
        row=c.execute('SELECT state,worker_id FROM standalone_message_batches WHERE id=?',(batch_id,)).fetchone()
    if not row:raise ValueError('Batch not found')
    # Important: no registry/database write while the queue write transaction is open.
    if action in ('start','resume'):
        if row['state'] not in ('paused','running'):raise ValueError('This queue cannot be restarted')
        register_queue_worker(row['worker_id'])
    with db.connect(True) as c:
        row=c.execute('SELECT state FROM standalone_message_batches WHERE id=?',(batch_id,)).fetchone()
        if not row:raise ValueError('Batch not found')
        if action in ('start','resume'):
            if row['state'] not in ('paused','running'):raise ValueError('This queue cannot be restarted')
            state='running'
        elif action=='pause':state='paused' if row['state']=='running' else row['state']
        else:state='cancelled'
        c.execute('UPDATE standalone_message_batches SET state=?,updated_at=? WHERE id=?',
                  (state,time.time(),batch_id))
        if action in ('start','resume'):
            c.execute("UPDATE standalone_message_items SET reason='Waiting for worker dispatcher',updated_at=? WHERE batch_id=? AND status='pending'",(time.time(),batch_id))
        if action=='cancel':
            c.execute("UPDATE standalone_message_items SET status='cancelled',updated_at=? WHERE batch_id=? AND status='pending'",(time.time(),batch_id))
        return state


def verified_whatsapp_chat_id(requested_phone, returned_id):
    """Accept the WID returned by WhatsApp registration lookup for this phone.

    @lid is an opaque WhatsApp identity, *not* a telephone number. Only accept
    it here because /verify-number calls getNumberId() using requested_phone.
    Never fabricate a LID or derive one from digits in a phone number.
    """
    if not re.fullmatch(r'\+[1-9]\d{8,14}', str(requested_phone or '')):
        return None, 'Invalid requested phone number'
    if not isinstance(returned_id, str) or not re.fullmatch(r'\d{9,20}@(c\.us|lid)', returned_id):
        return None, 'Unsupported WhatsApp ID'
    if returned_id.endswith('@c.us') and returned_id != requested_phone[1:] + '@c.us':
        return None, 'Verified WhatsApp ID differs from entered phone'
    # A LID must remain opaque. The lookup from the requested phone is the
    # binding, not a comparison of unrelated LID and telephone digits.
    return returned_id, None


def _start_of_utc_day(timestamp):
    d=datetime.fromtimestamp(timestamp,timezone.utc)
    return d.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()


def _used_today(c,worker_id,t):
    return int(c.execute('''SELECT COUNT(*) FROM standalone_message_items i
        JOIN standalone_message_batches b ON b.id=i.batch_id
        WHERE b.worker_id=? AND i.attempted_at>=? AND i.status IN ('sending','sent','uncertain')''',
        (worker_id,_start_of_utc_day(t))).fetchone()[0])


def queue_contacts_today(db,worker_id):
    with db.connect() as c:
        return _used_today(c,worker_id,time.time())


def _refresh(c,batch_id,t):
    b=c.execute('SELECT * FROM standalone_message_batches WHERE id=?',(batch_id,)).fetchone()
    if not b:return
    pending=c.execute('''SELECT 1 FROM standalone_message_items WHERE batch_id=?
        AND status IN ('pending','checking','sending') LIMIT 1''',(batch_id,)).fetchone()
    if pending:
        if b['state']=='running':
            c.execute('UPDATE standalone_message_batches SET next_at=?,updated_at=? WHERE id=?',
                      (t+random.randint(b['min_gap'],b['max_gap']),t,batch_id))
    elif b['state']=='running':
        c.execute("UPDATE standalone_message_batches SET state='completed',next_at=0,updated_at=? WHERE id=?",(t,batch_id))


def tick(db,ledger):
    """One recipient at a time per worker. Never manipulates Leads or AI messages.

    Network sends never occur inside a DB write transaction. A lost send outcome
    stays 'uncertain' and is never retried automatically.
    """
    init_queue(db)
    t=time.time()
    candidates=[]
    with db.connect(True) as c:
        # Heartbeat distinguishes a missing dispatcher from a WhatsApp send failure.
        c.execute("UPDATE standalone_message_batches SET last_polled_at=? WHERE state='running'",(t,))
        # Crash recovery: never silently repeat an in-flight send.
        c.execute("UPDATE standalone_message_items SET status='uncertain',reason='Interrupted send: review manually',updated_at=? WHERE status='sending' AND attempted_at<?",(t,t-180))
        c.execute("UPDATE standalone_message_items SET status='pending' WHERE status='checking' AND updated_at<?",(t-120,))
        for rec in c.execute("SELECT DISTINCT worker_id FROM standalone_message_batches WHERE state='running' ORDER BY worker_id").fetchall():
            wid=rec['worker_id']
            b=c.execute("SELECT * FROM standalone_message_batches WHERE worker_id=? AND state='running' ORDER BY created_at LIMIT 1",(wid,)).fetchone()
            if b is None or t<b['next_at']:continue
            if c.execute("SELECT 1 FROM standalone_message_items WHERE batch_id=? AND status IN ('checking','sending')",(b['id'],)).fetchone():continue
            item=c.execute("SELECT * FROM standalone_message_items WHERE batch_id=? AND status='pending' ORDER BY id LIMIT 1",(b['id'],)).fetchone()
            if item is None:
                _refresh(c,b['id'],t)
                continue
            # Replies and already-queued CRM sends have priority over bulk queue traffic.
            if c.execute("SELECT 1 FROM ai_messages WHERE worker_id=? AND direction='out' AND status IN ('queued','queued_reply','sending') LIMIT 1",(wid,)).fetchone():
                c.execute("UPDATE standalone_message_items SET reason='Waiting: worker has pending AI replies' WHERE id=?",(item['id'],))
                continue
            # A newer inbound message is not evidence of ongoing processing.
            # Completed handoffs, paused leads, opt-outs, and ignored messages
            # may leave inbound rows newer than replied_through indefinitely.
            # Only an actual, currently leased generation may defer this send.
            # Outbound AI messages are separately prioritized above.
            if c.execute('''SELECT 1 FROM ai_threads th
                     JOIN ai_assignments a ON a.lead_id=th.lead_id
                     WHERE a.worker_id=? AND th.in_progress=1
                       AND th.lease_at IS NOT NULL AND th.lease_at>=?
                     LIMIT 1''',(wid,t-180)).fetchone():
                c.execute("UPDATE standalone_message_items SET reason='Waiting: AI generating a reply',updated_at=? WHERE id=?",(t,item['id']))
                continue
            if ledger.is_blocked(item['e164']):
                c.execute("UPDATE standalone_message_items SET status='skipped',reason='Recipient requested no contact',updated_at=? WHERE id=?",(t,item['id']))
                _refresh(c,b['id'],t)
                continue
            worker=db.worker(wid)
            if not worker:
                c.execute("UPDATE standalone_message_items SET reason='Worker not found' WHERE id=?",(item['id'],))
                continue
            used=_used_today(c,wid,t)+db.daily_new_contact_used(wid)
            if used>=int(worker['daily_new_contacts']):
                c.execute("UPDATE standalone_message_items SET reason='Daily worker contact limit reached' WHERE id=?",(item['id'],))
                c.execute('UPDATE standalone_message_batches SET next_at=?,updated_at=? WHERE id=?',
                          (_start_of_utc_day(t)+86400,t,b['id']))
                continue
            c.execute("UPDATE standalone_message_items SET status='checking',reason='Checking WhatsApp registration',updated_at=? WHERE id=?",(t,item['id']))
            candidates.append((item['id'],b['id'],wid,item['e164']))
    for iid,bid,wid,phone in candidates:
        try:
            live=node_api('/status',{'workerId':wid},timeout=5)
            if live.get('state')!='ready':raise RuntimeError('WhatsApp worker is not READY')
            reg=node_api('/verify-number',{'workerId':wid,'number':phone},timeout=15)
        except Exception as exc:
            with db.connect(True) as c:
                c.execute("UPDATE standalone_message_items SET status='pending',reason=?,updated_at=? WHERE id=? AND status='checking'",
                          ('Waiting for WhatsApp: '+str(exc)[:120],time.time(),iid))
                c.execute('UPDATE standalone_message_batches SET next_at=? WHERE id=?',(time.time()+60,bid))
            continue
        registered=reg.get('registered')
        with db.connect(True) as c:
            b=c.execute('SELECT * FROM standalone_message_batches WHERE id=?',(bid,)).fetchone()
            it=c.execute('SELECT status FROM standalone_message_items WHERE id=?',(iid,)).fetchone()
            if not b or not it or it['status']!='checking':continue
            if b['state']!='running':
                c.execute("UPDATE standalone_message_items SET status='pending' WHERE id=?",(iid,))
                continue
            if ledger.is_blocked(phone):
                c.execute("UPDATE standalone_message_items SET status='skipped',reason='Do not contact',updated_at=? WHERE id=?",(time.time(),iid))
                _refresh(c,bid,time.time())
                continue
            if registered is False:
                c.execute("UPDATE standalone_message_items SET status='skipped',reason='Not registered on WhatsApp',updated_at=? WHERE id=?",(time.time(),iid))
                _refresh(c,bid,time.time())
                continue
            if registered is not True or not reg.get('chatId'):
                c.execute("UPDATE standalone_message_items SET status='pending',reason='WhatsApp verification inconclusive',updated_at=? WHERE id=?",(time.time(),iid))
                c.execute('UPDATE standalone_message_batches SET next_at=? WHERE id=?',(time.time()+60,bid))
                continue
            # /verify-number queried this E.164 number through WhatsApp's
            # getNumberId(). WhatsApp may return a phone WID (@c.us) or an
            # opaque @lid WID. Both are valid only as a verified lookup result.
            chat_id, invalid_reason=verified_whatsapp_chat_id(phone,reg['chatId'])
            if invalid_reason:
                c.execute("UPDATE standalone_message_items SET status='skipped',reason=?,updated_at=? WHERE id=?",(invalid_reason,time.time(),iid))
                _refresh(c,bid,time.time())
                continue
            # Persist the ACTUAL identity returned by WhatsApp's lookup.
            # A phone may resolve to an opaque @lid rather than @c.us.
            c.execute('UPDATE standalone_message_items SET verified_chat_id=?,updated_at=? WHERE id=?',
                      (chat_id,time.time(),iid))
            message=b['message']
        # acquire_send() opens its own write transaction: never call it while
        # holding the queue transaction (SQLite would lock itself).
        if not db.acquire_send(wid,6):
            with db.connect(True) as c:
                c.execute("UPDATE standalone_message_items SET status='pending',reason='Worker send slot busy' WHERE id=? AND status='checking'",(iid,))
                c.execute('UPDATE standalone_message_batches SET next_at=? WHERE id=?',(time.time()+7,bid))
            continue
        with db.connect(True) as c:
            current=c.execute('''SELECT b.state,i.status FROM standalone_message_items i
                     JOIN standalone_message_batches b ON b.id=i.batch_id WHERE i.id=?''',(iid,)).fetchone()
            if not current or current['state']!='running' or current['status']!='checking':
                if current and current['status']=='checking':
                    c.execute("UPDATE standalone_message_items SET status='pending' WHERE id=?",(iid,))
                continue
            now=time.time()
            c.execute("UPDATE standalone_message_items SET status='sending',attempted_at=?,reason='',updated_at=? WHERE id=?",(now,now,iid))
        try:
            response=node_api('/send',{'workerId':wid,'chatId':chat_id,'text':message,'reply':False},timeout=125)
            outcome='sent' if response.get('sent') else 'uncertain'
            reason='' if outcome=='sent' else 'No confirmed WhatsApp message ID'
        except RuntimeError as exc:
            msg=str(exc)
            if 'HTTP 409' in msg:
                outcome='pending';reason='Worker busy or disconnected; will retry'
            elif 'HTTP 400' in msg:
                outcome='skipped';reason='Invalid WhatsApp send request'
            else:
                outcome='uncertain';reason=msg[:160]
        except Exception as exc:
            outcome='uncertain';reason=str(exc)[:160]
        with db.connect(True) as c:
            now=time.time()
            c.execute('''UPDATE standalone_message_items SET status=?,reason=?,
                      completed_at=?,updated_at=? WHERE id=?''',
                      (outcome,reason,now if outcome in ('sent','uncertain','skipped') else None,now,iid))
            if outcome=='pending':
                c.execute('UPDATE standalone_message_batches SET next_at=? WHERE id=?',(now+15,bid))
            else:_refresh(c,bid,now)
