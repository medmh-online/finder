"""Regression tests for confirmed mass lead removal; no live data touched."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from leadfinder.storage import Repository


class ClearAllLeadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dbfile = Path(self.temp.name) / 'leads.db'
        self.repo = Repository(self.dbfile).initialize()
        with self.repo._write() as c:
            c.executemany("INSERT INTO leads (name,city,niche,updated_at,do_not_contact) VALUES (?, 'Oujda', 'Test', '', ?)",
                          [('Business A',0),('Business B',1)])
            c.execute("INSERT INTO campaigns(name,country,locations,niches,settings,created_at,updated_at) VALUES ('Test','MA','[]','[]','{}','today','today')")
            c.execute("INSERT INTO jobs(campaign_id,state,queries,settings,progress,checkpoint,created_at,updated_at) VALUES (1,'completed','[]','{}','{}','{}','today','today')")
            c.execute("INSERT INTO job_candidates(job_id,source_key,url,lead_id,created_at,updated_at) VALUES(1,'src1','https://example.com',1,'today','today')")
            c.execute("INSERT INTO lead_sources(lead_id,source_key,first_seen,last_seen) VALUES(1,'src1','today','today')")
            c.execute("INSERT INTO lead_discoveries(lead_id,job_id,campaign_id,is_new,seen_at) VALUES(1,1,1,1,'today')")
            c.execute("INSERT INTO activities(lead_id,kind,text,happened_at,created_at) VALUES(1,'note','test','today','today')")
            c.execute('CREATE TABLE ai_workers (id TEXT PRIMARY KEY, name TEXT, enabled INTEGER NOT NULL DEFAULT 0)')
            c.execute('CREATE TABLE ai_assignments (lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE, worker_id TEXT REFERENCES ai_workers(id))')
            c.execute('CREATE TABLE ai_keys(id TEXT PRIMARY KEY,worker_id TEXT REFERENCES ai_workers(id))')
            c.execute("CREATE TABLE ai_messages(id INTEGER PRIMARY KEY,lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,direction TEXT,status TEXT)")
            c.execute('CREATE TABLE ai_first_contact_attempts(message_id INTEGER PRIMARY KEY REFERENCES ai_messages(id) ON DELETE CASCADE, lead_id INTEGER)')
            c.execute('CREATE TABLE ai_usage(id INTEGER PRIMARY KEY,lead_id INTEGER, input_tokens INTEGER)')
            c.execute('CREATE TABLE ai_threads(lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE, in_progress INTEGER, lease_at REAL)')
            c.execute('CREATE TABLE ai_permissions(lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,opted_out_at REAL)')
            c.execute('CREATE TABLE standalone_message_batches(id TEXT PRIMARY KEY,worker_id TEXT REFERENCES ai_workers(id))')
            c.execute("INSERT INTO ai_workers VALUES('w1','WhatsApp Worker',0)")
            c.execute("INSERT INTO ai_assignments VALUES(1,'w1')")
            c.execute("INSERT INTO ai_keys VALUES('secretkey','w1')")
            c.execute("INSERT INTO ai_messages VALUES(1,1,'in','received')")
            c.execute("INSERT INTO ai_messages VALUES(2,NULL,'in','received')")
            c.execute("INSERT INTO ai_first_contact_attempts VALUES(1,1)")
            c.execute("INSERT INTO ai_usage VALUES(1,1,52)")
            c.execute("INSERT INTO ai_threads VALUES(1,0,NULL)")
            c.execute("INSERT INTO ai_permissions VALUES(2,123456)")
            c.execute("INSERT INTO standalone_message_batches VALUES('batch1','w1')")

    def scalar(self, sql):
        with self.repo._read() as c:
            return c.execute(sql).fetchone()[0]

    def test_clears_all_leads_and_related_data_preserves_unrelated_data(self):
        self.assertEqual(self.repo.check_clear_all_leads(2),2)
        backup=self.repo.backup()
        self.assertTrue(Path(backup).is_file())
        self.assertEqual(self.repo.clear_all_leads(expected_count=2),2)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM leads'),0)
        for table in ('lead_sources','lead_discoveries','activities','ai_assignments','ai_permissions','ai_threads','ai_first_contact_attempts'):
            self.assertEqual(self.scalar(f'SELECT COUNT(*) FROM {table}'),0,table)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ai_messages'),1)
        self.assertEqual(self.scalar('SELECT lead_id FROM ai_usage'),None)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ai_workers'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ai_keys'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM standalone_message_batches'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaigns'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM jobs'),1)
        self.assertIsNone(self.scalar('SELECT lead_id FROM job_candidates'))
        with self.repo._read() as c:
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM leads').fetchone()[0],2)

    def test_rejects_wrong_count_without_changes(self):
        with self.assertRaisesRegex(ValueError,'number of leads changed'):
            self.repo.clear_all_leads(expected_count=1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM leads'),2)

    def test_rejects_queued_jobs(self):
        with self.repo._write() as c:
            c.execute("UPDATE jobs SET state='queued' WHERE id=1")
        with self.assertRaisesRegex(ValueError,'queued or active'):
            self.repo.clear_all_leads(expected_count=2)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM leads'),2)

    def test_rejects_active_assigned_ai_worker(self):
        with self.repo._write() as c:
            c.execute("UPDATE ai_workers SET enabled=1 WHERE id='w1'")
        with self.assertRaisesRegex(ValueError,'Disable AI replies'):
            self.repo.clear_all_leads(expected_count=2)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM leads'),2)

    def test_rejects_active_sending(self):
        with self.repo._write() as c:
            c.execute("UPDATE ai_messages SET status='sending',direction='out' WHERE id=1")
        with self.assertRaisesRegex(ValueError,'still sending'):
            self.repo.clear_all_leads(expected_count=2)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM leads'),2)

    def test_database_error_rolls_back_full_transaction(self):
        with self.repo._write() as c:
            c.execute('CREATE TABLE preserve_refs (id INTEGER PRIMARY KEY, lead_id INTEGER REFERENCES leads(id) ON DELETE RESTRICT)')
            c.execute('INSERT INTO preserve_refs VALUES (1,1)')
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.clear_all_leads(expected_count=2)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM leads'),2)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM activities'),1)
        self.assertEqual(self.scalar('SELECT lead_id FROM job_candidates'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ai_messages'),2)


if __name__ == '__main__':
    unittest.main()
