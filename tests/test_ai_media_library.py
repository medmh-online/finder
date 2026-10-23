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
            return {'sent':True,'messageId':'id1'}
        with patch.object(ai_agent,'node_api',side_effect=fake_node):
            ai_agent.send_pending(self.db,object())
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0][1]['mediaKind'],'document')
        self.assertEqual(sent[0][1]['mediaSha256'],self.db.media_record(mid)['sha256'])
        with self.db.connect() as c:
            row=c.execute('SELECT status FROM ai_messages WHERE id=?',(identifier,)).fetchone()
        self.assertEqual(row['status'],'sent')
        with self.assertRaises(ValueError):self.db.validate_media_choice(self.thread,mid)
    def test_gemini_auto_selects_uploaded_cv_and_hands_off(self):
        mid,_=self.upload()
        worker_model=self.db.worker(self.worker)
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        prompts=[]
        def fake_gen(db,worker,base,prompt,dialogue,**kwargs):
            prompts.append(prompt)
            return json.dumps({'action':'handoff','reply':'Voici son CV. Mohammed vous contactera directement.',
                'media_id':mid,'qualification':{'service':'Developer recruitment'}}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=fake_gen):
            ai_agent.answer_one(self.db,self.thread,self.tmp.name)
        self.assertTrue(prompts)
        self.assertIn(mid,prompts[0])
        with self.db.connect() as c:
            r=c.execute("SELECT kind,media_id FROM ai_messages WHERE direction='out'").fetchone()
            handoff=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()[0]
        self.assertEqual((r['kind'],r['media_id'],handoff),('handoff',mid,1))
        self.assertEqual(self.db.available_media_for_lead(self.profile,1),[])

    def test_switch_blocks_old_profile_media(self):
        mid,_=self.upload()
        identifier=self.db.commit_reply(self.thread,'Here is CV',media_id=mid)
        self.db.activate_instruction_profile(self.worker,'legacy-medmh')
        # Queued replies from previous profile are superseded before sending
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM ai_messages WHERE id=?',(identifier,)).fetchone()[0],'superseded')
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
if __name__=='__main__':unittest.main()
