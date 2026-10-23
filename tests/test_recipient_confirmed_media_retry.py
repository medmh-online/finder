"""Regression tests for the stuck 'envoi non confirmé' CV loop.

Only an explicit new inbound non-delivery confirmation permits ONE bounded
re-attempt. An unknown provider result is never silently treated as success.
"""
import os, sys, tempfile, sqlite3, unittest, types
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:import phonenumbers
except ImportError:
    stub=types.ModuleType('leadfinder.ai_outreach')
    stub.normalize_phone=lambda p,country='': p if p.startswith('+') else '+212'+p.lstrip('0')
    stub.GlobalContactLedger=object
    sys.modules['leadfinder.ai_outreach']=stub
from leadfinder.ai_workers import WorkerDB,uid,now
from leadfinder.ai_media import store_upload
from leadfinder.ai_agent import parse_media_router,route_incoming_attachment,answer_one

class RecipientRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LEADFINDER_DATA_DIR':self.tmp.name});self.env.start()
        self.path=str(Path(self.tmp.name)/'leads.db')
        with sqlite3.connect(self.path) as c:
            c.execute('''CREATE TABLE leads (id INTEGER PRIMARY KEY,name TEXT,niche TEXT,city TEXT,website_url TEXT,
                phone TEXT,phone_e164 TEXT,country_code TEXT,notes TEXT DEFAULT '',archived INTEGER DEFAULT 0,
                do_not_contact INTEGER DEFAULT 0,contacted INTEGER DEFAULT 0,crm_status TEXT DEFAULT 'New',next_followup TEXT)''')
            c.execute("INSERT INTO leads(id,name,phone,phone_e164,country_code) VALUES(77,'Recruiter','+212612345671','+212612345671','MA')")
        self.db=WorkerDB(self.path).initialize()
        self.worker=self.db.create_worker('Job AI')
        self.db.configure_worker(self.worker,enabled=1,model_name='test')
        self.db.assign(self.worker,[77])
        self.profile=self.db.create_instruction_profile('Jobs','Job-hunting assistant')
        self.db.activate_instruction_profile(self.worker,self.profile)
        self.file_id=uid()
        name,kind,size,sha=store_upload(self.file_id,'myCV.pdf',b'%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF\n')
        self.db.add_profile_media(self.profile,'CV Mohammed','Only send to recruiters requesting CV',name,kind,size,sha,self.file_id)
        self.db.inbound(self.worker,'evt1','212612345671@c.us','Please send CV')
        self.thread=self.thread_for_latest()
        self.original=self.db.commit_reply(self.thread,'Voici son CV',media_id=self.file_id)
        self.db.status(self.original,'uncertain',event_id='wamid.original')
        with self.db.connect(True) as c:
            c.execute('UPDATE ai_messages SET created_at=? WHERE id=?',(now()-90,self.original))
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def thread_for_latest(self):
        with self.db.connect() as c:
            inbound=c.execute("SELECT MAX(id) FROM ai_messages WHERE direction='in'").fetchone()[0]
        return {'worker_id':self.worker,'lead_id':77,'latest_inbound_id':inbound,'instruction_profile_id':self.profile}
    def new_message(self,body='I received nothing. Please send the CV again.'):
        self.db.inbound(self.worker,uid(),'212612345671@c.us',body)
        self.thread=self.thread_for_latest()
    def test_parser_requires_explicit_recipient_missing_flag(self):
        import json
        decision={'file_requested':True,'human_required':False,'media_id':self.file_id,
                  'recipient_confirms_no_delivery':True}
        self.assertEqual(parse_media_router(json.dumps(decision),{self.file_id}),(True,False,self.file_id,True))
        decision['recipient_confirms_no_delivery']='true'
        with self.assertRaises(ValueError):parse_media_router(json.dumps(decision),{self.file_id})
    def test_repeated_send_me_cv_does_not_retry_without_missing_confirmation(self):
        self.new_message('send me CV')
        self.assertIsNone(self.db.queue_recipient_confirmed_media_retry(self.thread,'not-file','Here'))
        # Invoked only when the semantic router explicitly confirms absence.
        self.assertEqual(len(self.db.available_media_for_lead(self.profile,77)),0)
    def test_recipient_report_allows_one_retry_preserving_uncertain_audit(self):
        self.new_message()
        queued=self.db.queue_recipient_confirmed_media_retry(self.thread,self.file_id,'Voici le CV 😊')
        self.assertIsInstance(queued,int)
        self.assertEqual(self.db.queue_recipient_confirmed_media_retry(self.thread,self.file_id,'Again'),None)
        with self.db.connect() as c:
            old=c.execute('SELECT status FROM ai_messages WHERE id=?',(self.original,)).fetchone()['status']
            retry=c.execute('SELECT status,media_id,kind FROM ai_messages WHERE id=?',(queued,)).fetchone()
            audit=c.execute('SELECT original_message_id,retry_message_id,confirmed_by_inbound_id FROM ai_media_recipient_retries').fetchone()
            handoff=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=77').fetchone()
        self.assertEqual(old,'uncertain')
        self.assertEqual(retry['status'],'queued_reply')
        self.assertEqual(retry['media_id'],self.file_id)
        self.assertEqual(tuple(audit)[:2],(self.original,queued))
        self.assertIsNone(handoff)
        self.db.status(queued,'uncertain')
        self.new_message('Still no CV received')
        self.assertIsNone(self.db.queue_recipient_confirmed_media_retry(self.thread,self.file_id,'No more retries'))
    def test_recipient_retry_forbidden_when_sent_or_profile_switched(self):
        self.new_message()
        with self.db.connect(True) as c:c.execute("UPDATE ai_messages SET status='sent' WHERE id=?",(self.original,))
        self.assertIsNone(self.db.queue_recipient_confirmed_media_retry(self.thread,self.file_id,'No duplicate'))
        with self.db.connect(True) as c:c.execute("UPDATE ai_messages SET status='uncertain' WHERE id=?",(self.original,))
        other=self.db.create_instruction_profile('Other','MEDMH sales services')
        self.db.activate_instruction_profile(self.worker,other)
        self.assertIsNone(self.db.queue_recipient_confirmed_media_retry(self.thread,self.file_id,'No cross profile'))
    def test_late_ack_cancels_queued_retry_before_send(self):
        self.new_message()
        retry=self.db.queue_recipient_confirmed_media_retry(self.thread,self.file_id,'Voici le CV')
        self.assertIsNotNone(retry)
        self.assertEqual(self.db.outgoing_sync(self.worker,'wamid.original',
            '212612345671@c.us','Voici son CV'),77)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM ai_messages WHERE id=?',
                (self.original,)).fetchone()['status'],'sent')
            self.assertEqual(c.execute('SELECT status FROM ai_messages WHERE id=?',
                (retry,)).fetchone()['status'],'superseded')
    def test_matching_body_does_not_falsely_confirm_media(self):
        # A matching text echo is not proof the attached PDF was sent.
        self.assertEqual(self.db.outgoing_sync(self.worker,'wamid.unrelated',
            '212612345671@c.us','Voici son CV'),77)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM ai_messages WHERE id=?',
                (self.original,)).fetchone()['status'],'uncertain')
    def test_repeated_requests_do_not_spam_identical_uncertain_warning(self):
        import json
        self.db.working_models=lambda wid:['test']
        self.db.token_budget_available=lambda wid:True
        payload=json.dumps({'file_requested':True,'human_required':False,'media_id':self.file_id,
            'recipient_confirms_no_delivery':False})
        self.new_message('Please send CV')
        with patch('leadfinder.ai_agent.gemini_with_fallback',return_value=(payload,{})):
            answer_one(self.db,self.thread,self.tmp.name)
            self.new_message('CV please')
            answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            warnings=c.execute("SELECT body FROM ai_messages WHERE direction='out' AND media_id IS NULL").fetchall()
        self.assertEqual(len(warnings),1)
        self.assertIn('pas encore confirmé',warnings[0]['body'])
    def test_router_queues_on_explicit_report_instead_of_apology(self):
        import json
        self.new_message('I received nothing, please send me his CV')
        self.db.working_models=lambda wid:['test']
        self.db.token_budget_available=lambda wid:True
        payload=json.dumps({'file_requested':True,'human_required':False,'media_id':self.file_id,
            'recipient_confirms_no_delivery':True})
        with patch('leadfinder.ai_agent.gemini_with_fallback',return_value=(payload,{})):
            answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            rows=[dict(r) for r in c.execute("SELECT status,media_id,body FROM ai_messages WHERE direction='out' ORDER BY id")]
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[-1]['media_id'],self.file_id)
        self.assertEqual(rows[-1]['status'],'queued_reply')
        self.assertNotIn('confirmé',rows[-1]['body'])

if __name__=='__main__':unittest.main()
