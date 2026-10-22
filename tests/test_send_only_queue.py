"""Offline queue-retention regression tests; never sends actual messages."""
from contextlib import contextmanager
from pathlib import Path
import importlib.util
import sqlite3
import sys
import tempfile
import time
import types
import unittest

# The queue's persistence rules can be tested without installing Streamlit,
# phonenumbers or accessing any external WhatsApp/Gemini service.
def load_queue_module():
    pkg=types.ModuleType('leadfinder')
    pkg.__path__=[]
    sys.modules.setdefault('leadfinder',pkg)
    ai=types.ModuleType('leadfinder.ai_runtime')
    ai.app_paths=lambda: None
    ai.node_api=lambda *a,**k: None
    sys.modules['leadfinder.ai_runtime']=ai
    outreach=types.ModuleType('leadfinder.ai_outreach')
    outreach.normalize_phone=lambda number,region: str(number).strip()
    sys.modules['leadfinder.ai_outreach']=outreach
    loc=Path(__file__).resolve().parents[1]/'leadfinder'/'message_queue.py'
    spec=importlib.util.spec_from_file_location('leadfinder.message_queue',loc)
    mod=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=mod
    spec.loader.exec_module(mod)
    mod.register_queue_worker=lambda worker_id: None
    return mod


class TemporaryDB:
    def __init__(self,path):
        self.path=str(path)
        with self.connect(True) as c:
            c.execute('CREATE TABLE ai_workers(id TEXT PRIMARY KEY)')
            c.execute('INSERT INTO ai_workers(id) VALUES (?)',('ana',))

    def initialize(self):return self

    def worker(self,wid):
        return {'id':'ana', 'daily_new_contacts':100} if wid=='ana' else None

    @contextmanager
    def connect(self,write=False):
        c=sqlite3.connect(self.path,timeout=10)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        if write:c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            if write:c.commit()
        except BaseException:
            if write:c.rollback()
            raise
        finally:c.close()


class SendOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=TemporaryDB(Path(self.temp.name)/'leads.db')
        self.queue=load_queue_module()
        self.queue.init_queue(self.db)

    def tearDown(self):self.temp.cleanup()

    def insert(self,bid,state,status,offset=0):
        t=time.time()+offset
        with self.db.connect(True) as c:
            c.execute('''INSERT INTO standalone_message_batches
                (id,worker_id,message,state,country,min_gap,max_gap,created_at,updated_at)
                VALUES(?,?,'Private message',?,'MA',2,6,?,?)''',(bid,'ana',state,t,t))
            c.execute('''INSERT INTO standalone_message_items
                (batch_id,raw_number,e164,status,attempted_at,updated_at)
                VALUES(?,'Private number','+212612345678',?,?,?)''',
                (bid,status,t if status in ('sending','sent','uncertain') else None,t))

    def test_completed_history_removed_but_daily_count_retained(self):
        self.insert('old1','completed','sent')
        self.insert('old2','completed','uncertain')
        self.assertEqual(self.queue.queue_contacts_today(self.db,'ana'),2)
        self.queue.init_queue(self.db)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM standalone_message_batches').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM standalone_message_items').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM standalone_queue_daily_usage').fetchone()[0],1)
        self.assertEqual(self.queue.queue_contacts_today(self.db,'ana'),2)
        self.queue.init_queue(self.db)
        self.assertEqual(self.queue.queue_contacts_today(self.db,'ana'),2)

    def test_paused_batch_stays_until_finished(self):
        self.insert('active','paused','pending')
        self.queue.init_queue(self.db)
        self.assertEqual(self.queue.active_batch(self.db,'ana')['id'],'active')

    def test_inflight_cancel_kept_then_purged(self):
        self.insert('inflight','cancelled','sending')
        self.queue.init_queue(self.db)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM standalone_message_batches').fetchone()[0],1)
        with self.db.connect(True) as c:
            c.execute("UPDATE standalone_message_items SET status='uncertain' WHERE batch_id='inflight'")
        self.queue.init_queue(self.db)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM standalone_message_batches').fetchone()[0],0)
        self.assertEqual(self.queue.queue_contacts_today(self.db,'ana'),1)

    def test_skipped_does_not_consume_send_quota(self):
        self.insert('not-registered','completed','skipped')
        self.queue.init_queue(self.db)
        self.assertEqual(self.queue.queue_contacts_today(self.db,'ana'),0)

    def test_prepare_guard_and_automatic_pruning_after_completion(self):
        bid=self.queue.prepare(self.db,'ana','+212612345678','Hello','MA',2,4)
        self.assertEqual(self.queue.control(self.db,bid,'start'),'running')
        with self.assertRaisesRegex(ValueError,'current queue'):
            self.queue.prepare(self.db,'ana','+212612345678','Second message','MA',2,4)
        with self.db.connect(True) as c:
            now=time.time()
            c.execute("UPDATE standalone_message_items SET status='sent',attempted_at=? WHERE batch_id=?",(now,bid))
            c.execute("UPDATE standalone_message_batches SET state='completed' WHERE id=?",(bid,))
        self.queue.init_queue(self.db)
        self.assertIsNone(self.queue.active_batch(self.db,'ana'))
        self.assertEqual(self.queue.queue_contacts_today(self.db,'ana'),1)

    def test_old_abandoned_preview_cleaned(self):
        self.insert('abandoned','paused','pending',offset=-90000)
        self.queue.init_queue(self.db)
        self.assertIsNone(self.queue.active_batch(self.db,'ana'))


if __name__=='__main__':unittest.main()
