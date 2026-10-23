from pathlib import Path
import sys, tempfile, sqlite3, unittest, types, json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:import phonenumbers
except ImportError:
    import leadfinder
    stub=types.ModuleType('leadfinder.ai_outreach')
    stub.normalize_phone=lambda p, country='': (p if p.startswith('+') else ('+212'+p[1:] if p.startswith('0') else '+'+p))
    stub.GlobalContactLedger=object
    sys.modules['leadfinder.ai_outreach']=stub
from leadfinder.ai_workers import WorkerDB, now
class TestProfiles(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/'leads.db')
        with sqlite3.connect(self.path) as c:
            c.execute('''CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT, niche TEXT, city TEXT, website_url TEXT,
                phone TEXT,phone_e164 TEXT,country_code TEXT,notes TEXT DEFAULT "",archived INTEGER DEFAULT 0,do_not_contact INTEGER DEFAULT 0,
                contacted INTEGER DEFAULT 0,crm_status TEXT DEFAULT 'New',next_followup TEXT)''')
            for i in [1,2]:c.execute("INSERT INTO leads(id,name,phone,phone_e164,country_code) VALUES(?,?,?,?,?)",(i,'Test '+str(i),'+21261234567'+str(i),'+21261234567'+str(i),'MA'))
        self.db=WorkerDB(self.path).initialize()
        self.worker=self.db.create_worker('ana')
        self.db.configure_worker(self.worker,enabled=1,model_name='fake-model')
        self.db.assign(self.worker,[1,2])
    def tearDown(self):self.tmp.cleanup()
    def test_import_and_save(self):
        profiles=self.db.instruction_profiles()
        self.assertEqual(len(profiles),1)
        self.assertIn('MEDMH',profiles[0]['instructions'])
        prev=profiles[0]['instructions']
        self.db.initialize()
        self.assertEqual(self.db.instruction_profiles()[0]['instructions'],prev)
    def test_new_profile_and_pinning(self):
        jobs=self.db.create_instruction_profile('Job Hunting','Ask for suitable developer roles in Morocco. CV requests mean handoff.')
        self.db.inbound(self.worker,'evt-1','212612345671@c.us','bonjour')
        self.assertEqual(self.db.profile_for_thread({'worker_id':self.worker,'instruction_profile_id':'legacy-medmh'})['name'],'Imported MEDMH knowledge')
        self.db.activate_instruction_profile(self.worker,jobs)
        self.db.inbound(self.worker,'evt-2','212612345672@c.us','CV?')
        with self.db.connect() as c:
            threads=c.execute('SELECT lead_id,instruction_profile_id FROM ai_threads ORDER BY lead_id').fetchall()
        self.assertEqual([x['instruction_profile_id'] for x in threads],['legacy-medmh',jobs])
        self.assertEqual(self.db.paused_profile_threads(self.worker),1)
        # stale old-purpose reply cannot be committed under new profile
        self.assertIsNone(self.db.commit_reply({'worker_id':self.worker,'lead_id':1,'latest_inbound_id':1},'Wrong purpose'))
        # the job profile can be selected only for new chats
        self.assertTrue(self.db.instruction_profile(jobs))
        with self.assertRaises(ValueError):self.db.delete_instruction_profile(jobs)
        self.db.activate_instruction_profile(self.worker,'legacy-medmh')
        self.assertEqual(self.db.paused_profile_threads(self.worker),1)
    def test_edit_keep_data(self):
        pid=self.db.create_instruction_profile('Job Hunting','Contact agencies about developer work.')
        self.db.update_instruction_profile(pid,'Jobs','CV requests require human handoff, no salary claims.')
        self.assertEqual(self.db.instruction_profile(pid)['name'],'Jobs')
        with self.assertRaises(ValueError):self.db.create_instruction_profile('jobs','duplicate name')
        self.db.delete_instruction_profile(pid)
        self.assertIsNone(self.db.instruction_profile(pid))
    def test_no_crm_impact(self):
        pid=self.db.create_instruction_profile('Job Hunt','Introduce as job candidate; politely qualify recruiters.')
        self.db.activate_instruction_profile(self.worker,pid)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM leads').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_assignments').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_workers').fetchone()[0],1)
    def test_gemini_profile_and_recruitment_handoff(self):
        from leadfinder import ai_agent
        pid=self.db.create_instruction_profile('Recruitment',
            'You assist Mohammed in finding a developer job. Hand off on CV request. Do not pretend to send the CV.')
        self.db.activate_instruction_profile(self.worker,pid)
        self.db.inbound(self.worker,'in-123','212612345671@c.us','Pouvez-vous envoyer son CV ?')
        with self.db.connect() as c:
            msg_id=c.execute("SELECT MAX(id) FROM ai_messages WHERE lead_id=1 AND direction='in'").fetchone()[0]
        thread={'worker_id':self.worker,'lead_id':1,'latest_inbound_id':msg_id}
        original=ai_agent.gemini_with_fallback
        original_models=self.db.working_models
        original_budget=self.db.token_budget_available
        seen=[]
        def fake_generation(db,worker,base,prompt,messages,**kwargs):
            seen.append(prompt)
            return json.dumps({'action':'handoff',
                'reply':'Mohammed va reprendre la conversation pour son CV.',
                'qualification':{'service':'Developer job','requirements':'Recruiter requested CV'}}),{}
        try:
            ai_agent.gemini_with_fallback=fake_generation
            self.db.working_models=lambda wid:['fake-model']
            self.db.token_budget_available=lambda *args,**kwargs:True
            ai_agent.answer_one(self.db,thread,self.tmp.name)
        finally:
            ai_agent.gemini_with_fallback=original
            self.db.working_models=original_models
            self.db.token_budget_available=original_budget
        self.assertIn('developer job',seen[0])
        self.assertNotIn('menu QR simple',seen[0])
        with self.db.connect() as c:
            out=c.execute("SELECT kind,status,body FROM ai_messages WHERE lead_id=1 AND direction='out'").fetchone()
            handed=c.execute('SELECT handoff FROM ai_lead_control WHERE lead_id=1').fetchone()
        self.assertEqual((out['kind'],out['status'],handed['handoff']),('handoff','queued_reply',1))

    def test_guard_handoff(self):
        self.db.inbound(self.worker,'evt-1','212612345671@c.us','CV?')
        with self.db.connect() as c:
            inbound=c.execute('SELECT id FROM ai_messages WHERE direction="in"').fetchone()[0]
        thread={'worker_id':self.worker,'lead_id':1,'latest_inbound_id':inbound}
        self.db.activate_instruction_profile(self.worker,self.db.create_instruction_profile('Jobs','Respond to recruitment questions and hand off for CV.'))
        self.assertIsNone(self.db.complete_qualification(thread,'I will send a CV.'))
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_messages WHERE direction="out"').fetchone()[0],0)
if __name__=='__main__':unittest.main()
