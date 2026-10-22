"""Offline AI scheduler quota and fairness checks; no Gemini calls."""
import sqlite3, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
from leadfinder.ai_workers import WorkerDB

class QuotaTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)/'leads.db'
        with sqlite3.connect(p) as c:
            c.execute('''CREATE TABLE leads(id INTEGER PRIMARY KEY, name TEXT, phone TEXT, phone_e164 TEXT,country_code TEXT,
                city TEXT, niche TEXT, archived INTEGER NOT NULL DEFAULT 0, do_not_contact INTEGER NOT NULL DEFAULT 0,website_url TEXT)''')
            for i in range(1,4):
                c.execute('INSERT INTO leads(id,name,phone_e164,country_code) VALUES(?,?,?,?)',
                          (i,'Test',f'+21261234567{i}','MA'))
        self.db=WorkerDB(p).initialize()
        self.worker=self.db.create_worker('A')
        self.db.configure_worker(self.worker,enabled=1,model_name='test-model',batching_seconds=2)
        key=self.db.add_key(self.worker,'test','not-a-real-key','test')
        self.db.tested_models(key,self.worker,[{'model':'test-model','working':True}])
        self.db.select_working_model(self.worker,'test-model')
        self.db.assign(self.worker,[1,2,3])
        with self.db.connect(True) as c:
            for i in range(1,4):
                c.execute('INSERT INTO ai_threads(lead_id,last_inbound) VALUES(?,?)',(i,time.time()-35+i))
                c.execute('''INSERT INTO ai_messages(lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at)
                   VALUES(?,?,?,'in','hello','received',?,?)''',(i,self.worker,f'21261234567{i}@c.us',time.time()-35+i,time.time()-35+i))
    def test_exhaustion_marks_all_pending_and_no_lease(self):
        self.db.update_settings(daily_ai_limit=1)
        self.db.log_usage(self.worker,'fake','test-model','reply',0,0,error='test offline failure')
        self.assertIsNone(self.db.due())
        self.assertEqual(self.db.generation_quota(self.worker)['remaining'],0)
        for i in range(1,4):
            self.assertEqual(self.db.reply_trace(i)['stage'],'daily_limit')
    def test_increase_limit_unblocks_existing_inbound(self):
        self.db.update_settings(daily_ai_limit=1)
        self.db.log_usage(self.worker,'fake','test-model','reply',0,0,error='test failure')
        self.assertIsNone(self.db.due())
        self.db.update_settings(daily_ai_limit=3)
        next_thread=self.db.due()
        self.assertEqual(next_thread['lead_id'],1)
        self.db.release(1);self.db.release_generation(self.worker)
    def test_invalid_model_marks_all(self):
        self.db.configure_worker(self.worker,model_name='')
        self.assertIsNone(self.db.due())
        self.assertTrue(all(self.db.reply_trace(i)['stage']=='model_unavailable' for i in (1,2,3)))
    def test_valid_request_uses_oldest_first(self):
        thread=self.db.due()
        self.assertEqual(thread['lead_id'],1)
    def test_blocked_leads_not_considered(self):
        with self.db.connect(True) as c:
            c.execute('UPDATE leads SET do_not_contact=1 WHERE id=1')
        self.assertEqual(self.db.due()['lead_id'],2)
if __name__=='__main__':unittest.main()
