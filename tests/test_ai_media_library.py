from pathlib import Path
import os, sys, tempfile, sqlite3, unittest, types, json
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try: import phonenumbers
except ImportError:
    stub=types.ModuleType('leadfinder.ai_outreach')
    stub.normalize_phone=lambda p, country='': p if p.startswith('+') else '+212'+p.lstrip('0')
    stub.GlobalContactLedger=object
    sys.modules['leadfinder.ai_outreach']=stub
from leadfinder.ai_workers import WorkerDB, uid
from leadfinder.ai_media import store_upload, remove_file, media_root
from leadfinder import ai_agent

class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {'LEADFINDER_DATA_DIR':self.tmp.name})
        self.env.start()
        self.dbpath=str(Path(self.tmp.name)/'leads.db')
        with sqlite3.connect(self.dbpath) as c:
            c.execute('''CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT, niche TEXT, city TEXT, website_url TEXT,
                phone TEXT,phone_e164 TEXT,country_code TEXT,notes TEXT DEFAULT '',archived INTEGER DEFAULT 0,do_not_contact INTEGER DEFAULT 0,
                contacted INTEGER DEFAULT 0,crm_status TEXT DEFAULT 'New',next_followup TEXT)''')
            c.execute("INSERT INTO leads(id,name,phone,phone_e164,country_code) VALUES(1,'Recruiter','+212612345671','+212612345671','MA')")
        self.db=WorkerDB(self.dbpath).initialize()
        self.worker=self.db.create_worker('Recruitment')
        self.db.configure_worker(self.worker,enabled=1,model_name='model1')
        self.db.assign(self.worker,[1])
        self.profile=self.db.create_instruction_profile('Job Hunt','Apply to relevant developer vacancies in Morocco.')
        self.db.activate_instruction_profile(self.worker,self.profile)
        self.db.inbound(self.worker,'in-1','212612345671@c.us','Please send CV')
        with self.db.connect() as c:
            self.inbound=c.execute("SELECT MAX(id) FROM ai_messages WHERE direction='in'").fetchone()[0]
        self.thread={'worker_id':self.worker,'lead_id':1,'latest_inbound_id':self.inbound,
                     'instruction_profile_id':self.profile}
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def upload(self, content=b'%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF\n', name='cv.pdf'):
        mid=uid()
        filename,kind,size,digest=store_upload(mid,name,content)
        self.db.add_profile_media(self.profile,'CV','Send only when recruiter requests CV',filename,kind,size,digest,mid)
        return mid,filename
    def test_upload_and_migration_preserves_files(self):
        mid, filename=self.upload()
        self.assertEqual(self.db.media_record(mid)['profile_id'],self.profile)
        self.db.initialize()
        self.assertTrue((media_root()/filename).is_file())
        self.assertEqual(self.db.validate_media_choice(self.thread,mid)['kind'],'document')
    def test_file_integrity_and_profile_separation(self):
        mid,filename=self.upload()
        wrong=dict(self.thread,instruction_profile_id='legacy-medmh')
        with self.assertRaises(ValueError):self.db.validate_media_choice(wrong,mid)
        (media_root()/filename).write_bytes(b'%PDF-tampered')
        with self.assertRaises(ValueError):self.db.validate_media_choice(self.thread,mid)
    def test_disabled_and_invalid_files(self):
        mid,_=self.upload()
        self.db.set_media_enabled(mid,False)
        with self.assertRaises(ValueError):self.db.validate_media_choice(self.thread,mid)
        with self.assertRaises(ValueError):store_upload(uid(),'bad.pdf',b'wrong contents')
        with self.assertRaises(ValueError):store_upload(uid(),'bad.exe',b'not executable')
        with self.assertRaises(ValueError):store_upload(uid(),'large.pdf',b'%PDF-'+b'0'*(12*1024*1024))
    def test_handoff_queues_attachment_and_worker_sends(self):
        mid,_=self.upload()
        self.db.validate_media_choice(self.thread,mid)
        identifier=self.db.complete_qualification(self.thread,'Merci, voici le CV',{'service':'Job'},media_id=mid)
        self.assertIsNotNone(identifier)
        pending=[m for m in self.db.pending_outbound() if m['id']==identifier]
        self.assertEqual(pending[0]['media_id'],mid)
        sent=[]
        def fake_node(endpoint,payload,timeout=10):
            if endpoint=='/status': return {'state':'ready'}
            sent.append((endpoint,payload))
            return {'sent':True,'messageId':'id1','mediaAccepted':True,'confirmedBy':'server_ack','ack':1}
        with patch.object(ai_agent,'node_api',side_effect=fake_node):
            ai_agent.send_pending(self.db,object())
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0][1]['mediaKind'],'document')
        self.assertEqual(sent[0][1]['mediaSha256'],self.db.media_record(mid)['sha256'])
        with self.db.connect() as c:
            row=c.execute('SELECT status FROM ai_messages WHERE id=?',(identifier,)).fetchone()
        self.assertEqual(row['status'],'sent')
        # The first confirmed send is allowed to be explicitly requested once again.
        self.assertEqual(self.db.validate_media_choice(self.thread,mid)['id'],mid)
    def test_cv_request_does_not_handoff_when_first_model_proposes_it(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        prompts=[]
        def route(*args,**kwargs):
            prompts.append(args[3])
            return json.dumps({'file_requested':True,'human_required':False,'media_id':mid}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=route):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        self.assertEqual(len(prompts),1)
        self.assertIn('attachment request classifier',prompts[0])
        with self.db.connect() as c:
            r=c.execute("SELECT kind,media_id,body FROM ai_messages WHERE direction='out'").fetchone()
            handoff=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()
        self.assertEqual(r['media_id'],mid)
        self.assertNotEqual(r['kind'],'handoff')
        self.assertFalse(handoff and handoff['handoff'])
        self.assertEqual(self.db.available_media_for_lead(self.profile,1),[])

    def test_false_cannot_attach_claim_gets_independent_media_review(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        # Generic text generation is never called for an authorized request.
        calls=[]
        def gen(*a,**k):
            calls.append(a[3]);return json.dumps({'file_requested':True,'human_required':False,'media_id':mid}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=gen):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            out=c.execute("SELECT body,media_id,status FROM ai_messages WHERE direction='out'").fetchone()
        self.assertEqual(len(calls),1)
        self.assertEqual(out['media_id'],mid)
        self.assertEqual(out['status'],'queued_reply')
        self.assertNotIn('peux pas',out['body'])

    def test_media_review_does_not_override_real_interview_handoff(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        drafts=[json.dumps({'file_requested':False,'human_required':True,'media_id':None}),
                json.dumps({'action':'handoff','reply':'Mohammed vous appellera personnellement.','media_id':None}),
                json.dumps({'requested_matching_file':False,'media_id':None})]
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=lambda *a,**k:(drafts.pop(0),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            out=c.execute("SELECT kind,media_id FROM ai_messages WHERE direction='out'").fetchone()
            handoff=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()
        self.assertEqual(out['kind'],'handoff')
        self.assertIsNone(out['media_id'])
        self.assertTrue(handoff['handoff'])

    def test_readiness_explains_uncertain_and_profile_mismatch(self):
        mid,_=self.upload()
        first=self.db.media_readiness_for_lead(self.worker,1)
        self.assertEqual(first['files'][0]['status'],'available')
        self.assertTrue(first['thread_profile_matches_worker'])
        out=self.db.commit_reply(self.thread,'Voici le CV',media_id=mid)
        self.db.status(out,'uncertain')
        flagged=self.db.media_readiness_for_lead(self.worker,1)
        self.assertIn('uncertain',flagged['files'][0]['status'])
        self.db.unlock_unconfirmed_media(self.worker,out)
        self.assertEqual(self.db.media_readiness_for_lead(self.worker,1)['files'][0]['status'],'available')
        self.db.activate_instruction_profile(self.worker,'legacy-medmh')
        changed=self.db.media_readiness_for_lead(self.worker,1)
        self.assertFalse(changed['thread_profile_matches_worker'])
        self.assertEqual(changed['files'],[])


    def test_switch_blocks_old_profile_media(self):
        mid,_=self.upload()
        identifier=self.db.commit_reply(self.thread,'Here is CV',media_id=mid)
        self.db.activate_instruction_profile(self.worker,'legacy-medmh')
        # Queued replies from previous profile are superseded before sending
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM ai_messages WHERE id=?',(identifier,)).fetchone()[0],'superseded')
    def test_false_cv_delivery_claim_is_repaired_before_queueing(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        calls=[]
        def gen(*args,**kwargs):
            calls.append(args[3])
            return json.dumps({'file_requested':True,'human_required':False,'media_id':mid}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=gen):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        self.assertEqual(len(calls),1)
        with self.db.connect() as c:
            sent=c.execute("SELECT media_id,status FROM ai_messages WHERE direction='out'").fetchall()
        self.assertEqual(len(sent),1)
        self.assertEqual((sent[0]['media_id'],sent[0]['status']),(mid,'queued_reply'))

    def test_cannot_send_false_claim_without_upload(self):
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        proposals=[
            {'action':'reply','reply':'Voilà son CV 😊','media_id':None},
            {'action':'reply','reply':'Je ne peux pas joindre le CV actuellement.','media_id':None}
        ]
        with patch.object(ai_agent,'gemini_with_fallback',
                          side_effect=lambda *args,**kwargs:(json.dumps(proposals.pop(0)),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            msg=c.execute("SELECT media_id,body FROM ai_messages WHERE direction='out'").fetchone()
        self.assertIsNone(msg['media_id'])
        self.assertNotIn('Voilà',msg['body'])

    def test_uncertain_cv_record_is_visible_to_model_but_not_resent(self):
        mid,_=self.upload()
        msgid=self.db.commit_reply(self.thread,'Voilà son CV 😊',media_id=mid)
        self.db.status(msgid,'uncertain')
        self.assertEqual(self.db.available_media_for_lead(self.profile,1),[])
        statuses=self.db.media_delivery_state(self.profile,1)
        self.assertEqual(statuses[0]['status'],'uncertain')
        hist=self.db.messages(1,limit=50)
        self.assertNotIn('Voilà son CV',str(ai_agent.human_history(hist)))
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        prompts=[]
        def gen(*args,**kwargs):
            prompts.append(args[3])
            return json.dumps({'file_requested':True,'human_required':False,'media_id':mid}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=gen):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        self.assertIn('"available": false',prompts[0])
        self.assertEqual(len(prompts),1)
        with self.db.connect() as c:
            out=c.execute('SELECT media_id,body,status FROM ai_messages WHERE direction="out" ORDER BY id DESC LIMIT 1').fetchone()
        self.assertIsNone(out['media_id'])
        self.assertIn('pas encore confirmé',out['body'])
        self.assertEqual(len([x for x in self.db.messages(1,limit=50) if x.get('media_id')==mid]),1)

    def test_operator_unlocks_only_unconfirmed_media(self):
        mid,_=self.upload()
        ident=self.db.commit_reply(self.thread,'Voilà son CV',media_id=mid)
        self.db.status(ident,'uncertain')
        with self.assertRaises(ValueError):self.db.unlock_unconfirmed_media('wrong-worker',ident)
        self.db.unlock_unconfirmed_media(self.worker,ident)
        self.assertEqual(len(self.db.available_media_for_lead(self.profile,1)),1)
        self.assertEqual(self.db.recent_worker_media_delivery(self.worker)[0]['status'],'failed')
        with self.assertRaises(ValueError):self.db.unlock_unconfirmed_media(self.worker,ident)

    def test_gateway_media_accepted_required_for_media_sent(self):
        mid,_=self.upload()
        ident=self.db.commit_reply(self.thread,'Voici son CV',media_id=mid)
        def fake_node(endpoint,payload,timeout=10):
            if endpoint=='/status':return {'state':'ready'}
            return {'sent':True,'messageId':'123'} # old gateway, cannot prove media was attached
        with patch.object(ai_agent,'node_api',side_effect=fake_node):
            ai_agent.send_pending(self.db,object())
        with self.db.connect() as c:
            status=c.execute('SELECT status FROM ai_messages WHERE id=?',(ident,)).fetchone()[0]
        self.assertEqual(status,'uncertain')

    def test_ogg_opus_direct(self):
        mid=uid()
        raw=b'OggS'+b'0'*40+b'OpusHead'+b'\x00'*80
        filename,kind,_,_=store_upload(mid,'intro.ogg',raw)
        self.assertEqual((filename,kind),(mid+'.ogg','voice'))
    def test_bad_gemini_media_id(self):
        self.assertEqual(ai_agent.decision_media_id('{"action":"reply", "media_id":null}'),None)
        with self.assertRaises(ValueError):ai_agent.decision_media_id('{"action":"reply", "media_id":"../../etc/passwd"}')
    def test_delete_metadata_and_file(self):
        mid,filename=self.upload()
        with self.assertRaises(ValueError):self.db.delete_instruction_profile(self.profile)
        deleted=self.db.delete_profile_media(mid)
        self.assertEqual(deleted,filename)
        remove_file(filename)
        self.assertFalse((media_root()/filename).exists())
    def test_requested_cv_remains_active_and_queues_media(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        prompts=[]
        def fake_gen(db,worker,base,prompt,dialogue,**kwargs):
            prompts.append(prompt)
            return json.dumps({'file_requested':True,'human_required':False,'media_id':mid}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=fake_gen):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        self.assertIn('A CV or portfolio request alone never requires human handoff',prompts[0])
        with self.db.connect() as conn:
            msg=conn.execute("SELECT kind,media_id,status FROM ai_messages WHERE direction='out'").fetchone()
            ctrl=conn.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()
        self.assertEqual(msg['media_id'],mid)
        self.assertEqual(msg['status'],'queued_reply')
        self.assertNotEqual(msg['kind'],'handoff')
        self.assertFalse(ctrl and ctrl['handoff'])

    def test_router_does_not_send_for_greeting(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        answers=[{'file_requested':False,'human_required':False,'media_id':None},
                 {'action':'reply','reply':'Bonjour ! Comment puis-je vous aider ?','media_id':None}]
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=lambda *a,**k:(json.dumps(answers.pop(0)),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            out=c.execute("SELECT body,media_id FROM ai_messages WHERE direction='out'").fetchone()
        self.assertIsNone(out['media_id'])
        self.assertIn('Bonjour',out['body'])

    def test_router_rejects_unapproved_id_no_handoff(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        with patch.object(ai_agent,'gemini_with_fallback',return_value=(json.dumps(
            {'file_requested':True,'human_required':False,'media_id':'a'*32}),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM ai_messages WHERE direction='out'").fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM ai_lead_control WHERE handoff=1').fetchone()[0],0)


    def test_matching_uploaded_media_unavailable_no_immediate_handoff(self):
        mid,_=self.upload()
        self.db.set_media_enabled(mid,False)
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        with patch.object(ai_agent,'gemini_with_fallback',return_value=(json.dumps(
            {'file_requested':True,'human_required':False,'media_id':mid}),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            outbound=c.execute('SELECT status,media_id,kind,body FROM ai_messages WHERE direction="out"').fetchone()
            ctrl=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()
        self.assertEqual(outbound['status'],'queued_reply')
        self.assertIsNone(outbound['media_id'])
        self.assertNotEqual(outbound['kind'],'handoff')
        self.assertFalse(ctrl and ctrl['handoff'])

    def test_cv_request_without_matching_media_does_not_handoff(self):
        self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        with patch.object(ai_agent,'gemini_with_fallback',return_value=(json.dumps(
            {'file_requested':True,'human_required':False,'media_id':None}),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        with self.db.connect() as c:
            outbound=c.execute('SELECT status,media_id,kind,body FROM ai_messages WHERE direction="out"').fetchone()
            ctrl=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()
        self.assertIsNone(outbound['media_id'])
        self.assertNotEqual(outbound['kind'],'handoff')
        self.assertFalse(ctrl and ctrl['handoff'])

    def test_router_selects_before_main_model_and_dispatches(self):
        mid,_=self.upload()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        with patch.object(ai_agent,'gemini_with_fallback',return_value=(json.dumps(
            {'file_requested':True,'human_required':False,'media_id':mid}),{})):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        deliveries=[]
        def fake_node(endpoint,payload,timeout=10):
            if endpoint=='/status':return {'state':'ready'}
            deliveries.append(payload);return {'sent':True,'messageId':'wamid.fake','mediaAccepted':True,'confirmedBy':'server_ack','ack':1}
        with patch.object(ai_agent,'node_api',side_effect=fake_node):
            ai_agent.send_pending(self.db,object())
        self.assertEqual(len(deliveries),1)
        self.assertEqual(deliveries[0]['mediaKind'],'document')
        self.assertEqual(deliveries[0]['mediaFile'],self.db.media_record(mid)['filename'])
        with self.db.connect() as c:
            msg=c.execute("SELECT status FROM ai_messages WHERE direction='out'").fetchone()
        self.assertEqual(msg['status'],'sent')

if __name__=='__main__':unittest.main()
