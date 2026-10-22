"""Offline reply-pipeline regression tests. No WhatsApp or Gemini network requests."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from leadfinder.ai_workers import WorkerDB
from leadfinder.ai_agent import answer_one,parse_ai_decision


class TestReplyPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'leads.db'
        with sqlite3.connect(path) as c:
            c.execute('''CREATE TABLE leads(id INTEGER PRIMARY KEY, name TEXT,
                phone TEXT, phone_e164 TEXT,country_code TEXT,city TEXT,niche TEXT,
                archived INTEGER NOT NULL DEFAULT 0, do_not_contact INTEGER NOT NULL DEFAULT 0,
                website_url TEXT, notes TEXT,crm_status TEXT NOT NULL DEFAULT 'New',
                contacted INTEGER NOT NULL DEFAULT 0,next_followup REAL)''')
            c.execute("INSERT INTO leads(id,name,phone,phone_e164,country_code,city,niche) VALUES(1,'Test lead','0612345678','+212612345678','MA','Oujda','test')")
        self.db=WorkerDB(path).initialize()
        self.worker_id=self.db.create_worker('Testing')
        self.db.configure_worker(self.worker_id, enabled=1,model_name='unit-test-model',batching_seconds=2)
        key=self.db.add_key(self.worker_id,'Test','FAKE_ENCRYPTED_KEY','XXXX')
        self.db.tested_models(key,self.worker_id,[{'model':'unit-test-model','working':True}])
        self.db.select_working_model(self.worker_id,'unit-test-model')
        self.db.assign(self.worker_id,[1])

    def _inbound(self,text='Hello',event='msg1'):
        return self.db.inbound(self.worker_id,event,'212612345678@c.us',text)

    def _due(self):
        with self.db.connect(True) as c:
            c.execute('UPDATE ai_threads SET last_inbound=? WHERE lead_id=1',(time.time()-10,))
        return self.db.due()

    def test_received_queued_sent(self):
        self.assertEqual(self._inbound(),1)
        self.assertEqual(self.db.reply_trace(1)['stage'],'received')
        thread=self._due()
        self.assertIsNotNone(thread)
        self.assertEqual(self.db.reply_trace(1)['stage'],'generating')
        with patch('leadfinder.ai_agent.gemini_with_fallback', return_value=(json.dumps({'action':'reply','reply':'Hello!'}), {})):
            answer_one(self.db,thread,Path(self.tmp.name))
        with self.db.connect() as c:
            msg=c.execute("SELECT id,body,status FROM ai_messages WHERE direction='out'").fetchone()
            self.assertEqual(msg['status'],'queued_reply')
            self.assertEqual(msg['body'],'Hello!')
        self.assertEqual(self.db.reply_trace(1)['stage'],'queued')
        self.db.status(msg['id'],'sent')
        self.assertEqual(self.db.reply_trace(1)['stage'],'sent')

    def test_incomplete_json_is_not_discarded(self):
        self._inbound()
        thread=self._due()
        with patch('leadfinder.ai_agent.gemini_with_fallback', return_value=(' {"action":"reply","reply":"incomplete', {})):
            answer_one(self.db,thread,Path(self.tmp.name))
        self.assertEqual(self.db.reply_trace(1)['stage'],'retry_scheduled')
        with self.db.connect() as c:
            state=c.execute('SELECT replied_through,retry_after FROM ai_threads WHERE lead_id=1').fetchone()
            self.assertEqual(state['replied_through'],0)
            self.assertGreater(state['retry_after'],time.time())
            self.assertEqual(c.execute("SELECT count(*) FROM ai_messages WHERE direction='out'").fetchone()[0],0)
        self.db.release(1)
        self.db.release_generation(self.worker_id)
        self.assertIsNone(self._due())

    def test_new_message_clears_backoff(self):
        self._inbound()
        thread=self._due()
        self.db.retry_reply(thread,'Temporary API failure',delay=120)
        self.db.release(1)
        self.db.release_generation(self.worker_id)
        self.assertEqual(self._inbound('Are you there?','msg2'),1)
        self.assertEqual(self.db.reply_trace(1)['stage'],'received')
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT retry_after FROM ai_threads WHERE lead_id=1').fetchone()[0],0)

    def test_first_ignore_corrected_by_ai(self):
        self._inbound('Salam')
        thread=self._due()
        replies=[(json.dumps({'action':'ignore','reply':''}),{}),
                 (json.dumps({'action':'reply','reply':'Wa alaykom salam!'}),{})]
        with patch('leadfinder.ai_agent.gemini_with_fallback',side_effect=replies) as req:
            answer_one(self.db,thread,Path(self.tmp.name))
        self.assertEqual(req.call_count,2)
        self.assertEqual(self.db.reply_trace(1)['stage'],'queued')

    def test_explicit_opt_out_preserved(self):
        self._inbound('Stop contacting me')
        with self.db.connect() as c:
            self.assertTrue(c.execute('SELECT do_not_contact FROM leads WHERE id=1').fetchone()[0])

    def test_model_unavailable_reports_reason(self):
        self._inbound()
        thread=self._due()
        self.db.configure_worker(self.worker_id,model_name='not-tested')
        answer_one(self.db,thread,Path(self.tmp.name))
        self.assertEqual(self.db.reply_trace(1)['stage'],'model_unavailable')

    def test_malformed_is_failure_not_ignore(self):
        with self.assertRaises(ValueError):
            parse_ai_decision('{"action":"reply", unfinished')
        self.assertEqual(parse_ai_decision('{"action":"reply","reply":"Hi"}'),('reply','Hi'))


if __name__=='__main__': unittest.main()
