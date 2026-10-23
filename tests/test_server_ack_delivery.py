"""Offline integration regression: local message creation is never a confirmed send."""
from pathlib import Path
import os, sys, tempfile, sqlite3, types, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try: import phonenumbers
except ImportError:
    sys.modules['leadfinder.ai_outreach']=types.SimpleNamespace(
        normalize_phone=lambda p, country='':p,
        GlobalContactLedger=object)
from leadfinder.ai_workers import WorkerDB
from leadfinder import ai_agent

class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {'LEADFINDER_DATA_DIR':self.tmp.name})
        self.env.start()
        self.dbpath=str(Path(self.tmp.name)/'db.sqlite')
        with sqlite3.connect(self.dbpath) as c:
            c.execute('''CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT, niche TEXT, city TEXT,
                website_url TEXT, phone TEXT, phone_e164 TEXT, country_code TEXT,
                notes TEXT DEFAULT '', archived INTEGER DEFAULT 0, do_not_contact INTEGER DEFAULT 0,
                contacted INTEGER DEFAULT 0, crm_status TEXT DEFAULT 'New', next_followup TEXT)''')
            c.execute("INSERT INTO leads(id,name,phone,phone_e164,country_code) VALUES(77,'Recruiter','+212612345671','+212612345671','MA')")
        self.db=WorkerDB(self.dbpath).initialize()
        self.worker=self.db.create_worker('ana')
        self.db.configure_worker(self.worker,enabled=1,model_name='fake')
        self.db.assign(self.worker,[77])
        self.db.inbound(self.worker,'incoming','212612345671@c.us','sift lia cv')
        with self.db.connect() as c:
            inbound=c.execute("SELECT MAX(id) FROM ai_messages WHERE direction='in'").fetchone()[0]
        self.thread={'worker_id':self.worker,'lead_id':77,'latest_inbound_id':inbound,
                     'instruction_profile_id':self.db.worker(self.worker)['instruction_profile_id']}
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def queue(self):
        msgid=self.db.commit_reply(self.thread,'Voici le document demandé 😊')
        self.assertIsNotNone(msgid)
        self.assertIn('[QUEUED',self.db.lead_message_summaries([77])[77]['preview'])
        return msgid
    def dispatch(self,response):
        class Ledger:
            def is_blocked(self,*args):return False
            def claim(self,*args):return True
            def settle(self,*args):pass
            def release(self,*args):pass
        def mock_gateway(endpoint,payload,timeout=10):
            if endpoint=='/status':return {'state':'ready'}
            if endpoint=='/send':return response
            raise RuntimeError('unexpected gateway')
        with patch.object(ai_agent,'node_api',side_effect=mock_gateway):
            ai_agent.send_pending(self.db,Ledger())
    def test_message_id_without_ack_is_unconfirmed_and_not_displayed_as_sent(self):
        mid=self.queue()
        self.dispatch({'sent':True,'messageId':'wamid.local-only','mediaAccepted':False})
        with self.db.connect() as c:
            rec=c.execute('SELECT status,event_id FROM ai_messages WHERE id=?',(mid,)).fetchone()
            last=c.execute('SELECT last_outbound FROM ai_threads WHERE lead_id=77').fetchone()['last_outbound']
        self.assertEqual(rec['status'],'uncertain')
        self.assertEqual(rec['event_id'],'wamid.local-only')
        self.assertIsNone(last)
        self.assertIn('UNCONFIRMED',self.db.lead_message_summaries([77])[77]['preview'])
    def test_only_ack1_confirms_and_sent_preview_has_no_warning(self):
        mid=self.queue()
        self.dispatch({'sent':True,'messageId':'wamid.acked','confirmedBy':'server_ack','ack':1})
        with self.db.connect() as c:
            rec=c.execute('SELECT status,event_id FROM ai_messages WHERE id=?',(mid,)).fetchone()
        self.assertEqual(rec['status'],'sent')
        self.assertEqual(rec['event_id'],'wamid.acked')
        self.assertEqual(self.db.lead_message_summaries([77])[77]['preview'],'Voici le document demandé 😊')
    def test_late_server_ack_recovers_uncertain_without_new_record(self):
        mid=self.queue()
        self.dispatch({'sent':False,'messageId':'wamid.late','reason':'server_ack_timeout'})
        self.assertIn('UNCONFIRMED',self.db.lead_message_summaries([77])[77]['preview'])
        lead=self.db.outgoing_sync(self.worker,'wamid.late','212612345671@c.us','Voici le document demandé 😊')
        self.assertEqual(lead,77)
        with self.db.connect() as c:
            count=c.execute("SELECT COUNT(*) FROM ai_messages WHERE direction='out'").fetchone()[0]
            rec=c.execute('SELECT status FROM ai_messages WHERE id=?',(mid,)).fetchone()
        self.assertEqual(count,1)
        self.assertEqual(rec['status'],'sent')
        self.assertIsNone(self.db.outgoing_sync(self.worker,'wamid.late','212612345671@c.us','Voici le document demandé 😊'))
    def test_status_does_not_demote_after_ack_race(self):
        mid=self.queue()
        # ACK reconciles the same row before the original sender records timeout.
        with self.db.connect(True) as c:
            c.execute('UPDATE ai_messages SET status="uncertain",event_id="wamid.race" WHERE id=?',(mid,))
        self.db.outgoing_sync(self.worker,'wamid.race','212612345671@c.us','Voici le document demandé 😊')
        self.db.status(mid,'uncertain',event_id='wamid.race')
        with self.db.connect() as c:
            status=c.execute('SELECT status FROM ai_messages WHERE id=?',(mid,)).fetchone()['status']
        self.assertEqual(status,'sent')

if __name__=='__main__':unittest.main()
