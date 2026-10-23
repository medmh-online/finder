"""CV one-time repeat and profile OGG audio routing regression tests."""
from pathlib import Path
import json, unittest
from unittest.mock import patch
import test_ai_media_library as media_fixtures
from leadfinder import ai_agent
from leadfinder.ai_workers import uid
from leadfinder.ai_media import store_upload,media_root


class VoiceAndRepeatTests(unittest.TestCase):
    def setUp(self):
        self.fx = media_fixtures.MediaTests('test_upload_and_migration_preserves_files')
        self.fx.setUp()
        self.db,self.thread=self.fx.db,self.fx.thread
    def tearDown(self):
        self.fx.tearDown()
    def make_ogg(self):
        mid=uid()
        # A small file sufficient to test the storage format and routing.
        raw=b'OggS'+b'\x00'*25+b'OpusHead'+b'\x01\x02'+b'\x00'*40
        name,kind,size,sha=store_upload(mid,'introduction.ogg',raw)
        self.assertEqual(name,mid+'.ogg')
        self.assertEqual(kind,'voice')
        self.db.add_profile_media(self.thread['instruction_profile_id'],'Introduction audio',
            'Envoyer si le recruteur veut une introduction vocale a ecouter',name,kind,size,sha,mid)
        return mid
    def test_voice_introduction_semantic_routing_to_ogg(self):
        voice_id=self.make_ogg()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        seen=[]
        def ai(*args,**kwargs):
            instructions=args[3]
            seen.append(instructions)
            self.assertIn('spoken introduction',instructions)
            self.assertIn('"kind": "voice"',instructions)
            self.assertIn('introduction tkun msm3a',instructions)
            return json.dumps({'file_requested':True,'human_required':False,
                               'media_id':voice_id,'recipient_confirms_no_delivery':False}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=ai):
            ai_agent.answer_one(self.db,self.thread,self.fx.tmp.name)
        self.assertEqual(len(seen),1)
        with self.db.connect() as c:
            msg=c.execute("SELECT id,media_id,status,body FROM ai_messages WHERE direction='out' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(msg['media_id'],voice_id)
        self.assertEqual(msg['status'],'queued_reply')
        self.assertIn('vocal',msg['body'])
        sends=[]
        def fake_node(endpoint,payload,timeout=10):
            if endpoint=='/status':return {'state':'ready'}
            sends.append(payload)
            return {'sent':True,'messageId':'voice1','mediaAccepted':True,'confirmedBy':'server_ack','ack':1}
        with patch.object(ai_agent,'node_api',side_effect=fake_node):
            ai_agent.send_pending(self.db,object())
        self.assertEqual(len(sends),1)
        self.assertEqual(sends[0]['mediaKind'],'voice')
        self.assertEqual(sends[0]['mediaFile'],voice_id+'.ogg')
    def test_once_confirmed_cv_can_be_requested_again_but_not_third_time(self):
        mid,_=self.fx.upload()
        first=self.db.commit_reply(self.thread,'Voici le CV',media_id=mid)
        self.assertIsNotNone(first)
        self.db.status(first,'sent')
        permitted=self.db.available_media_for_lead(self.thread['instruction_profile_id'],1)
        self.assertEqual(permitted[0]['id'],mid)
        self.assertEqual(permitted[0]['confirmed_sends'],1)
        self.assertEqual(self.db.validate_media_choice(self.thread,mid)['id'],mid)
        second=self.db.commit_reply(self.thread,'Voici le CV encore une fois',media_id=mid)
        self.assertIsNotNone(second)
        self.assertEqual(self.db.available_media_for_lead(self.thread['instruction_profile_id'],1),[])
        with self.assertRaises(ValueError):self.db.validate_media_choice(self.thread,mid)
        self.db.status(second,'sent')
        self.assertEqual(self.db.available_media_for_lead(self.thread['instruction_profile_id'],1),[])
        with self.assertRaises(ValueError):self.db.validate_media_choice(self.thread,mid)
        self.assertIsNone(self.db.commit_reply(self.thread,'Third CV',media_id=mid))
    def test_old_uncertain_does_not_block_after_newer_confirmed_delivery(self):
        mid,_=self.fx.upload()
        original=self.db.commit_reply(self.thread,'Original CV',media_id=mid)
        self.db.status(original,'uncertain')
        self.assertEqual(self.db.available_media_for_lead(self.thread['instruction_profile_id'],1),[])
        # A later operator-approved recovery send has been acknowledged.
        with self.db.connect(True) as c:
            c.execute("INSERT INTO ai_messages(lead_id,worker_id,chat_id,direction,body,status,sent_at,created_at,media_id) VALUES(?,?,?,?,?,?,?,?,?)",
              (1,self.thread['worker_id'],'212600000001@c.us','out','Verified CV','sent',42,42,mid))
        eligible=self.db.available_media_for_lead(self.thread['instruction_profile_id'],1)
        self.assertEqual(eligible[0]['confirmed_sends'],1)
        self.assertEqual(self.db.validate_media_choice(self.thread,mid)['id'],mid)

    def test_audio_selection_recovers_when_classifier_omits_id(self):
        voice_id=self.make_ogg()
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**k:True
        calls=[]
        def fake_ai(*args,**kwargs):
            calls.append(args[3])
            if len(calls)==1:
                return json.dumps({'file_requested':True,'human_required':False,
                    'media_id':None,'recipient_confirms_no_delivery':False}),{}
            self.assertIn('Never select a CV when',args[3])
            return json.dumps({'requested_matching_file':True,'media_id':voice_id}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=fake_ai):
            ai_agent.answer_one(self.db,self.thread,self.fx.tmp.name)
        with self.db.connect() as c:
            msg=c.execute("SELECT media_id,status FROM ai_messages WHERE direction='out' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(msg['media_id'],voice_id)
        self.assertEqual(msg['status'],'queued_reply')
        self.assertEqual(len(calls),2)

    def test_audio_unavailable_excuse_is_detected(self):
        self.assertTrue(ai_agent.media_unavailable_excuse("Je n'ai pas de fichier audio disponible à envoyer"))

    def test_confirmed_once_voice_or_cv_is_eligible_only_if_router_requests_it(self):
        mid,_=self.fx.upload()
        first=self.db.commit_reply(self.thread,'Voici le CV',media_id=mid)
        self.db.status(first,'sent')
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        calls=[]
        def responder(*args,**kw):
            calls.append(args[3])
            if len(calls)==1:
                return json.dumps({'file_requested':False,'human_required':False,'media_id':None}),{}
            return json.dumps({'action':'reply','reply':'Bonjour !','media_id':None}),{}
        with patch.object(ai_agent,'gemini_with_fallback',side_effect=responder):
            ai_agent.answer_one(self.db,self.thread,self.fx.tmp.name)
        self.assertEqual(len(calls),2)
        self.assertIn('ALLOWED MEDIA (use only these IDs, or null): []', calls[1])
        with self.db.connect() as c:
            files=c.execute("SELECT COUNT(*) FROM ai_messages WHERE media_id=? AND direction='out'",(mid,)).fetchone()[0]
        self.assertEqual(files,1)
    def test_repeated_cv_request_router_queues_only_second(self):
        mid,_=self.fx.upload()
        first=self.db.commit_reply(self.thread,'Voici le CV',media_id=mid)
        self.db.status(first,'sent')
        self.db.working_models=lambda wid:['model1']
        self.db.token_budget_available=lambda *a,**kw:True
        with patch.object(ai_agent,'gemini_with_fallback',return_value=(json.dumps({
            'file_requested':True,'human_required':False,'media_id':mid,
            'recipient_confirms_no_delivery':False}),{})):
            ai_agent.answer_one(self.db,self.thread,self.fx.tmp.name)
        with self.db.connect() as c:
            msgs=list(c.execute("SELECT media_id,status FROM ai_messages WHERE media_id=? ORDER BY id",(mid,)))
        self.assertEqual(len(msgs),2)
        self.assertEqual(msgs[-1]['status'],'queued_reply')
