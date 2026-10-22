"""Canonical phone parsing and privacy-preserving installation-wide contact lock.

No outreach is authorized by simply being listed on Google Maps. Callers must
verify consent separately. The lock cannot detect outreach outside this installation.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import re
import unicodedata
from pathlib import Path
import sqlite3
import time

import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException


def normalize_phone(raw, country=''):
    """Normalize supported phone formats locally to a single valid E.164 number.

    This operation NEVER calls Gemini or a network service. National numbers
    require their ISO country code. We try common explicit international forms
    (such as 00CC..., CC..., +CC...) but never use dangerous suffix matching.
    """
    value = unicodedata.normalize('NFKC', str(raw or '')).strip()
    value = re.sub(r'[\u200b-\u200f\u2060\ufeff]', '', value)
    if not value or len(value) > 100:
        return ''
    region = str(country or '').strip().upper()
    if len(region) != 2 or not region.isalpha():
        region = None
    forms = [value]
    digits = ''.join(ch for ch in value if ch.isascii() and ch.isdigit())
    compact = re.sub(r'[\s().\-]', '', value)
    # Explicit 00 international prefix; covers "00 212 ..." too.
    if compact.startswith('00') and compact[2:].isdigit():
        forms.append('+' + compact[2:])
    if compact.isdigit() and len(compact) >= 8:
        # Some spreadsheets store 212612345678 without the leading plus.
        if region:
            dial = phonenumbers.country_code_for_region(region)
            if dial and compact.startswith(str(dial)):
                forms.append('+' + compact)
        elif len(digits) >= 11 and not compact.startswith('0'):
            forms.append('+' + compact)
    # A valid number must produce just one canonical identity, regardless of
    # how it was written. Never guess between two different valid identities.
    identities = set()
    for form in dict.fromkeys(forms):
        try:
            parsed = phonenumbers.parse(form, region)
            if phonenumbers.is_valid_number(parsed):
                identities.add(phonenumbers.format_number(parsed, PhoneNumberFormat.E164))
        except (NumberParseException, TypeError, ValueError):
            pass
    return next(iter(identities)) if len(identities) == 1 else ''


class GlobalContactLedger:
    """Shared by ALL workers within this local installation, not across different computers.

    Stores only HMAC identifiers, never raw phone numbers or other workers' data.
    A claimed phone remains blocked until a safe release or a confirmed send.
    Uncertain deliveries deliberately remain blocked for manual review.
    """
    def __init__(self, base):
        base = Path(base).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / 'ai_global_contacts.sqlite3'
        self.secret_path = base / 'ai_contact_hmac.key'
        if not self.secret_path.exists():
            try:
                with self.secret_path.open('xb') as f:
                    f.write(os.urandom(32))
                if os.name != 'nt':
                    self.secret_path.chmod(0o600)
            except FileExistsError:
                pass
        self.secret = self.secret_path.read_bytes()
        with self._connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS contacted (
                phone_digest TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
                lead_id INTEGER NOT NULL, state TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS blocked_numbers (
                phone_digest TEXT PRIMARY KEY, blocked_at REAL NOT NULL)''')

    def _connect(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA busy_timeout=30000')
        return con

    def _digest(self, phone):
        if not phone.startswith('+') or not phone[1:].isdigit():
            raise ValueError('A valid E.164 phone is required')
        return hmac.new(self.secret, phone.encode('ascii'), hashlib.sha256).hexdigest()

    def is_blocked(self, phone):
        with self._connect() as c:
            return bool(c.execute('SELECT 1 FROM blocked_numbers WHERE phone_digest=?',
                                  (self._digest(phone),)).fetchone())

    def block(self, phone):
        """Permanent installation-wide opt-out; never cleared by reassignment."""
        if not phone:
            return
        with self._connect() as c:
            c.execute('INSERT OR IGNORE INTO blocked_numbers(phone_digest,blocked_at) VALUES(?,?)',
                      (self._digest(phone), time.time()))

    def is_taken(self, phone):
        with self._connect() as c:
            token=self._digest(phone)
            return bool(c.execute('''SELECT 1 FROM contacted WHERE phone_digest=?
                UNION SELECT 1 FROM blocked_numbers WHERE phone_digest=?''', (token,token)).fetchone())

    def taken_phones(self, phones):
        """Batch duplicate-status check without revealing contact owner information."""
        items={phone:self._digest(phone) for phone in phones if phone}
        found=set()
        with self._connect() as c:
            digests=list(items.values())
            for off in range(0,len(digests),300):
                group=digests[off:off+300]
                if not group:continue
                marks=','.join('?' for _ in group)
                found.update(row[0] for row in c.execute(
                    'SELECT phone_digest FROM contacted WHERE phone_digest IN ('+marks+') UNION '
                    'SELECT phone_digest FROM blocked_numbers WHERE phone_digest IN ('+marks+')',group+group))
        return {phone for phone,digest in items.items() if digest in found}

    def claim(self, phone, worker_id, lead_id):
        token = self._digest(phone)
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            found = c.execute('''SELECT 1 FROM contacted WHERE phone_digest=?
                UNION SELECT 1 FROM blocked_numbers WHERE phone_digest=?''', (token,token)).fetchone()
            if found:
                c.rollback()
                return False
            current = time.time()
            c.execute('INSERT INTO contacted VALUES(?,?,?,?,?,?)', (token, worker_id, int(lead_id), 'reserved', current, current))
            c.commit()
            return True

    def settle(self, phone, worker_id, lead_id, status):
        if status not in ('sent', 'uncertain'):
            raise ValueError('Invalid delivery state')
        with self._connect() as c:
            c.execute('''UPDATE contacted SET state=?,updated_at=? WHERE phone_digest=?
                AND worker_id=? AND lead_id=? AND state='reserved' ''',
                (status, time.time(), self._digest(phone), worker_id, int(lead_id)))
            c.commit()

    def release(self, phone, worker_id, lead_id):
        with self._connect() as c:
            c.execute('''DELETE FROM contacted WHERE phone_digest=? AND worker_id=? AND lead_id=? AND state='reserved' ''',
                (self._digest(phone), worker_id, int(lead_id)))
            c.commit()

    def observe_sent(self, phone, worker_id, lead_id):
        """Record outgoing messages observed in the visible Chrome window.

        Cannot retroactively detect contact made outside this installation.
        """
        digest = self._digest(phone)
        t = time.time()
        with self._connect() as c:
            c.execute('''INSERT OR IGNORE INTO contacted
                (phone_digest,worker_id,lead_id,state,created_at,updated_at)
                VALUES(?,?,?,'sent',?,?)''',
                (digest,worker_id,int(lead_id),t,t))
            c.commit()
