"""Purely local phone/identity regression tests. No messages sent."""
import sqlite3
import unittest

from leadfinder.ai_outreach import normalize_phone
from leadfinder.ai_workers import WorkerDB
from leadfinder.message_queue import normalize_locally


class PhoneNormalizingTests(unittest.TestCase):
    def test_morocco_common_formats(self):
        samples = ('0612345678', '06 12 34 56 78', '+212 612-345-678',
                   '00212 612 345 678', '212612345678', '612345678')
        for sample in samples:
            with self.subTest(value=sample):
                self.assertEqual(normalize_phone(sample, 'MA'), '+212612345678')

    def test_country_scope(self):
        self.assertEqual(normalize_phone('06 12 34 56 78', 'FR'), '+33612345678')
        self.assertEqual(normalize_phone('0612345678', ''), '')
        self.assertEqual(normalize_phone('not a phone', 'MA'), '')

    def test_queue_is_offline(self):
        self.assertEqual(normalize_locally(['06 12 34 56 78', '+212612345678', 'garbage'], 'MA'),
                         ['+212612345678', '+212612345678', ''])


class AssignedWorkerMatchingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE leads (id INTEGER PRIMARY KEY, phone TEXT, phone_e164 TEXT,
                                country_code TEXT, do_not_contact INTEGER DEFAULT 0);
            CREATE TABLE ai_assignments (worker_id TEXT, lead_id INTEGER);
            CREATE TABLE ai_chat_alias (worker_id TEXT, chat_id TEXT, lead_id INTEGER,
                          UNIQUE(worker_id,chat_id));
            CREATE TABLE ai_unmatched (event_id TEXT, worker_id TEXT, chat_id TEXT,
                          body TEXT, sent_at REAL);
        """)
        self.db = WorkerDB('unused-test-path')

    def tearDown(self):
        self.conn.close()

    def add(self, lead_id, phone, canonical='', country='MA', worker='worker1'):
        self.conn.execute('INSERT INTO leads(id,phone,phone_e164,country_code) VALUES(?,?,?,?)',
                          (lead_id, phone, canonical, country))
        self.conn.execute('INSERT INTO ai_assignments(worker_id,lead_id) VALUES(?,?)',
                          (worker, lead_id))

    def test_stored_national_to_whatsapp_international(self):
        self.add(1, '06 12 34 56 78')
        row = self.db._match_lead(self.conn, 'worker1', '212612345678@c.us')
        self.assertEqual(row['id'], 1)

    def test_formatted_lead_both_columns(self):
        self.add(1, '00212 612 345 678', '+212612345678')
        row = self.db._match_lead(self.conn, 'worker1', '212612345678@c.us')
        self.assertEqual(row['id'], 1)

    def test_worker_isolation(self):
        self.add(1, '0612345678', worker='otherworker')
        self.assertIsNone(self.db._match_lead(self.conn, 'worker1', '212612345678@c.us'))

    def test_duplicate_blocks_auto_matching(self):
        self.add(1, '0612345678')
        self.add(2, '+212612345678')
        self.assertIsNone(self.db._match_lead(self.conn, 'worker1', '212612345678@c.us'))

    def test_opaque_lid_needs_verified_phone(self):
        self.add(1, '0612345678')
        lid = '123456789012345@lid'
        self.assertIsNone(self.db._match_lead(self.conn, 'worker1', lid))
        self.assertEqual(self.db._match_lead(self.conn, 'worker1', lid,
                           '+212612345678')['id'], 1)

    def test_conflicting_valid_stored_numbers_rejected(self):
        self.add(1, '0612345678', '+212698765432')
        self.assertIsNone(self.db._match_lead(self.conn, 'worker1', '212612345678@c.us'))
        self.assertIsNone(self.db._match_lead(self.conn, 'worker1', '212698765432@c.us'))


if __name__ == '__main__':
    unittest.main()
