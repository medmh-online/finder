"""MedMH shared business knowledge regression tests (offline, no WhatsApp/Gemini)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from leadfinder.ai_workers import WorkerDB
from leadfinder import business_knowledge as knowledge


class KnowledgeUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'leads.db'
        with sqlite3.connect(self.path) as con:
            con.execute("""CREATE TABLE leads (
                id INTEGER PRIMARY KEY, name TEXT, phone TEXT, phone_e164 TEXT,
                country_code TEXT, city TEXT, niche TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                do_not_contact INTEGER NOT NULL DEFAULT 0, website_url TEXT)""")
        self.db = WorkerDB(self.path).initialize()

    def test_new_install_has_owner_approved_catalog(self):
        s = self.db.account_settings()
        self.assertEqual(s['business_name'], 'MEDMH')
        self.assertEqual(s['portfolio'], 'https://medmh.online')
        for snippet in ('600–1 200', '1 200–2 200', '1 000–2 000',
                        '1 800–3 500', '3 000–5 500', '4 500–8 000',
                        '2 500–6 000', '800–3 000', '2 500–6 000 + frais API'):
            self.assertIn(snippet, s['pricing'])
        self.assertIn('stock', s['services'])
        self.assertIn('offline', s['services'])
        self.assertIn('assistant IA b7al hada', s['instructions'])

    def test_revision_applied_once_no_repeat_overwrite(self):
        self.db.update_settings(services='Edited by operator later',
                                daily_ai_limit=387)
        self.db.initialize()
        self.assertEqual(self.db.account_settings()['services'], 'Edited by operator later')
        self.assertEqual(self.db.account_settings()['daily_ai_limit'], 387)
        with sqlite3.connect(self.path) as con:
            result = con.execute('SELECT COUNT(*) FROM ai_knowledge_revisions').fetchone()[0]
        self.assertEqual(result, 1)

    def test_existing_catalog_is_backed_up_and_updated_once(self):
        with sqlite3.connect(self.path) as con:
            con.execute('UPDATE ai_account_settings SET services=?', ('Prior knowledge',))
            con.execute('DELETE FROM ai_knowledge_revisions')
        self.db.initialize()
        self.assertEqual(self.db.account_settings()['services'], knowledge.SERVICES)
        with sqlite3.connect(self.path) as con:
            row = con.execute("""SELECT previous_settings_json
                    FROM ai_knowledge_revisions WHERE revision=?""",
                    (knowledge.KNOWLEDGE_REVISION,)).fetchone()
        self.assertEqual(json.loads(row[0])['services'], 'Prior knowledge')

    def test_sales_instructions_fit_prompt_context(self):
        self.assertLessEqual(len(knowledge.SALES_INSTRUCTIONS), 2400)
        self.assertIn('assistants WhatsApp', knowledge.SERVICES)
        self.assertIn('portfolio seulement si demandé', knowledge.SALES_INSTRUCTIONS)
        self.assertIn('ne force pas le passage au développeur', knowledge.SALES_INSTRUCTIONS)

    def test_no_worker_keys_or_leads_touched(self):
        with sqlite3.connect(self.path) as con:
            con.execute('INSERT INTO leads(id,name) VALUES (1,?)', ('Fixture',))
        worker = self.db.create_worker('test worker')
        self.db.initialize()
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT name FROM leads WHERE id=1').fetchone()[0], 'Fixture')
            self.assertEqual(con.execute('SELECT name FROM ai_workers WHERE id=?',(worker,)).fetchone()[0], 'test worker')

if __name__ == '__main__':
    unittest.main()
