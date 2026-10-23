"""Additive AI workforce tables; never modifies the original Lead Finder schema.

Every account has its own database. This module is deliberately independent from
Streamlit and from whatsapp-web.js, and all its methods scope IDs to that DB.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from .ai_outreach import normalize_phone
from . import business_knowledge as medmh_knowledge


def now() -> float:
    return time.time()


def uid() -> str:
    return uuid.uuid4().hex


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def is_opt_out(text: str) -> bool:
    """Safety-only stop requests, not a greeting or conversation classifier."""
    value = ' '.join(str(text or '').strip().casefold().split())
    return bool(re.search(r"\b(?:stop|unsubscribe|opt.?out|remove me|don't contact|do not contact|matzidsh\s+tsift\w*|mat3awdch\s+tsift\w*|arrêtez|arretez|désabonner|desabonner|ma\s*tb9ach\s*tsift|ماتبقاش تصيفط|توقف عن المراسلة)\b", value))


class WorkerDB:
    def __init__(self, db_path):
        self.path = str(Path(db_path).expanduser().resolve())

    @contextmanager
    def connect(self, write=False):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA busy_timeout=30000')
        if write:
            con.execute('BEGIN IMMEDIATE')
        try:
            yield con
            if write:
                con.commit()
        except BaseException:
            if write:
                con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        with self.connect(True) as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS ai_workers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                model_key_id TEXT, model_name TEXT, status TEXT NOT NULL DEFAULT 'disconnected',
                batching_seconds INTEGER NOT NULL DEFAULT 4, followup_enabled INTEGER NOT NULL DEFAULT 0,
                followup_hours INTEGER NOT NULL DEFAULT 48, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_generation_leases (
                worker_id TEXT PRIMARY KEY REFERENCES ai_workers(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_intro_jobs (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL REFERENCES ai_workers(id) ON DELETE CASCADE,
                state TEXT NOT NULL DEFAULT 'generating', attempts INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_first_contact_attempts (
                message_id INTEGER PRIMARY KEY REFERENCES ai_messages(id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL, lead_id INTEGER NOT NULL, attempted_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS ai_attempts_daily ON ai_first_contact_attempts(worker_id,attempted_at);
            CREATE TABLE IF NOT EXISTS ai_keys (
                id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES ai_workers(id) ON DELETE CASCADE,
                label TEXT NOT NULL, encrypted_key TEXT NOT NULL, key_suffix TEXT NOT NULL,
                test_results TEXT NOT NULL DEFAULT '[]', tested_at REAL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_assignments (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL REFERENCES ai_workers(id) ON DELETE CASCADE,
                assigned_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS ai_assignments_worker ON ai_assignments(worker_id);
            CREATE TABLE IF NOT EXISTS ai_permissions (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                opted_in INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '',
                proof TEXT NOT NULL DEFAULT '', recorded_at REAL, opted_out_at REAL);
            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE, lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
                worker_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('in','out')),
                body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'received',
                sent_at REAL NOT NULL, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS ai_messages_lead ON ai_messages(lead_id,id);
            CREATE INDEX IF NOT EXISTS ai_messages_worker ON ai_messages(worker_id,created_at);
            CREATE TABLE IF NOT EXISTS ai_threads (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                summary TEXT NOT NULL DEFAULT '', facts TEXT NOT NULL DEFAULT '{}',
                summarized_through INTEGER NOT NULL DEFAULT 0,
                replied_through INTEGER NOT NULL DEFAULT 0, last_inbound REAL,
                last_outbound REAL, followups_sent INTEGER NOT NULL DEFAULT 0,
                in_progress INTEGER NOT NULL DEFAULT 0, lease_at REAL);
            CREATE TABLE IF NOT EXISTS ai_chat_alias (
                worker_id TEXT NOT NULL REFERENCES ai_workers(id) ON DELETE CASCADE,
                chat_id TEXT NOT NULL,lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                PRIMARY KEY(worker_id,chat_id));
            CREATE TABLE IF NOT EXISTS ai_unmatched (
                event_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                body TEXT NOT NULL, sent_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_send_tracker (
                worker_id TEXT PRIMARY KEY REFERENCES ai_workers(id) ON DELETE CASCADE,
                last_attempt REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ai_intro_drafts (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL REFERENCES ai_workers(id) ON DELETE CASCADE,
                body TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_research_cache (
                url TEXT PRIMARY KEY, content TEXT NOT NULL, fetched_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id TEXT NOT NULL,
                key_id TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', recorded_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_account_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), business_name TEXT NOT NULL DEFAULT 'MedMH',
                portfolio TEXT NOT NULL DEFAULT 'https://medmh.online',
                services TEXT NOT NULL DEFAULT '', pricing TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '', daily_ai_limit INTEGER NOT NULL DEFAULT 100);
            INSERT OR IGNORE INTO ai_account_settings(id) VALUES (1);
            ''')
            c.execute('''CREATE TABLE IF NOT EXISTS ai_lead_control (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                paused INTEGER NOT NULL DEFAULT 0,
                changed_at REAL NOT NULL
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS ai_whatsapp_checks (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                phone_e164 TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'unknown',
                worker_id TEXT NOT NULL DEFAULT '', checked_at REAL NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS ai_auto_assign_config (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
                last_attempt REAL NOT NULL DEFAULT 0
            )''')
            c.execute('INSERT OR IGNORE INTO ai_auto_assign_config(id) VALUES(1)')
            control_columns={x['name'] for x in c.execute('PRAGMA table_info(ai_lead_control)')}
            if 'handoff' not in control_columns:
                c.execute('ALTER TABLE ai_lead_control ADD COLUMN handoff INTEGER NOT NULL DEFAULT 0')
            # Sales qualification persists across worker restarts, and can be
            # reviewed when the developer takes over the lead.
            thread_columns={x['name'] for x in c.execute('PRAGMA table_info(ai_threads)')}
            if 'retry_after' not in thread_columns:
                c.execute('ALTER TABLE ai_threads ADD COLUMN retry_after REAL NOT NULL DEFAULT 0')
            # Record a concise reply pipeline stage, so failure is not hidden.
            # No message contents, Gemini keys, or phone numbers are stored here.
            c.execute('''CREATE TABLE IF NOT EXISTS ai_reply_trace (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL)''')
            if 'qualification_questions' not in thread_columns:
                c.execute('ALTER TABLE ai_threads ADD COLUMN qualification_questions INTEGER NOT NULL DEFAULT 0')
            if 'qualification_facts' not in thread_columns:
                c.execute("ALTER TABLE ai_threads ADD COLUMN qualification_facts TEXT NOT NULL DEFAULT '{}'")
            if 'handoff_reason' not in control_columns:
                c.execute("ALTER TABLE ai_lead_control ADD COLUMN handoff_reason TEXT NOT NULL DEFAULT ''")
            if 'handoff_at' not in control_columns:
                c.execute('ALTER TABLE ai_lead_control ADD COLUMN handoff_at REAL')
            # Additive migrations: old project leads and messages remain untouched.
            existing = {x['name'] for x in c.execute('PRAGMA table_info(ai_workers)')}
            if 'outreach_enabled' not in existing:
                c.execute('ALTER TABLE ai_workers ADD COLUMN outreach_enabled INTEGER NOT NULL DEFAULT 0')
            if 'daily_new_contacts' not in existing:
                c.execute('ALTER TABLE ai_workers ADD COLUMN daily_new_contacts INTEGER NOT NULL DEFAULT 10')
            worker_columns = {x['name'] for x in c.execute('PRAGMA table_info(ai_workers)')}
            for field, ddl in (
                ('daily_token_limit', 'INTEGER NOT NULL DEFAULT 10000'),
                ('reply_delay_min', 'INTEGER NOT NULL DEFAULT 4'),
                ('reply_delay_max', 'INTEGER NOT NULL DEFAULT 30'),
            ):
                if field not in worker_columns:
                    c.execute(f'ALTER TABLE ai_workers ADD COLUMN {field} {ddl}')
            usage_columns = {x['name'] for x in c.execute('PRAGMA table_info(ai_usage)')}
            if 'lead_id' not in usage_columns:
                c.execute('ALTER TABLE ai_usage ADD COLUMN lead_id INTEGER')
            c.execute('CREATE INDEX IF NOT EXISTS ai_usage_worker_day ON ai_usage(worker_id,recorded_at)')
            c.execute('CREATE INDEX IF NOT EXISTS ai_usage_lead ON ai_usage(lead_id)')
            columns = {x['name'] for x in c.execute('PRAGMA table_info(ai_messages)')}
            if 'kind' not in columns:
                c.execute("ALTER TABLE ai_messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'manual'")
            # Apply the explicitly owner-approved catalog once per revision.
            # Record the previous settings for auditing / manual rollback.
            # Existing worker quotas, AI keys, conversation history, and leads
            # are not touched. Subsequent dashboard edits remain persistent.
            c.execute('''CREATE TABLE IF NOT EXISTS ai_knowledge_revisions (
                revision TEXT PRIMARY KEY, previous_settings_json TEXT NOT NULL,
                applied_at REAL NOT NULL)''')
            if not c.execute('SELECT 1 FROM ai_knowledge_revisions WHERE revision=?',
                             (medmh_knowledge.KNOWLEDGE_REVISION,)).fetchone():
                previous = c.execute('''SELECT business_name,portfolio,services,pricing,instructions
                                         FROM ai_account_settings WHERE id=1''').fetchone()
                if previous is None:
                    raise RuntimeError('Missing shared MEDMH knowledge settings')
                c.execute('''UPDATE ai_account_settings
                             SET business_name=?,portfolio=?,services=?,pricing=?,instructions=?
                             WHERE id=1''', (
                    medmh_knowledge.BUSINESS_NAME, medmh_knowledge.PORTFOLIO,
                    medmh_knowledge.SERVICES, medmh_knowledge.PRICING,
                    medmh_knowledge.SALES_INSTRUCTIONS))
                c.execute('''INSERT INTO ai_knowledge_revisions
                             (revision,previous_settings_json,applied_at) VALUES(?,?,?)''',
                          (medmh_knowledge.KNOWLEDGE_REVISION,
                           json.dumps(dict(previous),ensure_ascii=False),now()))
            # Named, reusable instruction profiles. Existing settings are
            # preserved verbatim in the imported profile, never overwritten.
            c.execute('''CREATE TABLE IF NOT EXISTS ai_instruction_profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                instructions TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL)''')
            legacy_id='legacy-medmh'
            if not c.execute('SELECT 1 FROM ai_instruction_profiles WHERE id=?',(legacy_id,)).fetchone():
                current=c.execute('SELECT * FROM ai_account_settings WHERE id=1').fetchone()
                imported='\n\n'.join(x for x in (
                    'Business: '+str(current['business_name'] or '').strip(),
                    'Portfolio: '+str(current['portfolio'] or '').strip(),
                    'Services: '+str(current['services'] or '').strip(),
                    'Pricing policy: '+str(current['pricing'] or '').strip(),
                    'Instructions: '+str(current['instructions'] or '').strip()
                ) if x.strip())
                c.execute('INSERT INTO ai_instruction_profiles VALUES(?,?,?,?,?)',
                          (legacy_id,'Imported MEDMH knowledge',imported,now(),now()))
            cols={x['name'] for x in c.execute('PRAGMA table_info(ai_workers)')}
            if 'instruction_profile_id' not in cols:
                c.execute("ALTER TABLE ai_workers ADD COLUMN instruction_profile_id TEXT NOT NULL DEFAULT 'legacy-medmh'")
            c.execute("UPDATE ai_workers SET instruction_profile_id=? WHERE instruction_profile_id IS NULL",(legacy_id,))
            cols={x['name'] for x in c.execute('PRAGMA table_info(ai_threads)')}
            if 'instruction_profile_id' not in cols:
                c.execute('ALTER TABLE ai_threads ADD COLUMN instruction_profile_id TEXT')
            # Pin old conversations to the imported profile before workers can
            # activate other profiles. Never change a conversation's purpose.
            c.execute('UPDATE ai_threads SET instruction_profile_id=? WHERE instruction_profile_id IS NULL',
                      (legacy_id,))
            # Media is profile-scoped, additive, and never stored in source/Git.
            c.execute('''CREATE TABLE IF NOT EXISTS ai_profile_media (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES ai_instruction_profiles(id),
                display_name TEXT NOT NULL, description TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                file_size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)''')
            c.execute('CREATE INDEX IF NOT EXISTS ai_profile_media_profile ON ai_profile_media(profile_id)')
            msg_cols={x['name'] for x in c.execute('PRAGMA table_info(ai_messages)')}
            if 'media_id' not in msg_cols:
                c.execute('ALTER TABLE ai_messages ADD COLUMN media_id TEXT')
        return self

    @staticmethod
    def asdict(row):
        return dict(row) if row is not None else None

    def workers(self):
        with self.connect() as c:
            items = [dict(r) for r in c.execute('SELECT * FROM ai_workers ORDER BY created_at')]
            for w in items:
                w['lead_count'] = c.execute('SELECT COUNT(*) FROM ai_assignments WHERE worker_id=?', (w['id'],)).fetchone()[0]
            return items

    def worker(self, worker_id):
        with self.connect() as c:
            return self.asdict(c.execute('SELECT * FROM ai_workers WHERE id=?', (worker_id,)).fetchone())

    def create_worker(self, name):
        name = str(name).strip()[:70]
        if not name:
            raise ValueError('Worker name is required')
        value = uid()
        with self.connect(True) as c:
            c.execute('INSERT INTO ai_workers(id,name,created_at,instruction_profile_id) VALUES(?,?,?,?)', (value,name,now(),'legacy-medmh'))
        return value

    def configure_worker(self, worker_id, **fields):
        allowed = {'name','enabled','model_key_id','model_name','batching_seconds','outreach_enabled','daily_new_contacts','daily_token_limit','reply_delay_min','reply_delay_max'}
        if set(fields) - allowed:
            raise ValueError('Unsupported worker setting')
        if not self.worker(worker_id):
            raise ValueError('Worker not found')
        if 'batching_seconds' in fields and not 2 <= int(fields['batching_seconds']) <= 15:
            raise ValueError('Batching delay must be 2 to 15 seconds')
        if 'daily_new_contacts' in fields and not 1 <= int(fields['daily_new_contacts']) <= 100:
            raise ValueError('Daily new-contact limit must be between 1 and 100')
        if 'daily_token_limit' in fields and not 100 <= int(fields['daily_token_limit']) <= 2000000:
            raise ValueError('Daily token limit must be between 100 and 2,000,000')
        current=self.worker(worker_id)
        lo=int(fields.get('reply_delay_min',current['reply_delay_min']))
        hi=int(fields.get('reply_delay_max',current['reply_delay_max']))
        if not 0 <= lo <= hi <= 60:
            raise ValueError('Reply delay requires 0 ≤ minimum ≤ maximum ≤ 60 seconds')
        if 'model_key_id' in fields and fields['model_key_id']:
            with self.connect() as c:
                if not c.execute('SELECT 1 FROM ai_keys WHERE id=? AND worker_id=?', (fields['model_key_id'],worker_id)).fetchone():
                    raise ValueError('This key belongs to another worker')
        if not fields:
            return
        with self.connect(True) as c:
            c.execute('UPDATE ai_workers SET '+', '.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),worker_id))

    def keys(self, worker_id, reveal=False):
        with self.connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM ai_keys WHERE worker_id=? ORDER BY created_at', (worker_id,))]
        if not reveal:
            for r in rows:
                del r['encrypted_key']
        for r in rows:
            r['test_results'] = json.loads(r['test_results'])
        return rows

    def add_key(self, worker_id, label, encrypted_key, suffix):
        if not self.worker(worker_id):
            raise ValueError('Worker not found')
        key_id = uid()
        with self.connect(True) as c:
            c.execute('INSERT INTO ai_keys(id,worker_id,label,encrypted_key,key_suffix,created_at) VALUES(?,?,?,?,?,?)',
                      (key_id,worker_id,str(label).strip()[:70] or 'Gemini key',encrypted_key,suffix,now()))
        return key_id

    def replace_keys(self, worker_id, encrypted_keys):
        """Atomically replace a worker's key pool; preserve its tested model selection when possible."""
        if not self.worker(worker_id):
            raise ValueError('Worker does not exist')
        with self.connect(True) as c:
            old = c.execute('SELECT model_name FROM ai_workers WHERE id=?',(worker_id,)).fetchone()
            selected = old['model_name'] if old else None
            c.execute('DELETE FROM ai_keys WHERE worker_id=?',(worker_id,))
            for index, (ciphertext, suffix) in enumerate(encrypted_keys, 1):
                c.execute('INSERT INTO ai_keys(id,worker_id,label,encrypted_key,key_suffix,created_at) VALUES(?,?,?,?,?,?)',
                    (uid(),worker_id,f'Key {index}',ciphertext,suffix,now()+index*.0001))
            c.execute('UPDATE ai_workers SET model_key_id=NULL,model_name=? WHERE id=?', (selected,worker_id))

    def working_models(self, worker_id):
        keys=self.keys(worker_id)
        return sorted({r['model'] for key in keys for r in key['test_results'] if r.get('working')})

    def select_working_model(self, worker_id, model):
        keys=self.keys(worker_id)
        match=next((key for key in keys if any(r.get('model')==model and r.get('working') for r in key['test_results'])),None)
        if not match: raise ValueError('Select a model that passed testing')
        self.configure_worker(worker_id,model_key_id=match['id'],model_name=model)

    def tokens_today(self, worker_id):
        import datetime
        utc=datetime.datetime.now(datetime.timezone.utc)
        start=utc.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        with self.connect() as c:
            row=c.execute('SELECT COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(output_tokens),0) AS output FROM ai_usage WHERE worker_id=? AND recorded_at>=?',(worker_id,start)).fetchone()
            return {'input':int(row['input']),'output':int(row['output']),'total':int(row['input'])+int(row['output'])}

    def token_budget_available(self, worker_id, reserve=300):
        worker=self.worker(worker_id)
        return bool(worker and self.tokens_today(worker_id)['total']+reserve<=int(worker['daily_token_limit']))

    def lead_token_totals(self, ids):
        ids=sorted({int(x) for x in ids})
        if not ids:return {}
        with self.connect() as c:
            mark=','.join('?' for _ in ids)
            rows=c.execute(f'''SELECT lead_id, COALESCE(SUM(input_tokens),0) AS input,
                COALESCE(SUM(output_tokens),0) AS output FROM ai_usage WHERE lead_id IN ({mark})
                GROUP BY lead_id''',ids).fetchall()
            return {int(r['lead_id']):{'input':r['input'],'output':r['output']} for r in rows}

    def delete_key(self, worker_id, key_id):
        with self.connect(True) as c:
            c.execute('DELETE FROM ai_keys WHERE worker_id=? AND id=?',(worker_id,key_id))
            c.execute('UPDATE ai_workers SET model_key_id=NULL,model_name=NULL WHERE id=? AND model_key_id=?',(worker_id,key_id))

    def tested_models(self, key_id, worker_id, results):
        with self.connect(True) as c:
            c.execute('UPDATE ai_keys SET test_results=?,tested_at=? WHERE id=? AND worker_id=?',
                      (json.dumps(results,ensure_ascii=False),now(),key_id,worker_id))

    def account_settings(self):
        with self.connect() as c:
            return dict(c.execute('SELECT * FROM ai_account_settings WHERE id=1').fetchone())

    def update_settings(self, **fields):
        allowed = {'business_name','portfolio','services','pricing','instructions','daily_ai_limit'}
        if set(fields)-allowed:
            raise ValueError('Invalid setting')
        if 'daily_ai_limit' in fields and not 1 <= int(fields['daily_ai_limit']) <= 50000:
            raise ValueError('Daily limit is invalid')
        with self.connect(True) as c:
            c.execute('UPDATE ai_account_settings SET '+', '.join(k+'=?' for k in fields)+' WHERE id=1', tuple(fields.values()))

    def media_for_profile(self, profile_id, only_enabled=True):
        with self.connect() as c:
            return [dict(x) for x in c.execute(
                'SELECT * FROM ai_profile_media WHERE profile_id=? '+
                ('AND enabled=1 ' if only_enabled else '')+'ORDER BY created_at,id', (profile_id,))]

    def available_media_for_lead(self, profile_id, lead_id):
        """Hide files already sent/queued/uncertain to this contact from Gemini."""
        with self.connect() as c:
            return [dict(r) for r in c.execute('''SELECT a.* FROM ai_profile_media a WHERE a.profile_id=? AND a.enabled=1
                AND NOT EXISTS (SELECT 1 FROM ai_messages m WHERE m.lead_id=?
                  AND m.media_id=a.id AND m.status IN ('sent','uncertain','sending','queued_reply'))
                ORDER BY a.created_at,a.id''',(profile_id,lead_id))]

    def media_delivery_state(self, profile_id, lead_id, limit=6):
        # Ground future AI replies in recorded, not assumed, media outcomes.
        with self.connect() as c:
            return [dict(r) for r in c.execute('''SELECT a.display_name AS name,
                a.kind, m.status, m.created_at AS attempted_at
                FROM ai_messages m JOIN ai_profile_media a ON m.media_id=a.id
                WHERE a.profile_id=? AND m.lead_id=? AND m.direction='out'
                AND m.status IN ('sent','uncertain','queued_reply','sending','failed','blocked')
                ORDER BY m.id DESC LIMIT ?''',(profile_id,lead_id,int(limit)))]

    def media_readiness_for_lead(self, worker_id, lead_id):
        '''Local operator diagnostics; never return files from another worker.

        No database writes, WhatsApp sends, or automatic retries. The status
        answers whether Gemini *can* select a profile attachment for this lead.
        '''
        from .ai_media import media_root
        import hashlib
        worker=self.worker(worker_id)
        if not worker:
            raise ValueError('Worker does not exist')
        lead_id=int(lead_id)
        with self.connect() as c:
            assigned=c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?',(lead_id,)).fetchone()
            if not assigned or assigned['worker_id']!=worker_id:
                raise ValueError('This lead is not assigned to the selected worker')
            thread=c.execute('SELECT instruction_profile_id FROM ai_threads WHERE lead_id=?',(lead_id,)).fetchone()
            ctrl=c.execute('SELECT paused,handoff FROM ai_lead_control WHERE lead_id=?',(lead_id,)).fetchone()
            files=[dict(x) for x in c.execute('''SELECT id,display_name,filename,enabled,kind,file_size,sha256 FROM ai_profile_media
                   WHERE profile_id=? ORDER BY created_at''',(worker.get('instruction_profile_id'),))]
            for f in files:
                old=c.execute('''SELECT status FROM ai_messages WHERE lead_id=? AND media_id=?
                      AND direction='out' AND status IN ('sent','uncertain','sending','queued_reply')
                      ORDER BY id DESC LIMIT 1''',(lead_id,f['id'])).fetchone()
                f['status']=(('already_sent_or_pending: '+old['status']) if old else
                       'disabled' if not f['enabled'] else 'available')
                # Do not expose storage paths; just report file readiness.
                if f['status']=='available':
                    saved=media_root()/f['filename']
                    if saved.is_symlink() or not saved.is_file():
                        f['status']='stored_file_missing'
                    elif (saved.stat().st_size != f['file_size'] or
                          hashlib.sha256(saved.read_bytes()).hexdigest() != f['sha256']):
                        f['status']='stored_file_corrupted'
                for hidden in ('filename','enabled','id','file_size','sha256'):
                    f.pop(hidden,None)
        return {'active_profile':self.instruction_profile(worker.get('instruction_profile_id'))['name']
                    if worker.get('instruction_profile_id') and self.instruction_profile(worker['instruction_profile_id']) else '(none)',
                'thread_profile_matches_worker':bool(thread and thread['instruction_profile_id']==worker.get('instruction_profile_id')),
                'lead_handoff':bool(ctrl and ctrl['handoff']),
                'lead_paused':bool(ctrl and ctrl['paused']),
                'files':files}

    def recent_worker_media_delivery(self, worker_id, limit=20):
        # Do not reveal files/contacts from another worker in the controls UI.
        with self.connect() as c:
            return [dict(r) for r in c.execute("""SELECT m.id AS message_id, m.lead_id,
                m.status, m.created_at, a.display_name, a.kind
                FROM ai_messages m JOIN ai_profile_media a ON a.id=m.media_id
                WHERE m.worker_id=? AND m.direction='out'
                ORDER BY m.id DESC LIMIT ?""",(worker_id,int(limit)))]

    def unlock_unconfirmed_media(self, worker_id, message_id):
        """User has checked actual recipient chat and confirms no attachment.

        No message is resent here. Only a later eligible inbound reply may select
        the same approved file again. Unconfirmed attempts stay in audit history.
        """
        with self.connect(True) as c:
            row=c.execute("""SELECT m.id,m.lead_id,m.media_id,m.kind,a.profile_id,
                w.instruction_profile_id AS active_profile
                FROM ai_messages m JOIN ai_profile_media a ON a.id=m.media_id
                JOIN ai_workers w ON w.id=m.worker_id
                WHERE m.id=? AND m.worker_id=? AND m.direction='out'
                AND m.status='uncertain'""",(int(message_id),worker_id)).fetchone()
            if (not row or row['kind']=='handoff' or row['profile_id']!=row['active_profile']):
                raise ValueError('This attachment cannot be unlocked for retry')
            c.execute("UPDATE ai_messages SET status='failed' WHERE id=? AND status='uncertain'",(int(message_id),))
            self._trace_in_tx(c,row['lead_id'],worker_id,'media_retry_unlocked',
                              'Operator verified absent WhatsApp attachment; no resend queued')
            return True

    def media_record(self, media_id):
        with self.connect() as c:
            r=c.execute('SELECT * FROM ai_profile_media WHERE id=?',(media_id,)).fetchone()
            return dict(r) if r else None

    def add_profile_media(self, profile_id, display_name, description, filename, kind, file_size, sha256, media_id):
        label=str(display_name or '').strip()[:120]
        description=str(description or '').strip()[:500]
        if not label or not description:
            raise ValueError('Give the attachment a name and clear sending instructions')
        if kind not in ('document','image','voice') or not re.fullmatch('[0-9a-f]{32}\\.(?:pdf|png|jpg|webp|ogg)',filename):
            raise ValueError('Invalid attachment file')
        with self.connect(True) as c:
            if not c.execute('SELECT 1 FROM ai_instruction_profiles WHERE id=?',(profile_id,)).fetchone():
                raise ValueError('Profile does not exist')
            c.execute('''INSERT INTO ai_profile_media(id,profile_id,display_name,description,filename,kind,file_size,sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (media_id,profile_id,label,description,filename,kind,int(file_size),sha256,now()))

    def set_media_enabled(self, media_id, enabled):
        with self.connect(True) as c:
            if c.execute('UPDATE ai_profile_media SET enabled=? WHERE id=?',(int(bool(enabled)),media_id)).rowcount!=1:
                raise ValueError('Attachment not found')

    def delete_profile_media(self, media_id):
        # Already-sent metadata remains in ai_messages (media_id), but the file
        # will be removed. Pending messages are cancelled first.
        with self.connect(True) as c:
            r=c.execute('SELECT filename FROM ai_profile_media WHERE id=?',(media_id,)).fetchone()
            if not r: raise ValueError('Attachment not found')
            c.execute("UPDATE ai_messages SET status='blocked' WHERE media_id=? AND status IN ('queued','queued_reply')",(media_id,))
            c.execute('DELETE FROM ai_profile_media WHERE id=?',(media_id,))
            return r['filename']

    def validate_media_choice(self, thread, media_id):
        media=self.media_record(media_id)
        if (not media or not media['enabled'] or
            media['profile_id'] != thread.get('instruction_profile_id')):
            raise ValueError('AI selected a missing or unauthorized attachment')
        from .ai_media import media_root
        import hashlib
        path=media_root()/media['filename']
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=media['file_size']:
            raise ValueError('Attachment file is missing or changed')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=media['sha256']:
            raise ValueError('Attachment file integrity mismatch')
        with self.connect() as c:
            repeat=c.execute("SELECT 1 FROM ai_messages WHERE lead_id=? AND media_id=? AND direction='out' AND status IN ('sent','uncertain','sending','queued_reply') LIMIT 1",
                             (thread['lead_id'],media_id)).fetchone()
        if repeat: raise ValueError('Attachment already sent or pending for this lead')
        return media

    def instruction_profiles(self):
        with self.connect() as c:
            return [dict(row) for row in c.execute(
                'SELECT * FROM ai_instruction_profiles ORDER BY name COLLATE NOCASE')]

    def instruction_profile(self, profile_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM ai_instruction_profiles WHERE id=?', (profile_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _validate_profile(name, instructions):
        name=str(name or '').strip()
        instructions=str(instructions or '').strip()
        if not 1<=len(name)<=80:
            raise ValueError('Profile name must contain 1–80 characters')
        if not 10<=len(instructions)<=16000:
            raise ValueError('Instructions must contain 10–16,000 characters')
        return name,instructions

    def create_instruction_profile(self, name, instructions):
        name,instructions=self._validate_profile(name,instructions)
        profile_id=uid()
        with self.connect(True) as c:
            try:
                c.execute('INSERT INTO ai_instruction_profiles VALUES(?,?,?,?,?)',
                          (profile_id,name,instructions,now(),now()))
            except sqlite3.IntegrityError as exc:
                raise ValueError('A profile already uses that name') from exc
        return profile_id

    def update_instruction_profile(self, profile_id, name, instructions):
        name,instructions=self._validate_profile(name,instructions)
        with self.connect(True) as c:
            if not c.execute('SELECT 1 FROM ai_instruction_profiles WHERE id=?',(profile_id,)).fetchone():
                raise ValueError('Profile not found')
            try:
                c.execute('UPDATE ai_instruction_profiles SET name=?,instructions=?,updated_at=? WHERE id=?',
                          (name,instructions,now(),profile_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('A profile already uses that name') from exc

    def delete_instruction_profile(self, profile_id):
        with self.connect(True) as c:
            if c.execute('SELECT 1 FROM ai_workers WHERE instruction_profile_id=?',(profile_id,)).fetchone():
                raise ValueError('This profile is active on a worker. Switch that worker first.')
            if c.execute('SELECT 1 FROM ai_threads WHERE instruction_profile_id=?',(profile_id,)).fetchone():
                raise ValueError('This profile has saved conversations and cannot be deleted safely.')
            if c.execute('SELECT 1 FROM ai_profile_media WHERE profile_id=?',(profile_id,)).fetchone():
                raise ValueError('Delete this profile’s media first')
            if c.execute('SELECT COUNT(*) FROM ai_instruction_profiles').fetchone()[0]<=1:
                raise ValueError('Keep at least one profile')
            if c.execute('DELETE FROM ai_instruction_profiles WHERE id=?',(profile_id,)).rowcount!=1:
                raise ValueError('Profile not found')

    def activate_instruction_profile(self, worker_id, profile_id):
        with self.connect(True) as c:
            if not c.execute('SELECT 1 FROM ai_instruction_profiles WHERE id=?',(profile_id,)).fetchone():
                raise ValueError('Profile not found')
            if not c.execute('SELECT 1 FROM ai_workers WHERE id=?',(worker_id,)).fetchone():
                raise ValueError('Worker not found')
            c.execute('UPDATE ai_workers SET instruction_profile_id=? WHERE id=?',(profile_id,worker_id))
            # Auto-generated first introductions from another purpose must not
            # leak into the newly activated role. Explicit manual sends remain.
            c.execute("UPDATE ai_messages SET status='superseded' WHERE worker_id=? AND status='queued' AND kind='intro'",
                      (worker_id,))
            c.execute("UPDATE ai_intro_jobs SET state='skipped' WHERE worker_id=? AND state='queued'",
                      (worker_id,))
            # Cancel not-yet-sent old-role AI replies. Preserve final handoffs,
            # deliberate manual messages, and the previous conversation data.
            c.execute('''UPDATE ai_messages SET status='superseded'
                WHERE worker_id=? AND direction='out' AND status='queued_reply'
                  AND kind!='handoff' AND lead_id IN (
                    SELECT lead_id FROM ai_threads
                    WHERE instruction_profile_id IS NOT NULL AND instruction_profile_id!=?)''',
                      (worker_id,profile_id))

    def profile_for_thread(self, thread):
        pid=thread.get('instruction_profile_id') or self.worker(thread['worker_id']).get('instruction_profile_id')
        return self.instruction_profile(pid) if pid else None

    def paused_profile_threads(self, worker_id):
        with self.connect() as c:
            return c.execute('''SELECT COUNT(*) FROM ai_threads t
                JOIN ai_assignments a ON a.lead_id=t.lead_id
                JOIN ai_workers w ON w.id=a.worker_id
                WHERE a.worker_id=? AND t.instruction_profile_id IS NOT NULL
                AND t.instruction_profile_id!=w.instruction_profile_id''',(worker_id,)).fetchone()[0]

    def assign(self, worker_id, lead_ids):
        if not self.worker(worker_id):
            raise ValueError('Worker not found')
        ids = sorted({int(x) for x in lead_ids})
        with self.connect(True) as c:
            for lead_id in ids:
                r = c.execute('SELECT id FROM leads WHERE id=?', (lead_id,)).fetchone()
                if not r:
                    raise ValueError(f'Lead {lead_id} is missing')
            for lead_id in ids:
                c.execute('''INSERT INTO ai_assignments(lead_id,worker_id,assigned_at) VALUES(?,?,?)
                     ON CONFLICT(lead_id) DO UPDATE SET worker_id=excluded.worker_id,assigned_at=excluded.assigned_at''',
                          (lead_id,worker_id,now()))
        return len(ids)

    def unassign(self, lead_ids):
        with self.connect(True) as c:
            c.executemany('DELETE FROM ai_assignments WHERE lead_id=?', [(int(x),) for x in lead_ids])

    def wa_statuses(self, ids):
        ids=sorted({int(x) for x in ids})
        if not ids:return {}
        with self.connect() as c:
            q=','.join('?' for _ in ids)
            return {int(r['lead_id']):dict(r) for r in c.execute(
                f'SELECT * FROM ai_whatsapp_checks WHERE lead_id IN ({q})',ids)}

    def auto_assignment_enabled(self):
        with self.connect() as c:
            r=c.execute('SELECT enabled FROM ai_auto_assign_config WHERE id=1').fetchone()
            return bool(r and r['enabled'])

    def set_auto_assignment(self,enabled):
        with self.connect(True) as c:
            c.execute('UPDATE ai_auto_assign_config SET enabled=? WHERE id=1',(int(bool(enabled)),))

    def auto_candidate(self, interval=60, max_daily=25):
        """One unassigned business lead at a time, with a strict daily cap.

        WhatsApp registration does not imply permission for sales outreach.
        This process never sends a message.
        """
        with self.connect(True) as c:
            t=now()
            cfg=c.execute('SELECT * FROM ai_auto_assign_config WHERE id=1').fetchone()
            if not cfg or not cfg['enabled'] or t-cfg['last_attempt']<interval:return None
            daily=c.execute('SELECT count(*) FROM ai_whatsapp_checks WHERE checked_at>=?',(t-86400,)).fetchone()[0]
            if daily>=max_daily:return None
            rows=c.execute("""SELECT l.id,l.phone,l.phone_e164,l.country_code FROM leads l
                LEFT JOIN ai_permissions p ON p.lead_id=l.id
                LEFT JOIN ai_assignments a ON a.lead_id=l.id
                LEFT JOIN ai_whatsapp_checks wc ON wc.lead_id=l.id
                WHERE l.archived=0 AND l.do_not_contact=0 AND p.opted_out_at IS NULL AND a.lead_id IS NULL
                  AND (wc.lead_id IS NULL OR wc.checked_at<?)
                ORDER BY l.id LIMIT 25""",(t-30*86400,)).fetchall()
            if not rows:return None
            c.execute('UPDATE ai_auto_assign_config SET last_attempt=? WHERE id=1',(t,))
            return [dict(x) for x in rows]

    def record_whatsapp_check(self,lead_id,number,status,worker_id='',detail=''):
        assert status in ('registered','not_registered','unknown','not_mobile','duplicate')
        with self.connect(True) as c:
            c.execute("""INSERT INTO ai_whatsapp_checks(lead_id,phone_e164,status,worker_id,checked_at,detail)
                VALUES(?,?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET
                phone_e164=excluded.phone_e164,status=excluded.status,worker_id=excluded.worker_id,
                checked_at=excluded.checked_at,detail=excluded.detail""",
                (int(lead_id),number,status,worker_id,now(),str(detail)[:170]))

    def auto_assign_registered(self,lead_id,worker_id,phone):
        """Atomic check avoids assigning a second record of the same phone."""
        with self.connect(True) as c:
            record=c.execute('SELECT l.id,l.phone,l.phone_e164,l.country_code,l.do_not_contact,l.archived,COALESCE(p.opted_in,0) opted_in, p.opted_out_at FROM leads l LEFT JOIN ai_permissions p ON p.lead_id=l.id WHERE l.id=?',(lead_id,)).fetchone()
            if not record or record['archived'] or record['do_not_contact'] or record['opted_out_at']:
                return False
            if c.execute('SELECT 1 FROM ai_assignments WHERE lead_id=?',(lead_id,)).fetchone():return False
            for r in c.execute("""SELECT l.phone,l.phone_e164,l.country_code FROM ai_assignments a JOIN leads l ON l.id=a.lead_id"""):
                if self._lead_phone(r)==phone:return False
            c.execute('INSERT INTO ai_assignments(lead_id,worker_id,assigned_at) VALUES(?,?,?)',(lead_id,worker_id,now()))
            return True

    def set_handoff(self,lead_id,enabled):
        with self.connect(True) as c:
            if not c.execute('SELECT 1 FROM leads WHERE id=?',(int(lead_id),)).fetchone():return
            c.execute("""INSERT INTO ai_lead_control(lead_id,paused,handoff,changed_at,handoff_reason,handoff_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET
                handoff=excluded.handoff,changed_at=excluded.changed_at,
                handoff_reason=excluded.handoff_reason,handoff_at=excluded.handoff_at""",
                (int(lead_id),int(bool(enabled)),int(bool(enabled)),now(),
                 'manual' if enabled else '',now() if enabled else None))
            if enabled:
                c.execute('UPDATE ai_messages SET status="blocked" WHERE lead_id=? AND status="queued_reply"',(lead_id,))

    def leads_for_worker(self, worker_id):
        with self.connect() as c:
            return [dict(x) for x in c.execute('''SELECT l.id,l.name,l.city,l.country_code,l.phone,l.phone_e164,l.do_not_contact,
                a.worker_id,COALESCE(p.opted_in,0) opted_in,COALESCE(p.opted_out_at,0) opted_out_at
                FROM ai_assignments a JOIN leads l ON l.id=a.lead_id
                LEFT JOIN ai_permissions p ON p.lead_id=l.id WHERE a.worker_id=? ORDER BY l.id DESC''',(worker_id,))]

    def is_lead_paused(self, lead_id):
        with self.connect() as c:
            row=c.execute('SELECT paused FROM ai_lead_control WHERE lead_id=?',(int(lead_id),)).fetchone()
            return bool(row and row['paused'])

    def set_lead_paused(self, lead_id, paused):
        with self.connect(True) as c:
            if not c.execute('SELECT 1 FROM leads WHERE id=?',(int(lead_id),)).fetchone():
                raise ValueError('Lead does not exist')
            c.execute('''INSERT INTO ai_lead_control(lead_id,paused,changed_at) VALUES(?,?,?)
                ON CONFLICT(lead_id) DO UPDATE SET paused=excluded.paused,changed_at=excluded.changed_at''',
                (int(lead_id),int(bool(paused)),now()))
            if paused:
                # Existing queued AI replies must not be sent after a pause.
                c.execute("UPDATE ai_messages SET status='blocked' WHERE lead_id=? AND direction='out' AND (status='queued_reply' OR (status='queued' AND kind IN ('followup','intro')))",(int(lead_id),))

    def lead_ai_state(self, lead_id):
        with self.connect() as c:
            row=c.execute('''SELECT a.worker_id, w.name worker_name,
                COALESCE(ctrl.paused,0) paused, COALESCE(ctrl.handoff,0) handoff, COALESCE(ctrl.handoff_reason,'') handoff_reason, COALESCE(p.opted_in,0) opted_in,
                COALESCE(p.opted_out_at,0) opted_out_at
                FROM ai_assignments a JOIN ai_workers w ON a.worker_id=w.id
                LEFT JOIN ai_lead_control ctrl ON ctrl.lead_id=a.lead_id
                LEFT JOIN ai_permissions p ON p.lead_id=a.lead_id
                WHERE a.lead_id=?''',(int(lead_id),)).fetchone()
            return dict(row) if row else None

    def lead_ai_states(self, ids):
        ids=[int(i) for i in ids]
        if not ids:return {}
        placeholders=','.join('?' for _ in ids)
        with self.connect() as c:
            return {r['lead_id']:dict(r) for r in c.execute('''SELECT a.lead_id,a.worker_id,w.name worker_name,
                COALESCE(ctrl.paused,0) paused,COALESCE(ctrl.handoff,0) handoff,COALESCE(ctrl.handoff_reason,'') handoff_reason,COALESCE(p.opted_in,0) opted_in,
                COALESCE(p.opted_out_at,0) opted_out_at
                FROM ai_assignments a JOIN ai_workers w ON a.worker_id=w.id
                LEFT JOIN ai_lead_control ctrl ON ctrl.lead_id=a.lead_id
                LEFT JOIN ai_permissions p ON p.lead_id=a.lead_id
                WHERE a.lead_id IN ('''+placeholders+')',ids)}

    def qualification_briefs(self, ids):
        ids=sorted({int(i) for i in ids})
        if not ids:return {}
        with self.connect() as c:
            placeholders=','.join('?' for _ in ids)
            rows=c.execute(f"""SELECT t.lead_id,t.qualification_facts,c.handoff_reason
                FROM ai_threads t LEFT JOIN ai_lead_control c ON c.lead_id=t.lead_id
                WHERE t.lead_id IN ({placeholders})""",ids)
            out={}
            for r in rows:
                try: fields=json.loads(r['qualification_facts'] or '{}')
                except (ValueError,TypeError): fields={}
                out[int(r['lead_id'])]={"facts":fields,
                    "summary":' · '.join(str(fields.get(k)) for k in ('service','requirements','budget','deadline') if fields.get(k))[:210],
                    "handoff_reason":r['handoff_reason'] or ''}
            return out

    def assigned_leads(self):
        with self.connect() as c:
            return {r['lead_id']:r['worker_id'] for r in c.execute('SELECT lead_id,worker_id FROM ai_assignments')}

    def draft(self, lead_id, worker_id):
        with self.connect() as c:
            row=c.execute('SELECT body,updated_at FROM ai_intro_drafts WHERE lead_id=? AND worker_id=?',
                          (lead_id,worker_id)).fetchone()
            return dict(row) if row else None

    def save_draft(self, lead_id, worker_id, message):
        if not message.strip() or len(message)>1800:
            raise ValueError('Draft must be between 1 and 1800 characters')
        with self.connect(True) as c:
            assigned=c.execute('SELECT 1 FROM ai_assignments WHERE lead_id=? AND worker_id=?',
                               (lead_id,worker_id)).fetchone()
            if not assigned:raise ValueError('Lead is not assigned to this worker')
            c.execute('''INSERT INTO ai_intro_drafts(lead_id,worker_id,body,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(lead_id) DO UPDATE SET worker_id=excluded.worker_id,
                body=excluded.body,updated_at=excluded.updated_at''',
                (lead_id,worker_id,message,now()))

    def cached_research(self, url, max_age=86400):
        with self.connect() as c:
            row=c.execute('SELECT content FROM ai_research_cache WHERE url=? AND fetched_at>?',
                          (url,now()-max_age)).fetchone()
            return row['content'] if row else None

    def save_research(self, url, content):
        with self.connect(True) as c:
            c.execute('''INSERT INTO ai_research_cache(url,content,fetched_at) VALUES(?,?,?)
                ON CONFLICT(url) DO UPDATE SET content=excluded.content,fetched_at=excluded.fetched_at''',
                (url,str(content)[:5000],now()))

    def opted_out_phones(self):
        with self.connect() as c:
            rows=c.execute('''SELECT l.phone_e164,l.phone,l.country_code FROM leads l
                LEFT JOIN ai_permissions p ON p.lead_id=l.id
                WHERE l.do_not_contact=1 OR p.opted_out_at IS NOT NULL''').fetchall()
            return sorted({x for r in rows if (x:=normalize_phone(r['phone_e164'] or r['phone'],r['country_code']))})

    def permission(self, lead_id, consent, source='', proof=''):
        # Never reset an existing opt-out merely by bulk-marking consent.
        if consent and (not str(source).strip() or not str(proof).strip()):
            raise ValueError('Document the consent source and evidence')
        with self.connect(True) as c:
            lead = c.execute('SELECT do_not_contact FROM leads WHERE id=?',(lead_id,)).fetchone()
            if lead is None:
                raise ValueError('Lead not found')
            previous = c.execute('SELECT opted_out_at FROM ai_permissions WHERE lead_id=?',(lead_id,)).fetchone()
            if consent and (lead['do_not_contact'] or (previous and previous['opted_out_at'])):
                raise ValueError('This lead opted out. Do not reactivate without new verified permission.')
            c.execute('''INSERT INTO ai_permissions(lead_id,opted_in,source,proof,recorded_at) VALUES(?,?,?,?,?)
                 ON CONFLICT(lead_id) DO UPDATE SET opted_in=excluded.opted_in,source=excluded.source,
                 proof=excluded.proof,recorded_at=excluded.recorded_at''',
                      (lead_id,int(bool(consent)),str(source)[:150],str(proof)[:800],now()))

    @staticmethod
    def _lead_phone(row):
        """One canonical lead identity from both saved phone fields, without AI.

        Both representations may contain spacing or local/country prefixes.
        Conflicting *valid* numbers require owner review rather than guessing
        which phone belongs to the person who wrote to this WhatsApp worker.
        """
        region = row['country_code']
        phones = {number for field in ('phone_e164', 'phone')
                  if (number := normalize_phone(row[field], region))}
        return next(iter(phones)) if len(phones) == 1 else ''

    @staticmethod
    def _sender_phone(chat_id, phone_hint=''):
        # LID is not a phone number. Use only an explicitly verified @c.us hint.
        if str(chat_id).endswith('@c.us'):
            user = str(chat_id).split('@', 1)[0]
            if not re.fullmatch(r'\d{9,15}', user):
                return ''
            return normalize_phone('+' + user)
        if str(chat_id).endswith('@lid') and isinstance(phone_hint, str):
            if re.fullmatch(r'\+\d{9,15}', phone_hint):
                return normalize_phone(phone_hint)
        return ''

    def _match_lead(self, c, worker_id, chat, phone_hint=''):
        # An alias is authoritative only for the same worker and an assigned lead.
        alias = c.execute('''SELECT l.id,l.phone_e164,l.phone,l.country_code,l.do_not_contact
            FROM ai_chat_alias z JOIN ai_assignments a
            ON a.lead_id=z.lead_id AND a.worker_id=z.worker_id
            JOIN leads l ON l.id=z.lead_id
            WHERE z.worker_id=? AND z.chat_id=?''', (worker_id, chat)).fetchone()
        if alias:
            # A reassigned/edited phone must not keep an unrelated LID alias.
            verified=self._sender_phone(chat, phone_hint)
            if verified and self._lead_phone(alias)!=verified:
                return None
            return alias
        number = self._sender_phone(chat, phone_hint)
        if not number:
            return None
        rows = c.execute('''SELECT l.id,l.phone_e164,l.phone,l.country_code,l.do_not_contact
            FROM leads l JOIN ai_assignments a ON a.lead_id=l.id
            WHERE a.worker_id=?''', (worker_id,)).fetchall()
        matches = [r for r in rows if self._lead_phone(r) == number]
        if len(matches) != 1:
            # Duplicate normalized numbers must be resolved by the account owner.
            return None
        lead = matches[0]
        c.execute('''INSERT INTO ai_chat_alias(worker_id,chat_id,lead_id) VALUES(?,?,?)
            ON CONFLICT(worker_id,chat_id) DO UPDATE SET lead_id=excluded.lead_id''',
            (worker_id, chat, lead['id']))
        # Earlier messages for this LID may have arrived before WhatsApp
        # exposed a verified phone. Recover them now, without manual linking.
        waiting=c.execute('''SELECT * FROM ai_unmatched WHERE worker_id=? AND chat_id=?
            ORDER BY sent_at,event_id''',(worker_id,chat)).fetchall()
        if waiting:
            latest_in=None
            latest_out=None
            for item in waiting:
                outgoing=item['body'].startswith('[Outgoing] ')
                body=item['body'][11:] if outgoing else item['body']
                direction='out' if outgoing else 'in'
                c.execute('''INSERT OR IGNORE INTO ai_messages
                    (event_id,lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (item['event_id'],lead['id'],worker_id,chat,direction,body,
                     'sent' if outgoing else 'received',item['sent_at'],now()))
                if outgoing:latest_out=max(latest_out or 0,item['sent_at'])
                else:
                    latest_in=max(latest_in or 0,item['sent_at'])
                    if is_opt_out(body):
                        c.execute('''INSERT INTO ai_permissions(lead_id,opted_in,opted_out_at)
                            VALUES(?,0,?) ON CONFLICT(lead_id) DO UPDATE SET
                            opted_in=0,opted_out_at=excluded.opted_out_at''',(lead['id'],now()))
                        c.execute('UPDATE leads SET do_not_contact=1 WHERE id=?',(lead['id'],))
            c.execute('DELETE FROM ai_unmatched WHERE worker_id=? AND chat_id=?',(worker_id,chat))
            if latest_in is not None:
                c.execute('UPDATE ai_threads SET retry_after=0 WHERE lead_id=?',(lead['id'],))
                self._trace_in_tx(c,lead['id'],worker_id,'received',
                                  'WhatsApp identity verified; saved inbound recovered')
                c.execute('''INSERT INTO ai_threads(lead_id,last_inbound) VALUES(?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET last_inbound=MAX(COALESCE(last_inbound,0),excluded.last_inbound)''',
                    (lead['id'],latest_in))
            if latest_out is not None:
                c.execute('''INSERT INTO ai_threads(lead_id,last_outbound) VALUES(?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET last_outbound=MAX(COALESCE(last_outbound,0),excluded.last_outbound)''',
                    (lead['id'],latest_out))
        return lead

    @staticmethod
    def _trace_in_tx(c, lead_id, worker_id, stage, detail=''):
        # Never persist customer text or credential-bearing API responses.
        c.execute("""INSERT INTO ai_reply_trace(lead_id,worker_id,stage,detail,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET
            worker_id=excluded.worker_id,stage=excluded.stage,
            detail=excluded.detail,updated_at=excluded.updated_at""",
            (int(lead_id),str(worker_id),str(stage)[:40],str(detail)[:150],now()))

    def reply_trace(self, lead_id):
        with self.connect() as c:
            row=c.execute('SELECT stage,detail,updated_at FROM ai_reply_trace WHERE lead_id=?',(int(lead_id),)).fetchone()
            return dict(row) if row else None

    def set_reply_stage(self, lead_id, worker_id, stage, detail=''):
        with self.connect(True) as c:
            self._trace_in_tx(c,lead_id,worker_id,stage,detail)

    def retry_reply(self, thread, category, delay=90):
        """Persist bounded failure backoff without consuming the inbound turn.

        A new inbound message clears the cooldown immediately.
        """
        with self.connect(True) as c:
            current=c.execute("SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction='in'",
                              (thread['lead_id'],)).fetchone()[0]
            if current == thread['latest_inbound_id']:
                c.execute('UPDATE ai_threads SET retry_after=? WHERE lead_id=?',
                          (now()+max(20,min(600,int(delay))),thread['lead_id']))
                self._trace_in_tx(c,thread['lead_id'],thread['worker_id'],'retry_scheduled',category)

    def inbound(self, worker_id, event_id, chat_id, body, sent_at=None, sender_phone=''):
        # Persist incoming events; match verified phone IDs or keep unmatched.
        with self.connect(True) as c:
            if not c.execute('SELECT 1 FROM ai_workers WHERE id=?', (worker_id,)).fetchone():
                raise ValueError('Unknown worker')
            if (c.execute('SELECT 1 FROM ai_messages WHERE event_id=?', (event_id,)).fetchone()
                    or c.execute('SELECT 1 FROM ai_unmatched WHERE event_id=?', (event_id,)).fetchone()):
                return None
            lead = self._match_lead(c, worker_id, chat_id, sender_phone)
            if not lead:
                c.execute('INSERT INTO ai_unmatched VALUES(?,?,?,?,?)',
                          (event_id, worker_id, chat_id, body, now()))
                return None
            lead_id = lead['id']
            when = now()
            c.execute('''INSERT INTO ai_messages(event_id,lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at)
                VALUES(?,?,?,?,'in',?,'received',?,?)''',
                (event_id, lead_id, worker_id, chat_id, body, sent_at or when, when))
            active_profile=c.execute('SELECT instruction_profile_id FROM ai_workers WHERE id=?',(worker_id,)).fetchone()[0]
            c.execute('''INSERT INTO ai_threads(lead_id,last_inbound,instruction_profile_id) VALUES(?,?,?)
                ON CONFLICT(lead_id) DO UPDATE SET last_inbound=excluded.last_inbound,followups_sent=0,
                    instruction_profile_id=COALESCE(ai_threads.instruction_profile_id,excluded.instruction_profile_id)''',
                (lead_id, when, active_profile))
            c.execute('UPDATE ai_threads SET retry_after=0 WHERE lead_id=?',(lead_id,))
            self._trace_in_tx(c,lead_id,worker_id,'received','Incoming message matched to assigned lead')
            # Keep the one-off developer handoff confirmation until delivered.
            # A new opt-out, however, cancels even that pending confirmation.
            c.execute("""UPDATE ai_messages SET status='superseded' WHERE lead_id=?
                AND direction='out' AND status='queued_reply'
                AND (kind!='handoff' OR ?)""", (lead_id, int(is_opt_out(body))))
            c.execute("UPDATE ai_messages SET status='superseded' WHERE lead_id=? AND kind='intro' AND status='queued'", (lead_id,))
            c.execute("UPDATE ai_intro_jobs SET state='skipped' WHERE lead_id=? AND state='queued'", (lead_id,))
            if is_opt_out(body):
                c.execute('''INSERT INTO ai_permissions(lead_id,opted_in,opted_out_at) VALUES(?,0,?)
                    ON CONFLICT(lead_id) DO UPDATE SET opted_in=0,opted_out_at=excluded.opted_out_at''', (lead_id, when))
                c.execute('UPDATE leads SET do_not_contact=1 WHERE id=?', (lead_id,))
            return lead_id

    def assigned_phone_candidates(self, worker_id, max_candidates=50):
        # WhatsApp registration lookup is the sole basis for a LID mapping.
        with self.connect() as c:
            rows = c.execute('''SELECT l.phone_e164,l.phone,l.country_code
                FROM ai_assignments a JOIN leads l ON l.id=a.lead_id
                WHERE a.worker_id=? AND l.archived=0 ORDER BY a.assigned_at''',
                (worker_id,)).fetchall()
            phones = sorted({p for row in rows if (p := self._lead_phone(row))})
            # Do not silently test only a subset: that could hide a match.
            if len(phones)>max_candidates:
                return [], 'too_many_candidates'
            return phones, ''

    def unresolved_chat_ids(self, worker_id, max_chats=8):
        with self.connect() as c:
            return [r['chat_id'] for r in c.execute('''SELECT chat_id,MAX(sent_at) ts
                FROM ai_unmatched WHERE worker_id=? AND chat_id LIKE '%@lid'
                GROUP BY chat_id ORDER BY ts DESC LIMIT ?''', (worker_id,max_chats))]

    def resolve_verified_lid(self, worker_id, chat_id, verified_phone):
        # Reconcile only with a number that WhatsApp verified for this LID.
        if not re.fullmatch(r'\d{9,20}@lid',str(chat_id)):
            raise ValueError('Only opaque LID chat IDs require recovery')
        if not re.fullmatch(r'\+[1-9]\d{8,14}',str(verified_phone)):
            raise ValueError('An international verified phone is required')
        with self.connect(True) as c:
            lead=self._match_lead(c,worker_id,chat_id,verified_phone)
            if lead is None:return None
            return int(lead['id'])

    def unmatched(self,worker_id):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM ai_unmatched WHERE worker_id=? ORDER BY sent_at DESC LIMIT 100',(worker_id,))]

    def link_unmatched(self, worker_id, event_id, lead_id):
        # Import all saved chat messages after explicit owner assignment.
        with self.connect(True) as c:
            assigned = c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?', (lead_id,)).fetchone()
            if not assigned or assigned['worker_id'] != worker_id:
                raise ValueError('Assign this lead to this worker first')
            item = c.execute('SELECT * FROM ai_unmatched WHERE event_id=? AND worker_id=?',
                             (event_id, worker_id)).fetchone()
            if not item:
                raise ValueError('Message not found')
            waiting = c.execute('''SELECT * FROM ai_unmatched
                WHERE worker_id=? AND chat_id=? ORDER BY sent_at,event_id''',
                (worker_id, item['chat_id'])).fetchall()
            last_inbound = None
            last_outbound = None
            for message in waiting:
                outgoing = message['body'].startswith('[Outgoing] ')
                direction = 'out' if outgoing else 'in'
                body = message['body'][11:] if outgoing else message['body']
                timestamp = now()
                c.execute('''INSERT OR IGNORE INTO ai_messages
                    (event_id,lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (message['event_id'],lead_id,worker_id,message['chat_id'],direction,body,
                     'sent' if outgoing else 'received',message['sent_at'],timestamp))
                if outgoing:
                    last_outbound = timestamp
                else:
                    last_inbound = timestamp
                    if is_opt_out(body):
                        c.execute('''INSERT INTO ai_permissions(lead_id,opted_in,opted_out_at)
                            VALUES(?,0,?) ON CONFLICT(lead_id) DO UPDATE SET
                            opted_in=0,opted_out_at=excluded.opted_out_at''',(lead_id,timestamp))
                        c.execute('UPDATE leads SET do_not_contact=1 WHERE id=?',(lead_id,))
            c.execute('''INSERT INTO ai_chat_alias(worker_id,chat_id,lead_id) VALUES(?,?,?)
                ON CONFLICT(worker_id,chat_id) DO UPDATE SET lead_id=excluded.lead_id''',
                (worker_id, item['chat_id'], lead_id))
            c.execute('DELETE FROM ai_unmatched WHERE worker_id=? AND chat_id=?',
                      (worker_id, item['chat_id']))
            if last_inbound is not None:
                active_profile=c.execute('SELECT instruction_profile_id FROM ai_workers WHERE id=?',(worker_id,)).fetchone()[0]
                c.execute('''INSERT INTO ai_threads(lead_id,last_inbound,instruction_profile_id) VALUES(?,?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET last_inbound=excluded.last_inbound,
                      instruction_profile_id=COALESCE(ai_threads.instruction_profile_id,excluded.instruction_profile_id)''',
                    (lead_id,last_inbound,active_profile))
                c.execute("UPDATE ai_messages SET status='superseded' WHERE lead_id=? AND direction='out' AND status='queued_reply'",(lead_id,))
            if last_outbound is not None:
                c.execute('''INSERT INTO ai_threads(lead_id,last_outbound) VALUES(?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET last_outbound=excluded.last_outbound''',
                    (lead_id,last_outbound))
            return len(waiting)

    def lead_message_summaries(self, ids):
        """One DB query for message counts and the most recent preview per lead."""
        ids = sorted({int(value) for value in ids})
        if not ids:
            return {}
        placeholders = ','.join('?' for _ in ids)
        with self.connect() as c:
            rows = c.execute(f"""SELECT m.lead_id, COUNT(*) AS count,
                   SUBSTR((SELECT CASE
                     WHEN recent.direction='in' OR recent.status='sent' THEN recent.body
                     WHEN recent.status IN ('queued','queued_reply','sending') THEN '[QUEUED - not sent] '||recent.body
                     WHEN recent.status='uncertain' THEN '[UNCONFIRMED - not verified] '||recent.body
                     ELSE '[NOT SENT: '||recent.status||'] '||recent.body END
                     FROM ai_messages recent WHERE recent.lead_id=m.lead_id
                     ORDER BY recent.id DESC LIMIT 1),1,110) AS preview
                FROM ai_messages m WHERE m.lead_id IN ({placeholders}) GROUP BY m.lead_id""", ids)
            return {int(r['lead_id']): dict(r) for r in rows}

    def messages(self,lead_id,limit=200):
        with self.connect() as c:
            return [dict(r) for r in reversed(c.execute('SELECT * FROM ai_messages WHERE lead_id=? ORDER BY id DESC LIMIT ?',(lead_id,limit)).fetchall())]

    def usage(self,worker_id,limit=50):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM ai_usage WHERE worker_id=? ORDER BY id DESC LIMIT ?',(worker_id,limit))]

    def log_usage(self,worker_id,key_id,model,purpose,input_tokens=0,output_tokens=0,error='',lead_id=None):
        with self.connect(True) as c:
            c.execute('''INSERT INTO ai_usage(worker_id,key_id,model,purpose,input_tokens,output_tokens,error,recorded_at,lead_id)
                VALUES(?,?,?,?,?,?,?,?,?)''',(worker_id,key_id,model,purpose,input_tokens,output_tokens,str(error)[:300],now(),lead_id))

    def due(self):
        """Lease one eligible reply without starving other leads at a quota boundary.

        A worker-wide daily request limit is independent of its new-contact
        and token limits. When exhausted, no Gemini calls are attempted; each
        pending lead instead receives an actionable trace explaining the block.
        """
        with self.connect(True) as c:
            t=now()
            rows=c.execute('''SELECT t.*, a.worker_id,w.batching_seconds,w.model_name,w.model_key_id,
                  w.followup_enabled,w.followup_hours,l.name,l.niche,l.city,l.website_url,
                  l.do_not_contact,p.opted_in,p.opted_out_at
                FROM ai_threads t JOIN ai_assignments a ON a.lead_id=t.lead_id
                JOIN ai_workers w ON w.id=a.worker_id AND w.enabled=1
                JOIN leads l ON l.id=t.lead_id AND l.archived=0
                LEFT JOIN ai_permissions p ON p.lead_id=t.lead_id
                WHERE t.last_inbound IS NOT NULL AND
                  t.instruction_profile_id=w.instruction_profile_id AND
                  COALESCE(t.retry_after,0)<=?
                  AND (t.in_progress=0 OR t.lease_at<?)
                  AND t.last_inbound<=?
                  AND t.last_inbound>?
                  AND l.do_not_contact=0
                  AND COALESCE(p.opted_out_at,0)=0
                  AND NOT EXISTS (SELECT 1 FROM ai_lead_control z WHERE z.lead_id=t.lead_id AND (z.paused=1 OR z.handoff=1))
                  ORDER BY t.last_inbound LIMIT 1000''', (t,t-180,t-2,t-86400)).fetchall()
            quota_cache={}
            for row in rows:
                latest=c.execute("SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction='in'",(row['lead_id'],)).fetchone()[0] or 0
                if latest <= row['replied_through'] or t-row['last_inbound'] < row['batching_seconds']:
                    continue
                worker_id=row['worker_id']
                if worker_id not in quota_cache:
                    quota_limit=int(c.execute('SELECT daily_ai_limit FROM ai_account_settings WHERE id=1').fetchone()[0])
                    used=int(c.execute('''SELECT COUNT(*) FROM ai_usage WHERE worker_id=?
                        AND purpose IN ('reply','memory','intro','intro_draft') AND recorded_at>=?''',
                        (worker_id,t-86400)).fetchone()[0])
                    quota_cache[worker_id]=(used,quota_limit)
                used,limit=quota_cache[worker_id]
                if used >= limit:
                    previous=c.execute('SELECT stage FROM ai_reply_trace WHERE lead_id=?',(row['lead_id'],)).fetchone()
                    if not previous or previous['stage']!='daily_limit':
                        self._trace_in_tx(c,row['lead_id'],worker_id,'daily_limit',
                            f'Daily AI generation request limit reached ({used}/{limit}; rolling 24 hours). Raise the shared generation limit or wait for requests to expire.')
                    continue
                if not row['model_name']:
                    previous=c.execute('SELECT stage FROM ai_reply_trace WHERE lead_id=?',(row['lead_id'],)).fetchone()
                    if not previous or previous['stage']!='model_unavailable':
                        self._trace_in_tx(c,row['lead_id'],worker_id,'model_unavailable','No Gemini model selected')
                    continue
                if not self.token_budget_available(worker_id):
                    previous=c.execute('SELECT stage FROM ai_reply_trace WHERE lead_id=?',(row['lead_id'],)).fetchone()
                    if not previous or previous['stage']!='token_limit':
                        self._trace_in_tx(c,row['lead_id'],worker_id,'token_limit','Worker daily token budget exhausted')
                    continue
                if not self._acquire_generation_in_tx(c,worker_id,t):
                    continue
                c.execute('UPDATE ai_threads SET in_progress=1,lease_at=? WHERE lead_id=?',(t,row['lead_id']))
                self._trace_in_tx(c,row['lead_id'],worker_id,'generating','Gemini decision in progress')
                return {**dict(row),'latest_inbound_id':latest}
        return None

    def release(self,lead_id):
        with self.connect(True) as c:
            c.execute('UPDATE ai_threads SET in_progress=0,lease_at=NULL WHERE lead_id=?',(lead_id,))

    @staticmethod
    def _safe_qualification(facts):
        """Accept concise user-supplied requirements, never inferred commitments."""
        if not isinstance(facts,dict):return {}
        accepted={}
        for key in ('service','requirements','budget','deadline','other'):
            value=facts.get(key)
            if isinstance(value,str) and value.strip():
                accepted[key]=value.strip()[:180]
        return accepted

    def _record_qualification(self,c,lead_id,facts,question=False):
        if not facts and not question:return
        row=c.execute('SELECT qualification_facts FROM ai_threads WHERE lead_id=?',(lead_id,)).fetchone()
        if not row:return
        try: known=json.loads(row['qualification_facts'] or '{}')
        except (ValueError,TypeError): known={}
        known.update(self._safe_qualification(facts))
        c.execute('''UPDATE ai_threads SET qualification_facts=?,
            qualification_questions=qualification_questions+? WHERE lead_id=?''',
            (json.dumps(known,ensure_ascii=False),int(bool(question)),lead_id))

    def complete_qualification(self, thread, final_message, facts=None, reason='qualified', media_id=None):
        """Atomically queue ONE final answer, flag developer follow-up and stop the AI.

        Completing a lead must not suppress its own final confirmation. The
        sending queue treats only kind=handoff as an allowed final reply.
        """
        lead_id=int(thread['lead_id'])
        with self.connect(True) as c:
            latest=c.execute("SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction='in'",(lead_id,)).fetchone()[0]
            owner=c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?',(lead_id,)).fetchone()
            blocked=c.execute('SELECT 1 FROM ai_lead_control WHERE lead_id=? AND (paused=1 OR handoff=1)',(lead_id,)).fetchone()
            valid_profile=c.execute('''SELECT 1 FROM ai_threads t JOIN ai_workers w ON w.id=?
                WHERE t.lead_id=? AND t.instruction_profile_id=w.instruction_profile_id''',
                (thread['worker_id'],lead_id)).fetchone()
            if not owner or owner['worker_id']!=thread['worker_id'] or latest!=thread['latest_inbound_id'] or blocked or not valid_profile:
                return None
            recent=c.execute("SELECT chat_id FROM ai_messages WHERE lead_id=? AND direction='in' ORDER BY id DESC LIMIT 1",(lead_id,)).fetchone()
            if not recent:return None
            self._record_qualification(c,lead_id,facts or {})
            # Prevent all pending introductions, follow-ups and stale replies.
            c.execute('''UPDATE ai_messages SET status='blocked' WHERE lead_id=? AND direction='out'
                  AND status IN ('queued','queued_reply')''',(lead_id,))
            c.execute('''INSERT INTO ai_messages(lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at,kind,media_id)
                VALUES(?,?,?,'out',?,'queued_reply',?,?,'handoff',?)''',
                (lead_id,thread['worker_id'],recent['chat_id'],final_message,now(),now(),media_id))
            outgoing=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('''UPDATE ai_threads SET replied_through=?,followups_sent=1,retry_after=0 WHERE lead_id=?''',(latest,lead_id))
            self._trace_in_tx(c,lead_id,thread['worker_id'],'queued','Final developer handoff message queued')
            c.execute('''INSERT INTO ai_lead_control(lead_id,paused,handoff,changed_at,handoff_reason,handoff_at)
                VALUES(?,0,1,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET
                handoff=1,changed_at=excluded.changed_at,
                handoff_reason=excluded.handoff_reason,handoff_at=excluded.handoff_at''',
                (lead_id,now(),str(reason)[:60],now()))
            # Preserve Quoted/Won and other advanced/manual statuses.
            c.execute('''UPDATE leads SET crm_status=CASE WHEN crm_status IN ('New','Contacted')
                  THEN 'Interested' ELSE crm_status END, contacted=1, next_followup=NULL
                  WHERE id=?''',(lead_id,))
            # Requirements are kept in the thread; the Leads table and Chat
            # popup can show them without bloating the CRM notes.
            return outgoing

    def complete_existing_conversation(self, lead_id):
        # Owner-confirmed handoff for older chats that already promised one.
        # Local CRM only: queues no messages, and does not alter permissions.
        lead_id=int(lead_id)
        with self.connect(True) as c:
            owner=c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?',(lead_id,)).fetchone()
            if not owner:
                raise ValueError('Assign a worker before completing this lead')
            last=c.execute('SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction="in"',(lead_id,)).fetchone()[0]
            if not last:
                raise ValueError('No customer conversation to complete')
            t=now()
            c.execute('''INSERT INTO ai_lead_control(lead_id,paused,handoff,changed_at,handoff_reason,handoff_at)
                VALUES(?,0,1,?,'qualified',?) ON CONFLICT(lead_id) DO UPDATE SET
                handoff=1,changed_at=excluded.changed_at,
                handoff_reason='qualified',handoff_at=excluded.handoff_at''',(lead_id,t,t))
            c.execute('''UPDATE ai_messages SET status='blocked' WHERE lead_id=? AND direction='out'
                AND status IN ('queued','queued_reply')''',(lead_id,))
            c.execute('''UPDATE ai_threads SET replied_through=?,followups_sent=1 WHERE lead_id=?''',(last,lead_id))
            c.execute('''UPDATE leads SET crm_status=CASE WHEN crm_status IN ('New','Contacted')
                THEN 'Interested' ELSE crm_status END, contacted=1,next_followup=NULL WHERE id=?''',(lead_id,))
            return True

    def commit_reply(self,thread,body,facts=None,media_id=None):
        """Only queue a response if no new inbound arrived mid-generation."""
        with self.connect(True) as c:
            newest=c.execute("SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction='in'",(thread['lead_id'],)).fetchone()[0]
            current=c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?',(thread['lead_id'],)).fetchone()
            profile_ok=c.execute('''SELECT 1 FROM ai_threads t JOIN ai_workers w ON w.id=?
                WHERE t.lead_id=? AND t.instruction_profile_id=w.instruction_profile_id''',
                (thread['worker_id'],thread['lead_id'])).fetchone()
            if (newest!=thread['latest_inbound_id'] or not current or current['worker_id']!=thread['worker_id']
                or not profile_ok
                or c.execute('SELECT 1 FROM ai_lead_control WHERE lead_id=? AND (paused=1 OR handoff=1)',(thread['lead_id'],)).fetchone()):
                return None
            latest=c.execute('SELECT chat_id FROM ai_messages WHERE lead_id=? AND direction="in" ORDER BY id DESC LIMIT 1',(thread['lead_id'],)).fetchone()
            if not latest:
                return None
            c.execute('''INSERT INTO ai_messages(lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at,media_id)
                 VALUES(?,?,?,'out',?,'queued_reply',?,?,?)''',(thread['lead_id'],thread['worker_id'],latest['chat_id'],body,now(),now(),media_id))
            identifier=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('UPDATE ai_threads SET replied_through=?,retry_after=0 WHERE lead_id=?',(newest,thread['lead_id']))
            self._trace_in_tx(c,thread['lead_id'],thread['worker_id'],'queued','Reply generated; waiting for WhatsApp send')
            self._record_qualification(c,thread['lead_id'],facts or {}, '?' in body or '؟' in body)
            return identifier

    def acknowledge_inbound(self, thread):
        """Record an intentionally unanswered inbound turn to avoid repeated AI calls.

        Do not acknowledge if another user message arrived during generation.
        """
        with self.connect(True) as c:
            latest = c.execute("SELECT MAX(id) FROM ai_messages WHERE lead_id=? AND direction='in'", (thread['lead_id'],)).fetchone()[0]
            assigned = c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?', (thread['lead_id'],)).fetchone()
            profile_ok=c.execute('''SELECT 1 FROM ai_threads t JOIN ai_workers w ON w.id=?
                WHERE t.lead_id=? AND t.instruction_profile_id=w.instruction_profile_id''',
                (thread['worker_id'],thread['lead_id'])).fetchone()
            if not assigned or assigned['worker_id'] != thread['worker_id'] or latest != thread['latest_inbound_id'] or not profile_ok:
                return False
            c.execute('UPDATE ai_threads SET replied_through=?,retry_after=0 WHERE lead_id=?', (latest, thread['lead_id']))
            self._trace_in_tx(c,thread['lead_id'],thread['worker_id'],'ignored',
                              'Gemini chose not to reply to this turn')
            return True

    def pending_outbound(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('''SELECT m.*,a.worker_id assigned_worker,l.do_not_contact,
                p.opted_in,p.opted_out_at,t.last_inbound,w.enabled worker_enabled,w.daily_new_contacts FROM ai_messages m
                JOIN ai_assignments a ON a.lead_id=m.lead_id JOIN leads l ON l.id=m.lead_id
                LEFT JOIN ai_permissions p ON p.lead_id=m.lead_id
                LEFT JOIN ai_threads t ON t.lead_id=m.lead_id
                JOIN ai_workers w ON w.id=a.worker_id
                WHERE m.direction='out' AND m.status IN ('queued','queued_reply')
                  AND (m.status='queued' OR m.kind='handoff' OR t.instruction_profile_id=w.instruction_profile_id)
                ORDER BY m.id LIMIT 50''')]

    def status(self,message_id,status,event_id=None):
        if status not in ('sent','failed','blocked','uncertain'):
            raise ValueError('Invalid message status')
        with self.connect(True) as c:
            # Store a *real* WhatsApp message ID even when the server ACK is
            # still missing, allowing a later ACK to reconcile this exact
            # attempt. Never guess an ID or overwrite an existing event ID.
            real_id=(str(event_id) if isinstance(event_id,str) and
                     1 <= len(event_id) <= 150 and not event_id.startswith('fallback-')
                     else None)
            if real_id and c.execute('SELECT 1 FROM ai_messages WHERE event_id=? AND id<>?',
                                      (real_id,message_id)).fetchone():
                real_id=None
            c.execute('''UPDATE ai_messages SET status=?,event_id=COALESCE(event_id,?)
                WHERE id=? AND direction='out' AND status IN ('queued','queued_reply','sending')''',
                (status,real_id,message_id))
            record=c.execute("SELECT lead_id,worker_id,status FROM ai_messages WHERE id=? AND direction='out'",(message_id,)).fetchone()
            if record and record['lead_id'] is not None:
                latest=c.execute("SELECT kind FROM ai_messages WHERE id=?",(message_id,)).fetchone()
                actual_status=record['status']
                if (latest and latest['kind'] == 'handoff') or actual_status in ('sent','uncertain','failed','blocked'):
                    stage={'sent':'sent','uncertain':'unconfirmed','failed':'send_failed','blocked':'send_blocked'}[actual_status]
                    has_media=bool(c.execute('SELECT media_id FROM ai_messages WHERE id=?',(message_id,)).fetchone()[0])
                    self._trace_in_tx(c,record['lead_id'],record['worker_id'],stage,
                                      ('Media attachment' if has_media else 'Text message')+' WhatsApp send result: '+actual_status)
            if record and record['status']=='sent':
                c.execute('UPDATE ai_threads SET last_outbound=? WHERE lead_id=(SELECT lead_id FROM ai_messages WHERE id=?)',(now(),message_id))

    def chat_for_lead(self,lead_id):
        with self.connect() as c:
            row=c.execute('SELECT chat_id FROM ai_messages WHERE lead_id=? ORDER BY id DESC LIMIT 1',(lead_id,)).fetchone()
            if row:return row['chat_id']
            row=c.execute('SELECT phone_e164,phone,country_code FROM leads WHERE id=?',(lead_id,)).fetchone()
            number=digits(normalize_phone(row['phone_e164'] or row['phone'],row['country_code'])) if row else ''
            return number+'@c.us' if len(number)>=9 else ''

    def can_message(self,lead_id,require_optin=True):
        with self.connect() as c:
            row=c.execute('''SELECT l.do_not_contact,l.archived,p.opted_in,p.opted_out_at
                    FROM leads l LEFT JOIN ai_permissions p ON l.id=p.lead_id WHERE l.id=?''',(lead_id,)).fetchone()
            return bool(row and not row['do_not_contact'] and not row['archived'] and not row['opted_out_at'] and (not require_optin or row['opted_in']))

    def manual_outbound(self,worker_id,lead_id,body):
        if not body.strip() or len(body)>3000:
            raise ValueError('Message must contain 1–3000 characters')
        with self.connect(True) as c:
            assigned=c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?',(lead_id,)).fetchone()
            if not assigned or assigned['worker_id']!=worker_id:
                raise ValueError('This lead is not assigned to this worker')
            if not self._can_message_in_tx(c,lead_id,True):
                raise ValueError('Verified consent is needed before outbound initiation')
            last=c.execute('SELECT chat_id FROM ai_messages WHERE lead_id=? ORDER BY id DESC LIMIT 1',(lead_id,)).fetchone()
            lead=c.execute('SELECT phone_e164,phone,country_code FROM leads WHERE id=?',(lead_id,)).fetchone()
            chat=last['chat_id'] if last else digits(normalize_phone(lead['phone_e164'] or lead['phone'],lead['country_code']))+'@c.us'
            if not chat.endswith('@c.us') or len(digits(chat))<9:
                raise ValueError('No supported phone/chat for this lead')
            c.execute('''INSERT INTO ai_messages(lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at)
                VALUES(?,?,?,'out',?,'queued',?,?)''',(lead_id,worker_id,chat,body,now(),now()))
            return c.execute('SELECT last_insert_rowid()').fetchone()[0]

    @staticmethod
    def _can_message_in_tx(c,lead_id,require_optin):
        row=c.execute('''SELECT l.do_not_contact,l.archived,p.opted_in,p.opted_out_at
                FROM leads l LEFT JOIN ai_permissions p ON p.lead_id=l.id WHERE l.id=?''',(lead_id,)).fetchone()
        return bool(row and not row['do_not_contact'] and not row['archived'] and not row['opted_out_at'] and (not require_optin or row['opted_in']))

    def generation_quota(self, worker_id):
        """Rolling-24-hour AI request counter, including provider failures.

        This intentionally measures attempts, not tokens. Failed calls still
        count so a misconfigured API key cannot generate unbounded traffic.
        """
        cutoff=now()-86400
        with self.connect() as c:
            limit=int(c.execute('SELECT daily_ai_limit FROM ai_account_settings WHERE id=1').fetchone()[0])
            row=c.execute('''SELECT COUNT(*) AS used, MIN(recorded_at) AS oldest
                FROM ai_usage WHERE worker_id=?
                  AND purpose IN ('reply','memory','intro','intro_draft')
                  AND recorded_at>=?''',(worker_id,cutoff)).fetchone()
            used=int(row['used'])
            return {'used':used,'limit':limit,'remaining':max(0,limit-used),
                    'next_expiry':float(row['oldest'])+86400 if row['oldest'] is not None else None}

    def daily_used(self,worker_id):
        return self.generation_quota(worker_id)['used']

    def context(self,lead_id):
        with self.connect() as c:
            thread=c.execute('SELECT * FROM ai_threads WHERE lead_id=?',(lead_id,)).fetchone()
            messages=[dict(x) for x in c.execute('SELECT * FROM (SELECT id,direction,body,status FROM ai_messages WHERE lead_id=? ORDER BY id DESC LIMIT 12) ORDER BY id',(lead_id,))]
            return dict(thread) if thread else {},messages

    def write_memory(self,lead_id,summary,facts,through):
        with self.connect(True) as c:
            c.execute('UPDATE ai_threads SET summary=?,facts=?,summarized_through=? WHERE lead_id=? AND summarized_through<?',
                      (summary,json.dumps(facts,ensure_ascii=False),through,lead_id,through))

    def older_messages(self,lead_id,after_id,before_id):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,direction,body FROM ai_messages WHERE lead_id=? AND id>? AND id<? ORDER BY id LIMIT 100',
                (lead_id,after_id,before_id))]

    def lead_label(self,lead_id):
        with self.connect() as c:
            r=c.execute('SELECT id,name,niche,city,country_code,website_url,notes,crm_status FROM leads WHERE id=?',(lead_id,)).fetchone()
            return dict(r) if r else {}

    def outgoing_sync(self,worker_id,event_id,chat_id,body,sent_at=None,sender_phone=''):
        """Capture manual WhatsApp sends without duplicating app-sent messages."""
        with self.connect(True) as c:
            existing=c.execute('SELECT id,lead_id,status,worker_id,direction FROM ai_messages WHERE event_id=?',
                                (event_id,)).fetchone()
            if existing:
                if existing['worker_id']==worker_id and existing['direction']=='out' and existing['status']=='uncertain':
                    c.execute('UPDATE ai_messages SET status="sent" WHERE id=?',(existing['id'],))
                    if existing['lead_id'] is not None:
                        c.execute('UPDATE ai_threads SET last_outbound=? WHERE lead_id=?',(now(),existing['lead_id']))
                    return existing['lead_id']
                return None
            if c.execute('SELECT 1 FROM ai_unmatched WHERE event_id=?',(event_id,)).fetchone():
                return None
            lead=self._match_lead(c,worker_id,chat_id,sender_phone)
            if not lead:
                c.execute('INSERT INTO ai_unmatched VALUES(?,?,?,?,?)',(event_id,worker_id,chat_id,'[Outgoing] '+body,now()))
                return None
            lead_id=lead['id']
            matched=c.execute("""SELECT id FROM ai_messages WHERE lead_id=? AND worker_id=? AND
                      direction='out' AND chat_id=? AND body=? AND created_at>? AND
                      status IN ('queued','queued_reply','sending','uncertain','sent')
                      ORDER BY id DESC LIMIT 1""",
                      (lead_id,worker_id,chat_id,body,now()-120)).fetchone()
            if matched:
                c.execute('UPDATE ai_messages SET status="sent",event_id=COALESCE(event_id,?) WHERE id=?',
                          (event_id,matched['id']))
                c.execute('''INSERT INTO ai_threads(lead_id,last_outbound) VALUES(?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET last_outbound=excluded.last_outbound''',(lead_id,now()))
                return lead_id
            when=now()
            c.execute("""INSERT INTO ai_messages(event_id,lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at)
                VALUES(?,?,?,?,'out',?,'sent',?,?)""",(event_id,lead_id,worker_id,chat_id,body,sent_at or when,when))
            c.execute("""INSERT INTO ai_threads(lead_id,last_outbound) VALUES(?,?)
                ON CONFLICT(lead_id) DO UPDATE SET last_outbound=excluded.last_outbound""",(lead_id,when))
            return lead_id

    def opt_out(self,lead_id):
        with self.connect(True) as c:
            timestamp=now()
            c.execute('UPDATE leads SET do_not_contact=1 WHERE id=?',(lead_id,))
            c.execute("""INSERT INTO ai_permissions(lead_id,opted_in,opted_out_at) VALUES(?,0,?)
               ON CONFLICT(lead_id) DO UPDATE SET opted_in=0,opted_out_at=excluded.opted_out_at""",(lead_id,timestamp))
            c.execute("UPDATE ai_messages SET status='blocked' WHERE lead_id=? AND direction='out' AND status='queued'",(lead_id,))

    @staticmethod
    def _acquire_generation_in_tx(c,worker_id,t):
        c.execute('INSERT OR IGNORE INTO ai_generation_leases(worker_id,expires_at) VALUES(?,0)',(worker_id,))
        return bool(c.execute('UPDATE ai_generation_leases SET expires_at=? WHERE worker_id=? AND expires_at<?',
                     (t+180,worker_id,t)).rowcount)

    def release_generation(self,worker_id):
        with self.connect(True) as c:
            c.execute('UPDATE ai_generation_leases SET expires_at=0 WHERE worker_id=?',(worker_id,))

    def contact_phone(self,lead_id):
        with self.connect() as c:
            lead=c.execute('SELECT phone_e164,phone,country_code FROM leads WHERE id=?',(lead_id,)).fetchone()
            return normalize_phone(lead['phone_e164'] or lead['phone'],lead['country_code']) if lead else ''

    def historical_outbound(self):
        """Previously sent messages are backfilled to the installation-wide ledger."""
        with self.connect() as c:
            rows=c.execute("""SELECT DISTINCT m.lead_id,m.worker_id,l.phone_e164,l.phone,l.country_code
                FROM ai_messages m JOIN leads l ON l.id=m.lead_id
                WHERE m.direction='out' AND m.status IN ('sent','uncertain')""").fetchall()
            return [(normalize_phone(r['phone_e164'] or r['phone'],r['country_code']),r['worker_id'],r['lead_id'])
                    for r in rows]

    def intro_due(self, ledger=None, ready_worker_ids=None):
        # Assignment alone never authorizes messaging; only documented opt-in qualifies.
        t=now()
        with self.connect(True) as c:
            candidates=c.execute('''SELECT a.lead_id,a.worker_id,l.name,l.niche,l.city,l.phone,l.phone_e164,
                l.country_code,w.model_key_id,w.model_name,w.daily_new_contacts
                FROM ai_assignments a JOIN leads l ON l.id=a.lead_id
                JOIN ai_workers w ON w.id=a.worker_id AND w.enabled=1 AND w.outreach_enabled=1
                JOIN ai_permissions p ON p.lead_id=l.id AND p.opted_in=1 AND p.opted_out_at IS NULL
                LEFT JOIN ai_intro_jobs j ON j.lead_id=l.id
                WHERE l.archived=0 AND l.do_not_contact=0
                AND w.model_key_id IS NOT NULL AND w.model_name IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM ai_messages m WHERE m.lead_id=l.id)
                AND NOT EXISTS (SELECT 1 FROM message_queue_items q WHERE q.lead_id=l.id)
                AND NOT EXISTS (SELECT 1 FROM ai_lead_control z WHERE z.lead_id=l.id AND (z.paused=1 OR z.handoff=1))
                AND (j.lead_id IS NULL OR (j.state='failed' AND j.attempts<3 AND j.updated_at<?)
                      OR (j.state='generating' AND j.updated_at<?))
                ORDER BY a.assigned_at,a.lead_id LIMIT 40''',(t-1800,t-600)).fetchall()
            ai_limit=c.execute('SELECT daily_ai_limit FROM ai_account_settings WHERE id=1').fetchone()['daily_ai_limit']
            for r in candidates:
                if ready_worker_ids is not None and r['worker_id'] not in ready_worker_ids:
                    continue
                ai_calls=c.execute('''SELECT COUNT(*) FROM ai_usage WHERE worker_id=?
                    AND purpose IN ('reply','memory','intro','intro_draft') AND recorded_at>=?''',
                    (r['worker_id'],t-86400)).fetchone()[0]
                if ai_calls>=ai_limit or not self.token_budget_available(r['worker_id']):continue
                phone=normalize_phone(r['phone_e164'] or r['phone'],r['country_code'])
                if not phone or (ledger and ledger.is_taken(phone)):
                    continue
                if self._daily_first_in_tx(c,r['worker_id'],t)>=r['daily_new_contacts']:
                    continue
                if not self._acquire_generation_in_tx(c,r['worker_id'],t):
                    continue
                c.execute('''INSERT INTO ai_intro_jobs(lead_id,worker_id,state,attempts,updated_at)
                    VALUES(?,?,'generating',1,?)
                    ON CONFLICT(lead_id) DO UPDATE SET worker_id=excluded.worker_id,state='generating',
                    attempts=ai_intro_jobs.attempts+1,updated_at=excluded.updated_at''',
                    (r['lead_id'],r['worker_id'],t))
                return {**dict(r),'canonical_phone':phone}
        return None

    def commit_intro(self,task,body):
        # Commit only if consent, assignment, and model eligibility still hold.
        if not body or len(body)>1800:
            raise ValueError('Invalid intro message')
        with self.connect(True) as c:
            lead_id,worker_id=task['lead_id'],task['worker_id']
            assigned=c.execute('SELECT worker_id FROM ai_assignments WHERE lead_id=?',(lead_id,)).fetchone()
            job=c.execute('SELECT state FROM ai_intro_jobs WHERE lead_id=? AND worker_id=?',(lead_id,worker_id)).fetchone()
            if not assigned or assigned['worker_id']!=worker_id or not job or job['state']!='generating':
                return None
            if not self._can_message_in_tx(c,lead_id,True):
                return None
            if c.execute('SELECT 1 FROM ai_messages WHERE lead_id=? LIMIT 1',(lead_id,)).fetchone():
                return None
            lead=c.execute('SELECT phone,phone_e164,country_code FROM leads WHERE id=?',(lead_id,)).fetchone()
            phone=normalize_phone(lead['phone_e164'] or lead['phone'],lead['country_code'])
            if not phone or phone!=task['canonical_phone']:
                return None
            stamp=now()
            c.execute('''INSERT INTO ai_messages(lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at,kind)
                VALUES(?,?,?,'out',?,'queued',?,?,'intro')''',
                (lead_id,worker_id,phone[1:]+'@c.us',body,stamp,stamp))
            c.execute("UPDATE ai_intro_jobs SET state='queued',updated_at=? WHERE lead_id=?",(stamp,lead_id))
            return c.execute('SELECT last_insert_rowid()').fetchone()[0]

    def fail_intro(self,lead_id):
        with self.connect(True) as c:
            c.execute("UPDATE ai_intro_jobs SET state='failed',updated_at=? WHERE lead_id=? AND state='generating'",(now(),lead_id))

    @staticmethod
    def _daily_first_in_tx(c,worker_id,t):
        # UTC midnight so a cap doesn't reset multiple times in a rolling 24h window.
        day_start=int(t//86400)*86400
        return c.execute('SELECT COUNT(*) FROM ai_first_contact_attempts WHERE worker_id=? AND attempted_at>=?',
            (worker_id,day_start)).fetchone()[0]

    def daily_new_contact_used(self,worker_id):
        with self.connect() as c:
            return self._daily_first_in_tx(c,worker_id,now())

    def mark_first_contact_attempt(self,worker_id,lead_id,message_id,limit):
        with self.connect(True) as c:
            row=c.execute('SELECT 1 FROM ai_first_contact_attempts WHERE message_id=?',(message_id,)).fetchone()
            if row: return True
            t=now()
            if self._daily_first_in_tx(c,worker_id,t)>=int(limit):
                return False
            c.execute('INSERT INTO ai_first_contact_attempts VALUES(?,?,?,?)',(message_id,worker_id,lead_id,t))
            return True

    def release_first_contact_attempt(self,message_id):
        with self.connect(True) as c:
            c.execute('DELETE FROM ai_first_contact_attempts WHERE message_id=?',(message_id,))

    def is_first_contact(self,message_id,lead_id):
        with self.connect() as c:
            return not bool(c.execute("SELECT 1 FROM ai_messages WHERE lead_id=? AND direction='out' AND status IN ('sent','uncertain') AND id<>? LIMIT 1",(lead_id,message_id)).fetchone())

    def begin_sending(self,message_id):
        with self.connect(True) as c:
            return bool(c.execute("UPDATE ai_messages SET status='sending' WHERE id=? AND direction='out' AND status IN ('queued','queued_reply')",(message_id,)).rowcount)

    def requeue_sending(self,message_id,original_status='queued'):
        if original_status not in ('queued','queued_reply'):
            raise ValueError('Invalid queued message state')
        with self.connect(True) as c:
            c.execute('UPDATE ai_messages SET status=? WHERE id=? AND status="sending"',
                      (original_status,message_id))

    def acquire_send(self,worker_id,delay_seconds=6):
        """Worker-level pacing; avoids fast bursts from one WhatsApp number."""
        with self.connect(True) as c:
            c.execute('INSERT OR IGNORE INTO ai_send_tracker(worker_id,last_attempt) VALUES(?,0)',(worker_id,))
            stamp=now()
            changed=c.execute('''UPDATE ai_send_tracker SET last_attempt=?
                WHERE worker_id=? AND last_attempt <= ?''',(stamp,worker_id,stamp-delay_seconds)).rowcount
            return bool(changed)
