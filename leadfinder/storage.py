"""SQLite persistence for the local lead finder.

Every write is a short transaction and every method owns its connection.  The
repository can therefore be shared by a worker and its heartbeat thread.  No
network access, Streamlit state, or scraping decisions belong in this module.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

SCHEMA_VERSION = 2
ACTIVE_STATES = {"running", "pause_requested", "cancel_requested"}
JOB_STATES = {
    "queued", "running", "pause_requested", "paused", "cancel_requested",
    "cancelled", "completed", "partial", "failed",
}
TERMINAL_STATES = {"cancelled", "completed", "partial", "failed"}
PROGRESS_KEYS = (
    "inspected", "saved", "duplicate", "refreshed", "excluded_website",
    "excluded_phone", "unknown", "errors", "queries_completed", "discovered",
)
CRM_FIELDS = {
    "contacted", "notes", "crm_status", "deal_value", "last_contacted",
    "next_followup", "manual_priority", "archived", "archived_at",
    "do_not_contact", "first_seen", "created_at",
    "owner", "email", "email_source_url", "email_checked_at",
    "establishment_verified", "establishment_source", "establishment_evidence",
    "business_since_verified", "business_since_source", "business_since_evidence",
    "business_since", "established_year",
}
OBSERVED_FIELDS = {
    "name", "city", "country", "country_code", "country_raw", "niche",
    "phone", "phone_raw", "rating", "reviews_count", "address", "maps_link",
    "source_url", "place_id", "source_key", "fb_link", "instagram_link",
    "website_url", "website_status", "website_type", "category",
    "business_status", "maps_age_hint", "business_since", "established_year",
    "establishment_source", "oldest_review_age_years", "review_age_is_sample",
    "review_sample_count", "observed_at", "website_checked",
    "website_extraction_ok", "extraction_ok", "website_audit",
    "enrichment_checked_at", "emails", "contact_page", "booking_links",
    "enquiry_links", "website_title", "has_facebook", "has_instagram",
    "detail_verified", "observation_status", "website_verified",
}
EDITABLE_FIELDS = {
    "name", "city", "niche", "country", "country_code", "phone", "phone_raw",
    "address", "maps_link", "source_url", "website_url", "website_status",
    "fb_link", "instagram_link", "rating", "notes",
    "contacted", "crm_status", "deal_value", "last_contacted",
    "archived", "do_not_contact", "business_since",
    "established_year", "establishment_source", "category", "business_status",
    "website_audit", "enrichment_checked_at", "emails", "contact_page",
    "booking_links", "enquiry_links", "website_title",
    "email", "email_source_url", "email_checked_at",
    "establishment_verified", "establishment_source", "establishment_evidence",
    "business_since_verified", "business_since_source", "business_since_evidence",
}

LEAD_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "name": "TEXT NOT NULL DEFAULT ''",
    "city": "TEXT NOT NULL DEFAULT ''",
    "niche": "TEXT NOT NULL DEFAULT ''",
    "country": "TEXT DEFAULT ''",
    "country_code": "TEXT DEFAULT ''",
    "country_raw": "TEXT DEFAULT ''",
    "phone": "TEXT DEFAULT ''",
    "phone_raw": "TEXT DEFAULT ''",
    "phone_e164": "TEXT DEFAULT ''",
    "phone_type": "TEXT DEFAULT 'UNKNOWN'",
    "phone_valid": "INTEGER NOT NULL DEFAULT 0",
    "whatsapp_link": "TEXT",
    "rating": "REAL",
    "reviews_count": "INTEGER",
    "address": "TEXT DEFAULT ''",
    "maps_link": "TEXT DEFAULT ''",
    "source_url": "TEXT DEFAULT ''",
    "place_id": "TEXT DEFAULT ''",
    "source_key": "TEXT DEFAULT ''",
    "fb_link": "TEXT DEFAULT ''",
    "instagram_link": "TEXT DEFAULT ''",
    "has_facebook": "INTEGER DEFAULT 0",
    "has_instagram": "INTEGER DEFAULT 0",
    "website_url": "TEXT DEFAULT ''",
    "website_status": "TEXT NOT NULL DEFAULT 'unknown'",
    "website_type": "TEXT DEFAULT 'Unknown'",
    "category": "TEXT DEFAULT ''",
    "business_status": "TEXT DEFAULT ''",
    "first_seen": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "observed_at": "TEXT",
    "last_observed_at": "TEXT",
    "contacted": "INTEGER DEFAULT 0",
    "notes": "TEXT DEFAULT ''",
    "crm_status": "TEXT DEFAULT 'New'",
    "deal_value": "REAL DEFAULT 0",
    "last_contacted": "TEXT",
    "next_followup": "TEXT",
    "maps_age_hint": "TEXT",
    "business_since": "TEXT",
    "established_year": "INTEGER",
    "establishment_source": "TEXT",
    "establishment_evidence": "TEXT",
    "establishment_verified": "INTEGER DEFAULT 0",
    "business_since_source": "TEXT",
    "business_since_evidence": "TEXT",
    "business_since_verified": "INTEGER DEFAULT 0",
    "owner": "TEXT DEFAULT ''",
    "email": "TEXT DEFAULT ''",
    "email_source_url": "TEXT DEFAULT ''",
    "email_checked_at": "TEXT",
    "oldest_review_age_years": "REAL",
    "review_age_is_sample": "INTEGER DEFAULT 1",
    "review_sample_count": "INTEGER",
    "lead_score": "INTEGER DEFAULT 0",
    "lead_priority": "TEXT DEFAULT 'Cold'",
    "data_confidence": "INTEGER DEFAULT 0",
    "score_breakdown": "TEXT DEFAULT '{}'",
    "manual_priority": "TEXT DEFAULT ''",
    "archived": "INTEGER NOT NULL DEFAULT 0",
    "archived_at": "TEXT",
    "do_not_contact": "INTEGER NOT NULL DEFAULT 0",
    "legacy_unverified": "INTEGER NOT NULL DEFAULT 0",
    "observation_status": "TEXT DEFAULT 'observed'",
    "legacy_duplicate": "INTEGER NOT NULL DEFAULT 0",
    "duplicate_group": "TEXT DEFAULT ''",
    "legacy_priority": "TEXT",
    "legacy_data": "TEXT DEFAULT '{}'",
    "website_audit": "TEXT DEFAULT '{}'",
    "enrichment_checked_at": "TEXT",
    "extra_data": "TEXT DEFAULT '{}'",
    "identity_name": "TEXT DEFAULT ''",
    "identity_address": "TEXT DEFAULT ''",
}
LEAD_JSON_FIELDS = {"score_breakdown", "legacy_data", "website_audit", "extra_data"}
LEAD_BOOL_FIELDS = {
    "phone_valid", "has_facebook", "has_instagram", "contacted", "archived",
    "do_not_contact", "legacy_unverified", "legacy_duplicate", "review_age_is_sample",
    "establishment_verified", "business_since_verified",
}


class LeaseLostError(RuntimeError):
    """The job is no longer owned by the calling worker."""


def prepare_lead(data, config=None):
    # Lease recovery and backups do not need the optional normalization stack.
    # Importing it only at the observation boundary also keeps those recovery
    # tools usable when a dependency install is being repaired.
    from .quality import prepare_lead as normalize
    return normalize(data, config)


def country_info(value):
    from .quality import country_info as resolve
    return resolve(value)


def canonical_source_key(**values):
    from .quality import canonical_source_key as identify
    return identify(**values)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _loads(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _normal(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def _when(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _boolean(value, field):
    if isinstance(value, bool) or value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} must be true or false")


def _integer(value, field, minimum=0):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a whole number")
    try:
        result = int(value)
        if float(value) != result or result < minimum:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be a whole number of at least {minimum}") from None
    return result


def _safe_url(value, field):
    from urllib.parse import urlsplit
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} cannot contain credentials")
    return value


class Repository:
    """Persistence boundary. Call initialize() once before using other methods."""

    def __init__(self, db_path, config=None):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.config = config or {}
        self.last_backup_path = None
        self.lease_seconds = 180

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def _read(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self):
        """Compatibility helper; all method-owned connections are already closed."""

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _version(conn):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_meta'").fetchone()
        if not exists:
            return 0
        row = conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else 0

    def initialize(self):
        """Back up and atomically migrate old data, preserving every original row.

        Migration runs only once. Legacy cache is retained but never read by the
        new observation cache. The complete original row remains in legacy_data.
        """
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.exists() and path.stat().st_size > 0
        with self._read() as conn:
            version = self._version(conn)
        if version > SCHEMA_VERSION:
            raise RuntimeError("This database was created by a newer app version")
        if version == SCHEMA_VERSION:
            return self
        if existing:
            self.last_backup_path = self.backup()
        with self._write() as conn:
            if self._version(conn) == SCHEMA_VERSION:
                return self
            lead_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='leads'").fetchone()
            legacy_rows = []
            if lead_table:
                legacy_rows = [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY id")]
                conn.execute("ALTER TABLE leads RENAME TO leads_legacy_migration")
            self._create_schema(conn)
            for original in legacy_rows:
                data = dict(original)
                data.update({
                    "legacy_data": original,
                    "legacy_priority": original.get("lead_priority"),
                    "legacy_unverified": True,
                    "observation_status": "legacy_unverified",
                    "country_raw": original.get("country") or "",
                    "phone_raw": original.get("phone_raw") or original.get("phone") or "",
                    "manual_priority": original.get("manual_priority") or "",
                    "created_at": original.get("created_at") or original.get("first_seen") or utc_now(),
                    "updated_at": utc_now(),
                })
                prepared = prepare_lead(data, self.config)
                # These are human workflow fields, not scraping observations.
                for field in CRM_FIELDS:
                    if field in original:
                        prepared[field] = original[field]
                prepared["id"] = original["id"]
                prepared["legacy_data"] = original
                prepared["legacy_priority"] = original.get("lead_priority")
                prepared["legacy_unverified"] = True
                prepared["observation_status"] = "legacy_unverified"
                prepared["source_key"] = self._primary_key(prepared) or f"legacy:{original['id']}"
                self._insert_lead(conn, prepared)
                self._remember_sources(conn, original["id"], prepared)
            if lead_table:
                # Rebuilding removes UNIQUE(name, city, niche), without deleting
                # or combining records that already exist in the original DB.
                conn.execute("DROP TABLE leads_legacy_migration")
                self._mark_legacy_duplicates(conn)
                conn.execute("INSERT INTO app_meta(key,value) VALUES('legacy_row_count',?)", (str(len(legacy_rows)),))
                conn.execute("INSERT INTO app_meta(key,value) VALUES('migration_backup',?)", (self.last_backup_path or "",))
            conn.execute("INSERT INTO app_meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))
            conn.execute("INSERT INTO app_meta(key,value) VALUES('migrated_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (utc_now(),))
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError("Database migration failed its foreign key check")
        with self._read() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        return self

    @staticmethod
    def _create_schema(conn):
        lead_definition = ",\n".join(f'"{name}" {definition}' for name, definition in LEAD_COLUMNS.items())
        statements = [
            "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            f"CREATE TABLE leads ({lead_definition})",
            "CREATE INDEX leads_source_key ON leads(source_key)",
            "CREATE INDEX leads_identity_address ON leads(identity_name,identity_address,country_code)",
            "CREATE INDEX leads_identity_phone ON leads(identity_name,phone_e164,country_code)",
            "CREATE INDEX leads_followup ON leads(next_followup,archived,do_not_contact)",
            """CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT '', country_code TEXT NOT NULL DEFAULT '',
                locations TEXT NOT NULL DEFAULT '[]', niches TEXT NOT NULL DEFAULT '[]',
                settings TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER REFERENCES campaigns(id),
                state TEXT NOT NULL DEFAULT 'queued', queries TEXT NOT NULL DEFAULT '[]',
                settings TEXT NOT NULL DEFAULT '{}', progress TEXT NOT NULL DEFAULT '{}',
                checkpoint TEXT NOT NULL DEFAULT '{}', reason TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, heartbeat_at TEXT, worker_token TEXT)""",
            "CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,id)",
            """CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id),
                level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS job_logs_job ON job_logs(job_id,id)",
            """CREATE TABLE IF NOT EXISTS job_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id),
                source_key TEXT NOT NULL, url TEXT NOT NULL, preview_name TEXT DEFAULT '',
                query_index INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
                lead_id INTEGER REFERENCES leads(id), outcome TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(job_id,source_key))""",
            "CREATE INDEX IF NOT EXISTS candidates_job_state ON job_candidates(job_id,state,query_index)",
            """CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL REFERENCES leads(id),
                kind TEXT NOT NULL, text TEXT NOT NULL, happened_at TEXT NOT NULL,
                created_at TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}')""",
            "CREATE INDEX IF NOT EXISTS activities_lead ON activities(lead_id,happened_at,id)",
            """CREATE TABLE IF NOT EXISTS lead_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL REFERENCES leads(id),
                source_key TEXT NOT NULL, source_url TEXT DEFAULT '',
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(lead_id,source_key))""",
            "CREATE INDEX IF NOT EXISTS source_identity ON lead_sources(source_key,lead_id)",
            """CREATE TABLE IF NOT EXISTS lead_discoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL REFERENCES leads(id),
                job_id INTEGER REFERENCES jobs(id), campaign_id INTEGER REFERENCES campaigns(id),
                query TEXT, is_new INTEGER NOT NULL DEFAULT 0, seen_at TEXT NOT NULL)""",
            "CREATE UNIQUE INDEX IF NOT EXISTS discovery_identity ON lead_discoveries(lead_id,COALESCE(job_id,-1),COALESCE(campaign_id,-1))",
            """CREATE TABLE IF NOT EXISTS observations (
                source_key TEXT PRIMARY KEY, data TEXT NOT NULL, checked_at TEXT NOT NULL,
                website_status TEXT NOT NULL)""",
        ]
        for statement in statements:
            conn.execute(statement)

    def backup(self, destination=None):
        source = Path(self.db_path)
        if not source.exists():
            raise FileNotFoundError(source)
        if destination is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination = source.parent / "backups" / f"{source.stem}-backup-{stamp}.db"
        target = Path(destination).expanduser().resolve()
        if target == source:
            raise ValueError("Backup destination must differ from the active database")
        if target.exists():
            raise FileExistsError("Backup destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(str(target))) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Backup integrity check failed")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return str(target)

    @staticmethod
    def _decode_lead(row):
        if row is None:
            return None
        result = dict(row)
        for field in LEAD_JSON_FIELDS:
            result[field] = _loads(result.get(field), {})
        for key, value in result.get("extra_data", {}).items():
            result.setdefault(key, value)
        for field in LEAD_BOOL_FIELDS:
            if result.get(field) is not None:
                result[field] = bool(result[field])
        return result

    @classmethod
    def _check_lead_snapshot(cls, row, expected_snapshot, *, invalid_message, changed_message):
        """Compare a complete saved record while the caller holds its write lock."""
        if not isinstance(expected_snapshot, dict):
            raise ValueError(invalid_message)
        try:
            matches_snapshot = _json(expected_snapshot) == _json(cls._decode_lead(row))
        except (TypeError, ValueError) as exc:
            raise ValueError(invalid_message) from exc
        if not matches_snapshot:
            raise ValueError(changed_message)

    @staticmethod
    def _primary_key(data):
        return str(data.get("source_key") or canonical_source_key(
            url=data.get("maps_link") or data.get("source_url") or "",
            place_id=data.get("place_id") or "", name=data.get("name") or "",
            address=data.get("address") or "", country_code=data.get("country_code") or "",
            phone_e164=data.get("phone_e164") or "",
        ) or "")

    @classmethod
    def _source_keys(cls, data):
        keys = {cls._primary_key(data)}
        for url in (data.get("maps_link"), data.get("source_url")):
            if url:
                keys.add(canonical_source_key(url=url))
        if data.get("place_id"):
            keys.add(canonical_source_key(place_id=data["place_id"]))
        return {str(k) for k in keys if k}

    @staticmethod
    def _values(data):
        data = dict(data)
        extras = dict(_loads(data.get("extra_data"), {}))
        extras.update({k: v for k, v in data.items() if k not in LEAD_COLUMNS and not k.startswith("_")})
        data["extra_data"] = extras
        data["identity_name"] = _normal(data.get("name"))
        data["identity_address"] = _normal(data.get("address"))
        result = {k: v for k, v in data.items() if k in LEAD_COLUMNS}
        for field in LEAD_JSON_FIELDS:
            if field in result:
                result[field] = _json(_loads(result[field], {}))
        for field in LEAD_BOOL_FIELDS:
            if field in result and result[field] is not None:
                result[field] = int(bool(result[field]))
        return result

    def _insert_lead(self, conn, data):
        values = self._values(data)
        columns = ",".join(f'"{key}"' for key in values)
        marks = ",".join("?" for _ in values)
        return conn.execute(f"INSERT INTO leads ({columns}) VALUES ({marks})", tuple(values.values())).lastrowid

    def _write_lead(self, conn, lead_id, data):
        values = self._values(data)
        values.pop("id", None)
        assignments = ",".join(f'"{key}"=?' for key in values)
        conn.execute(f"UPDATE leads SET {assignments} WHERE id=?", (*values.values(), lead_id))

    def _remember_sources(self, conn, lead_id, data):
        now = utc_now()
        for key in self._source_keys(data):
            conn.execute("""INSERT INTO lead_sources(lead_id,source_key,source_url,first_seen,last_seen)
                VALUES(?,?,?,?,?) ON CONFLICT(lead_id,source_key) DO UPDATE SET
                last_seen=excluded.last_seen,
                source_url=CASE WHEN excluded.source_url!='' THEN excluded.source_url ELSE lead_sources.source_url END""",
                (lead_id, key, data.get("maps_link") or data.get("source_url") or "", now, now))

    def _mark_legacy_duplicates(self, conn):
        groups = conn.execute("SELECT source_key FROM lead_sources GROUP BY source_key HAVING COUNT(DISTINCT lead_id)>1").fetchall()
        for (key,) in groups:
            conn.execute("UPDATE leads SET legacy_duplicate=1,duplicate_group=? WHERE id IN (SELECT lead_id FROM lead_sources WHERE source_key=?)", (key, key))

    def list_leads(self, include_archived=False):
        sql = "SELECT * FROM leads"
        if not include_archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY COALESCE(updated_at,created_at) DESC,id DESC"
        with self._read() as conn:
            return [self._decode_lead(row) for row in conn.execute(sql)]

    def get_lead(self, lead_id):
        with self._read() as conn:
            return self._decode_lead(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())

    def _compatible_fallback(self, current, incoming):
        old_strong = {k for k in self._source_keys(current) if k.startswith("google:")}
        new_strong = {k for k in self._source_keys(incoming) if k.startswith("google:")}
        if old_strong and new_strong and not old_strong.intersection(new_strong):
            return False
        old_address, new_address = _normal(current.get("address")), _normal(incoming.get("address"))
        if old_address and new_address and old_address != new_address:
            return False
        return True

    def _find_existing(self, conn, incoming):
        direct_ids = set()
        for key in self._source_keys(incoming):
            for row in conn.execute("SELECT l.* FROM leads l JOIN lead_sources s ON l.id=s.lead_id WHERE s.source_key=?", (key,)):
                current = self._decode_lead(row)
                if key.startswith(("fallback:", "phone:")) and not self._compatible_fallback(current, incoming):
                    continue
                direct_ids.add(current["id"])
        if direct_ids:
            # Legacy duplicate rows remain separate; an observed stable source
            # refreshes the earliest preserved ID without changing other CRM rows.
            return min(direct_ids)
        name, address, country = _normal(incoming.get("name")), _normal(incoming.get("address")), incoming.get("country_code")
        if not name or not country:
            return None
        candidates = []
        if address:
            candidates = conn.execute("SELECT * FROM leads WHERE identity_name=? AND identity_address=? AND country_code=?", (name, address, country)).fetchall()
        elif incoming.get("phone_valid") and incoming.get("phone_e164"):
            # A shared switchboard is not evidence that different branches are
            # identical. Use phone fallback only when both lack an address.
            candidates = conn.execute("SELECT * FROM leads WHERE identity_name=? AND phone_e164=? AND country_code=? AND identity_address=''", (name, incoming["phone_e164"], country)).fetchall()
        eligible = [row["id"] for row in candidates if self._compatible_fallback(self._decode_lead(row), incoming)]
        return eligible[0] if len(eligible) == 1 else None

    def find_lead_by_source_key(self, source_key):
        """Read-only convenience for workers deciding whether refresh is needed."""
        with self._read() as conn:
            row = conn.execute("SELECT l.* FROM leads l JOIN lead_sources s ON s.lead_id=l.id WHERE s.source_key=? ORDER BY l.id LIMIT 1", (source_key,)).fetchone()
            return self._decode_lead(row)

    def save_lead(self, data, job_id=None, campaign_id=None, query=None, worker_token=None):
        data = dict(data)
        data.pop("id", None)
        if not str(data.get("name") or "").strip():
            raise ValueError("A business name is required")
        prepared = prepare_lead(data, self.config)
        prepared["source_key"] = self._primary_key(prepared) or "manual:" + uuid.uuid4().hex
        now = utc_now()
        with self._write() as conn:
            if job_id is not None:
                job = self._assert_owner(conn, job_id, worker_token) if worker_token else self._require_job(conn, job_id)
                campaign_id = campaign_id if campaign_id is not None else job["campaign_id"]
            if campaign_id is not None:
                self._require_campaign(conn, campaign_id)
            lead_id = self._find_existing(conn, prepared)
            created = lead_id is None
            if created:
                prepared.setdefault("first_seen", now)
                prepared.setdefault("created_at", now)
                prepared.setdefault("crm_status", "New")
                prepared.setdefault("notes", "")
                prepared["updated_at"] = now
                prepared["observed_at"] = data.get("observed_at") or now
                prepared["last_observed_at"] = prepared["observed_at"]
                lead_id = self._insert_lead(conn, prepared)
            else:
                current = self._decode_lead(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())
                merged = dict(current)
                for field in OBSERVED_FIELDS:
                    if field not in data or field in CRM_FIELDS:
                        continue
                    value = data[field]
                    if field == "niche" and current.get("niche"):
                        continue
                    if field in {"website_url", "website_status", "website_type", "website_checked", "website_extraction_ok", "extraction_ok", "website_verified"} and prepared.get("website_status") in {"unknown", "error"}:
                        continue
                    if field in {"phone", "phone_raw"} and current.get("phone_valid") and not prepared.get("phone_valid"):
                        continue
                    if value is None or value == "":
                        if field == "website_url" and prepared.get("website_status") == "confirmed_absent":
                            merged[field] = ""
                        continue
                    if field in {"country", "country_code"} and not prepared.get("country_code"):
                        continue
                    merged[field] = value
                if (data.get("phone") or data.get("phone_raw")) and (prepared.get("phone_valid") or not current.get("phone_valid")):
                    merged["phone_raw"] = data.get("phone_raw") or data.get("phone")
                if prepared.get("website_status") in {"found", "confirmed_absent"}:
                    merged["website_status"] = prepared["website_status"]
                    merged["website_url"] = prepared.get("website_url") or ""
                    merged["website_extraction_ok"] = True
                    if prepared["website_status"] == "confirmed_absent":
                        merged["website"] = ""
                if prepared["source_key"].startswith("google:"):
                    merged["source_key"] = prepared["source_key"]
                merged.update({"legacy_unverified": False, "observation_status": "observed", "updated_at": now,
                    "observed_at": data.get("observed_at") or now, "last_observed_at": data.get("observed_at") or now})
                refreshed = prepare_lead(merged, self.config)
                for field in CRM_FIELDS:
                    refreshed[field] = current.get(field)
                refreshed["legacy_data"] = current.get("legacy_data") or {}
                refreshed["legacy_priority"] = current.get("legacy_priority")
                self._write_lead(conn, lead_id, refreshed)
            self._remember_sources(conn, lead_id, prepared)
            if job_id is not None or campaign_id is not None:
                query_text = _json(query) if isinstance(query, (dict, list)) else str(query or "")
                conn.execute("""INSERT INTO lead_discoveries(lead_id,job_id,campaign_id,query,is_new,seen_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT DO UPDATE SET seen_at=excluded.seen_at""",
                    (lead_id, job_id, campaign_id, query_text, int(created), now))
            return lead_id, created

    def _validate_changes(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("Lead changes must be an object")
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError("Fields are not editable: " + ", ".join(sorted(unknown)))
        result = dict(changes)
        statuses = self.config.get("crm_statuses") or ["New", "Contacted", "Interested", "Quoted", "Won", "Lost"]
        for field, value in list(result.items()):
            if field in {"archived", "do_not_contact", "contacted", "establishment_verified", "business_since_verified"}:
                result[field] = _boolean(value, field)
            elif field == "crm_status":
                if value not in statuses:
                    raise ValueError("Unknown CRM status")
            elif field == "manual_priority":
                value = str(value or "").strip()
                if value not in {"", "Hot", "Warm", "Cold"}:
                    raise ValueError("Manual priority must be Hot, Warm, Cold or blank")
                result[field] = value
            elif field in {"rating", "deal_value"}:
                if value in ("", None) and field == "rating":
                    result[field] = None
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{field} must be a number") from None
                if not math.isfinite(number) or number < 0 or (field == "rating" and number > 5):
                    raise ValueError(f"{field} is outside the permitted range")
                result[field] = number
            elif field in {"reviews_count", "established_year"}:
                result[field] = None if value in ("", None) else _integer(value, field)
            elif field == "next_followup":
                if value in (None, ""):
                    result[field] = None
                else:
                    value = str(value)
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError("Follow-up date must be YYYY-MM-DD")
                    date.fromisoformat(value)
                    result[field] = value
            elif field in {"last_contacted", "enrichment_checked_at", "email_checked_at"}:
                if value in (None, ""):
                    result[field] = None
                elif _when(value) is None:
                    raise ValueError(f"{field} must be an ISO date or timestamp")
                else:
                    result[field] = str(value)
            elif field == "website_status":
                if value not in {"found", "confirmed_absent", "unknown", "error"}:
                    raise ValueError("Unknown website status")
            elif field in {"website_url", "maps_link", "source_url", "fb_link", "instagram_link", "contact_page", "email_source_url"}:
                result[field] = _safe_url(value, field)
            elif field == "email":
                value = str(value or "").strip()
                if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                    raise ValueError("Enter a valid email address or leave it blank")
                result[field] = value
            elif field == "website_audit":
                if not isinstance(value, dict):
                    raise ValueError("website_audit must be an object")
                _json(value)
            elif field in {"emails", "booking_links", "enquiry_links"}:
                if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                    raise ValueError(f"{field} must be a list of strings")
            else:
                result[field] = "" if value is None else str(value)
        if "name" in result and not result["name"].strip():
            raise ValueError("A business name is required")
        if "country" in result or "country_code" in result:
            country_value = result.get("country") if "country" in result else result.get("country_code")
            info = country_info(country_value)
            if country_value and not info.get("country_code"):
                raise ValueError("Choose a supported country name or ISO code")
            result.update(info)
            result["country_raw"] = str(country_value or "")
        return result

    def _apply_changes(self, conn, lead_id, changes):
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if row is None:
            raise KeyError(f"Lead {lead_id} does not exist")
        current = self._decode_lead(row)
        actual = {k: v for k, v in changes.items() if current.get(k) != v}
        if not actual:
            return current
        merged = {**current, **actual}
        if "phone" in changes or "phone_raw" in changes:
            raw = changes.get("phone_raw", changes.get("phone", ""))
            merged["phone"] = raw
            merged["phone_raw"] = raw
            merged["phone_e164"] = ""
            merged["phone_valid"] = False
        if "website_url" in actual and "website_status" not in actual:
            merged["website_status"] = "found" if actual["website_url"] else "unknown"
        if "website_url" in actual:
            merged["website"] = ""
        if "website_url" in actual or "website_status" in actual:
            merged["website_verified"] = True
            if not merged.get("legacy_unverified"):
                merged["observation_status"] = "user_verified"
            if merged.get("website_status") == "confirmed_absent":
                merged["website_url"] = ""
                merged["website"] = ""
            merged["website_extraction_ok"] = merged.get("website_status") != "error"
            merged["website_checked"] = merged.get("website_status") in {"found", "confirmed_absent"}
        if "archived" in actual:
            merged["archived_at"] = utc_now() if actual["archived"] else None
        merged["updated_at"] = utc_now()
        # Source IDs remain stable; identity based only on mutable fallback
        # attributes is recalculated after user corrections.
        if str(current.get("source_key") or "").startswith(("fallback:", "phone:", "manual:", "legacy:")):
            merged["source_key"] = ""
        source_changed = bool({"maps_link", "source_url"}.intersection(actual))
        if source_changed:
            merged["source_key"] = ""
            merged["place_id"] = ""
            conn.execute("DELETE FROM lead_sources WHERE lead_id=?", (lead_id,))
        prepared = prepare_lead(merged, self.config)
        prepared["source_key"] = self._primary_key(prepared) or current["source_key"]
        self._write_lead(conn, lead_id, prepared)
        self._remember_sources(conn, lead_id, prepared)
        details = {key: {"before": current.get(key), "after": value} for key, value in actual.items()}
        self._activity(conn, lead_id, "field_change", "Updated " + ", ".join(sorted(actual)), details=details)
        return self._decode_lead(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())

    def update_lead(self, lead_id, changes, *, expected_snapshot=None):
        """Apply validated changes, optionally requiring an exact saved snapshot.

        Pass the complete dict returned by get_lead to reject intervening edits,
        including edits within the same updated_at second. The snapshot check
        and update share one immediate transaction. Missing, invalid or stale
        guarded records raise ValueError without applying any changes; callers
        can keep their draft for review. Omitting the guard preserves the
        existing unconditional update behavior.
        """
        changes = self._validate_changes(changes)
        with self._write() as conn:
            if expected_snapshot is not None:
                current = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
                if current is None:
                    raise ValueError("This lead no longer exists. Your edits were not applied.")
                self._check_lead_snapshot(current, expected_snapshot,
                    invalid_message="The saved lead snapshot is invalid. Refresh and review your edits before saving.",
                    changed_message="This lead changed before your edits were saved. Refresh and review your draft before saving again.")
            return self._apply_changes(conn, lead_id, changes)

    def delete_lead(self, lead_id, *, expected_updated_at=None, expected_snapshot=None):
        """Permanently remove one workspace lead and its dependent lead records.

        The check and deletion share an immediate transaction, preventing a new
        collection worker from claiming a job between them. All active/owned
        jobs block deletion: workers cache identities for the whole workspace.
        Unfinished candidates tied to the lead also block deletion until their
        work is resolved. Archive remains available for those records.

        Completed candidate outcomes, job logs/checkpoints and cumulative job
        totals remain historical evidence. Their lead references are detached;
        this lead's activities, sources and campaign discoveries are removed.
        Only observation keys not shared with another lead are purged. Existing
        backups and the unused legacy cache are not altered. A new search may
        collect the business again after deletion.

        When ``expected_updated_at`` is not None it must exactly match the saved
        value. ``expected_snapshot`` accepts the complete dict returned by
        get_lead; its canonical value must match the current record, including
        changes made within the same timestamp second. Omit both guards for an
        explicitly unconditional deletion of the given ID.
        Returns the deleted ID/name; invalid, missing, stale or unsafe requests
        raise ValueError without committing any changes.
        """
        lead_id = _integer(lead_id, "lead ID", 1)
        identity_query = """SELECT source_key FROM lead_sources WHERE lead_id=?
            UNION SELECT source_key FROM leads WHERE id=?
            UNION SELECT source_key FROM job_candidates WHERE lead_id=?"""
        identity_values = (lead_id, lead_id, lead_id)
        with self._write() as conn:
            lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if lead is None:
                raise ValueError("This lead no longer exists. Refresh the table.")
            if expected_updated_at is not None and lead["updated_at"] != expected_updated_at:
                raise ValueError("This lead changed after you opened it. Refresh and review it before deleting.")
            if expected_snapshot is not None:
                self._check_lead_snapshot(lead, expected_snapshot,
                    invalid_message="The delete confirmation is invalid. Open it again from the saved lead.",
                    changed_message="This lead changed after you opened the delete confirmation. Refresh and review it before deleting.")
            active = conn.execute("""SELECT id FROM jobs
                WHERE state IN ('running','pause_requested','cancel_requested')
                   OR (worker_token IS NOT NULL AND worker_token!='')
                ORDER BY id LIMIT 1""").fetchone()
            if active is not None:
                raise ValueError(
                    f"Search job #{active['id']} is still active or owned by a worker. "
                    "Pause or cancel collection and wait for the worker to finish before deleting a lead."
                )
            unfinished = conn.execute(f"""SELECT job_id FROM job_candidates
                WHERE state IN ('pending','error') AND
                    (lead_id=? OR (lead_id IS NULL AND source_key!='' AND source_key IN ({identity_query})))
                ORDER BY job_id LIMIT 1""", (lead_id, *identity_values)).fetchone()
            if unfinished is not None:
                raise ValueError(
                    f"Unfinished candidate work in job #{unfinished['job_id']} still refers to this lead. "
                    "Finish that work or archive the lead instead."
                )

            job_ids = [row[0] for row in conn.execute("""
                SELECT job_id FROM lead_discoveries WHERE lead_id=? AND job_id IS NOT NULL
                UNION SELECT job_id FROM job_candidates WHERE lead_id=?""", (lead_id, lead_id))]
            for job_id in job_ids:
                job = self._require_job(conn, job_id)
                progress = _loads(job["progress"], {})
                if not isinstance(progress, dict):
                    raise ValueError(f"Job #{job_id} has invalid saved progress. Its lead cannot be deleted safely.")
                # A worker can stop after saving a discovery but before its
                # checkpoint. Reconcile genuine committed counts before the
                # discovery is removed, using the same max rule as run_job.
                committed = conn.execute("""SELECT
                    COUNT(DISTINCT CASE WHEN is_new=1 THEN lead_id END) AS saved,
                    COUNT(DISTINCT CASE WHEN is_new=0 THEN lead_id END) AS refreshed
                    FROM lead_discoveries WHERE job_id=?""", (job_id,)).fetchone()
                for counter in ("saved", "refreshed"):
                    progress[counter] = max(
                        _integer(progress.get(counter, 0) or 0, f"Job #{job_id} {counter}"),
                        int(committed[counter]),
                    )
                conn.execute("UPDATE jobs SET progress=?,updated_at=? WHERE id=?", (_json(progress), utc_now(), job_id))
                self._job_log(conn, job_id, "info",
                    f"Lead #{lead_id} was permanently deleted from the workspace. Historical job totals were retained.")

            # Keep a shared cache observation when another lead or its candidate
            # history still owns the same identity (including imported duplicates).
            conn.execute(f"""DELETE FROM observations
                WHERE source_key!='' AND source_key IN ({identity_query})
                  AND NOT EXISTS (SELECT 1 FROM lead_sources s
                      WHERE s.source_key=observations.source_key AND s.lead_id!=?)
                  AND NOT EXISTS (SELECT 1 FROM leads l
                      WHERE l.source_key=observations.source_key AND l.id!=?)
                  AND NOT EXISTS (SELECT 1 FROM job_candidates c
                      WHERE c.source_key=observations.source_key AND c.lead_id IS NOT NULL AND c.lead_id!=?)""",
                (*identity_values, lead_id, lead_id, lead_id))
            conn.execute("UPDATE job_candidates SET lead_id=NULL,updated_at=? WHERE lead_id=?", (utc_now(), lead_id))
            for table in ("activities", "lead_sources", "lead_discoveries"):
                conn.execute(f"DELETE FROM {table} WHERE lead_id=?", (lead_id,))
            removed = conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
            if removed.rowcount != 1:
                raise ValueError("The selected lead could not be deleted. Refresh the table.")
            return {"id": lead_id, "name": lead["name"]}

    @staticmethod
    def _check_clear_all_leads(conn, expected_count):
        """Guard mass deletion against active collection and AI tasks.

        Call under a read transaction for preview, and under BEGIN IMMEDIATE
        immediately before deleting, so a concurrent writer cannot pass the
        count check and then silently add or change leads.
        """
        count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        if count != expected_count:
            raise ValueError("The number of leads changed. Close this dialog, refresh and confirm again.")
        if not count:
            raise ValueError("There are no leads to clear.")
        active = conn.execute("""SELECT id FROM jobs
            WHERE state IN ('queued','running','pause_requested','cancel_requested')
                OR (worker_token IS NOT NULL AND worker_token!='')
            LIMIT 1""").fetchone()
        if active:
            raise ValueError(
                f"Search job #{active['id']} is queued or active. Finish or cancel queued/running jobs before clearing leads."
            )
        ai_tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ai_%'"
        )}
        if {'ai_workers', 'ai_assignments'} <= ai_tables:
            active_worker = conn.execute("""SELECT w.name FROM ai_workers w
                JOIN ai_assignments a ON a.worker_id=w.id
                WHERE w.enabled=1 LIMIT 1""").fetchone()
            if active_worker:
                raise ValueError(
                    "Disable AI replies for all workers with assigned leads before clearing: "
                    + str(active_worker['name'])
                )
        if 'ai_threads' in ai_tables:
            busy_thread = conn.execute("""SELECT lead_id FROM ai_threads
                WHERE in_progress=1 AND lease_at>? LIMIT 1""",
                (__import__('time').time()-180,)).fetchone()
            if busy_thread:
                raise ValueError("An AI conversation is being processed. Stop AI workers and try again.")
        if 'ai_messages' in ai_tables:
            sending = conn.execute("""SELECT id FROM ai_messages
                WHERE lead_id IS NOT NULL AND direction='out' AND status='sending'
                LIMIT 1""").fetchone()
            if sending:
                raise ValueError("A lead message is still sending. Stop the AI sender before clearing leads.")
        return ai_tables

    def check_clear_all_leads(self, expected_count):
        """Read-only preflight for the Leads page before it creates a backup."""
        expected_count = _integer(expected_count, 'expected lead count', 1)
        with self._read() as conn:
            self._check_clear_all_leads(conn, expected_count)
        return expected_count

    def clear_all_leads(self, *, expected_count):
        """Clear ALL leads and their linked CRM/AI data in one transaction.

        Preserve independent worker accounts/keys, queue batches, global opt-out
        ledger, campaigns, saved jobs and aggregate AI token accounting. The UI
        is responsible for taking a backup and transferring lead opt-outs to
        the installation-wide ledger before calling this destructive method.
        """
        expected_count = _integer(expected_count, 'expected lead count', 1)
        with self._write() as conn:
            ai_tables = self._check_clear_all_leads(conn, expected_count)
            # Retain historical jobs/candidates, but do not leave stale lead IDs.
            conn.execute("UPDATE job_candidates SET lead_id=NULL,updated_at=? WHERE lead_id IS NOT NULL", (utc_now(),))
            for table in ('activities', 'lead_sources', 'lead_discoveries'):
                conn.execute(f'DELETE FROM {table}')
            if 'ai_messages' in ai_tables:
                # ai_messages has ON DELETE SET NULL; delete linked conversations
                # explicitly BEFORE leads so they do not become unmatched history.
                conn.execute('DELETE FROM ai_messages WHERE lead_id IS NOT NULL')
            if 'ai_first_contact_attempts' in ai_tables:
                conn.execute('DELETE FROM ai_first_contact_attempts WHERE lead_id IN (SELECT id FROM leads)')
            if 'ai_usage' in ai_tables:
                # Keep Gemini cost accounting intact, but remove deleted lead IDs.
                conn.execute('UPDATE ai_usage SET lead_id=NULL WHERE lead_id IS NOT NULL')
            removed = conn.execute('DELETE FROM leads')
            if removed.rowcount != expected_count:
                raise RuntimeError('Lead deletion count did not match the confirmed count; all changes were rolled back.')
            problems = conn.execute('PRAGMA foreign_key_check').fetchall()
            if problems:
                raise RuntimeError('Database foreign-key validation failed; the deletion was rolled back.')
            return removed.rowcount

    def bulk_update(self, ids, changes):
        changes = self._validate_changes(changes)
        ids = list(dict.fromkeys(_integer(value, "lead ID", 1) for value in ids))
        with self._write() as conn:
            count = 0
            for lead_id in ids:
                self._apply_changes(conn, lead_id, changes)
                count += 1
            return count

    def _activity(self, conn, lead_id, kind, text, happened_at=None, details=None):
        if conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone() is None:
            raise KeyError(f"Lead {lead_id} does not exist")
        happened_at = happened_at or utc_now()
        if _when(happened_at) is None:
            raise ValueError("Activity date must be an ISO date or timestamp")
        return conn.execute("INSERT INTO activities(lead_id,kind,text,happened_at,created_at,details) VALUES(?,?,?,?,?,?)",
            (lead_id, str(kind), str(text), happened_at, utc_now(), _json(details or {}))).lastrowid

    def add_activity(self, lead_id, kind, text, happened_at=None):
        if not str(kind).strip() or not str(text).strip():
            raise ValueError("Activity type and text are required")
        with self._write() as conn:
            return self._activity(conn, lead_id, kind, text, happened_at)

    def list_activities(self, lead_id):
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM activities WHERE lead_id=? ORDER BY happened_at DESC,id DESC", (lead_id,))
            return [{**dict(row), "details": _loads(row["details"], {})} for row in rows]

    @staticmethod
    def _decode_campaign(row):
        if row is None:
            return None
        result = dict(row)
        for field in ("locations", "niches"):
            result[field] = _loads(result[field], [])
        result["settings"] = _loads(result["settings"], {})
        return result

    @staticmethod
    def _require_campaign(conn, campaign_id):
        row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row is None:
            raise KeyError(f"Campaign {campaign_id} does not exist")
        return row

    def list_campaigns(self):
        with self._read() as conn:
            return [self._decode_campaign(row) for row in conn.execute("SELECT * FROM campaigns ORDER BY updated_at DESC,id DESC")]

    def get_campaign(self, campaign_id):
        with self._read() as conn:
            return self._decode_campaign(conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone())

    def save_campaign(self, data):
        data = dict(data)
        with self._write() as conn:
            current = self._decode_campaign(self._require_campaign(conn, data["id"])) if data.get("id") else {}
            merged = {**current, **data}
            name = str(merged.get("name") or "").strip()
            if not name:
                raise ValueError("A campaign name is required")
            info = country_info(merged.get("country_code") or merged.get("country") or "")
            locations, niches = merged.get("locations", []), merged.get("niches", [])
            if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in (locations, niches)):
                raise ValueError("Campaign locations and niches must be lists of strings")
            locations = list(dict.fromkeys(x.strip() for x in locations if x.strip()))
            niches = list(dict.fromkeys(x.strip() for x in niches if x.strip()))
            settings = merged.get("settings") or {}
            if not isinstance(settings, dict):
                raise ValueError("Campaign settings must be an object")
            values = (name, info.get("country", ""), info.get("country_code", ""), _json(locations), _json(niches), _json(settings), utc_now())
            if current:
                conn.execute("UPDATE campaigns SET name=?,country=?,country_code=?,locations=?,niches=?,settings=?,updated_at=? WHERE id=?", (*values, data["id"]))
                return data["id"]
            return conn.execute("INSERT INTO campaigns(name,country,country_code,locations,niches,settings,updated_at,created_at) VALUES(?,?,?,?,?,?,?,?)", (*values, utc_now())).lastrowid

    @staticmethod
    def _decode_job(row):
        if row is None:
            return None
        result = dict(row)
        result["queries"] = _loads(result.get("queries"), [])
        for field in ("settings", "progress", "checkpoint"):
            result[field] = _loads(result.get(field), {})
        return result

    @staticmethod
    def _require_job(conn, job_id):
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Job {job_id} does not exist")
        return row

    def create_job(self, campaign_id=None, queries=None, settings=None):
        with self._write() as conn:
            campaign = self._decode_campaign(self._require_campaign(conn, campaign_id)) if campaign_id is not None else {}
            if queries is None:
                queries = [{"city": city, "niche": niche, "country": campaign.get("country", ""), "country_code": campaign.get("country_code", "")}
                    for city in campaign.get("locations", []) for niche in campaign.get("niches", [])]
            if not isinstance(queries, list) or not queries or len(queries) > 10000:
                raise ValueError("A job needs between 1 and 10,000 queries")
            clean_queries = []
            for query in queries:
                if not isinstance(query, dict) or not str(query.get("city") or "").strip() or not str(query.get("niche") or "").strip():
                    raise ValueError("Every query needs a city and niche")
                info = country_info(query.get("country_code") or query.get("country") or campaign.get("country_code") or "")
                if not info.get("country_code"):
                    raise ValueError("Every query needs a supported country")
                clean_queries.append({**query, **info, "city": str(query["city"]).strip(), "niche": str(query["niche"]).strip()})
            if settings is not None and not isinstance(settings, dict):
                raise ValueError("Job settings must be an object")
            merged_settings = {**self.config.get("search_defaults", {}), **campaign.get("settings", {}), **(settings or {})}
            now = utc_now()
            return conn.execute("""INSERT INTO jobs(campaign_id,state,queries,settings,progress,checkpoint,created_at,updated_at)
                VALUES(?,'queued',?,?,?,'{}',?,?)""",
                (campaign_id, _json(clean_queries), _json(merged_settings), _json(dict.fromkeys(PROGRESS_KEYS, 0)), now, now)).lastrowid

    def list_jobs(self, limit=100):
        limit = min(_integer(limit, "limit", 1), 10000)
        with self._read() as conn:
            return [self._decode_job(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]

    def get_job(self, job_id):
        with self._read() as conn:
            return self._decode_job(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def _assert_owner(self, conn, job_id, worker_token):
        job = self._require_job(conn, job_id)
        touched = _when(job["heartbeat_at"] or job["started_at"] or job["created_at"])
        expired = touched is None or (datetime.now(timezone.utc) - touched).total_seconds() > self.lease_seconds
        if not worker_token or job["worker_token"] != worker_token or job["state"] not in ACTIVE_STATES or expired:
            raise LeaseLostError(f"Worker no longer owns job {job_id}")
        return job

    def _recover_stale(self, conn, seconds):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        recovered = 0
        for row in conn.execute("SELECT * FROM jobs WHERE state IN ('running','pause_requested','cancel_requested')").fetchall():
            heartbeat = _when(row["heartbeat_at"] or row["started_at"] or row["created_at"])
            if heartbeat is not None and heartbeat >= cutoff:
                continue
            cancelled = row["state"] == "cancel_requested"
            target = "cancelled" if cancelled else "paused"
            reason = "cancel_requested" if cancelled else "worker_stale"
            message = "Cancelled after the worker stopped responding." if cancelled else "Worker heartbeat expired. Saved candidates and progress are ready to resume."
            conn.execute("UPDATE jobs SET state=?,worker_token=NULL,reason=?,finished_at=?,updated_at=? WHERE id=?",
                (target, reason, utc_now() if cancelled else None, utc_now(), row["id"]))
            self._job_log(conn, row["id"], "warning", message)
            recovered += 1
        return recovered

    def recover_stale_jobs(self, seconds=180):
        seconds = _integer(seconds, "lease timeout", 1)
        with self._write() as conn:
            return self._recover_stale(conn, seconds)

    def claim_next_job(self, worker_token):
        if not str(worker_token or "").strip():
            raise ValueError("A worker token is required")
        with self._write() as conn:
            self._recover_stale(conn, self.lease_seconds)
            if conn.execute("SELECT 1 FROM jobs WHERE state IN ('running','pause_requested','cancel_requested') LIMIT 1").fetchone():
                return None
            row = conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            now = utc_now()
            conn.execute("UPDATE jobs SET state='running',worker_token=?,heartbeat_at=?,started_at=COALESCE(started_at,?),finished_at=NULL,reason=NULL,error=NULL,updated_at=? WHERE id=?", (worker_token, now, now, now, row["id"]))
            self._job_log(conn, row["id"], "info", "Worker started or resumed this job.")
            return self._decode_job(self._require_job(conn, row["id"]))

    def heartbeat_job(self, job_id, worker_token):
        with self._write() as conn:
            self._assert_owner(conn, job_id, worker_token)
            now = utc_now()
            conn.execute("UPDATE jobs SET heartbeat_at=?,updated_at=? WHERE id=?", (now, now, job_id))
        return True

    def update_job(self, job_id, **fields):
        expected_token = fields.pop("worker_token", None)
        allowed = {"state", "progress", "checkpoint", "reason", "error", "started_at", "finished_at", "heartbeat_at", "settings"}
        if set(fields) - allowed:
            raise ValueError("Unsupported job fields: " + ", ".join(sorted(set(fields) - allowed)))
        with self._write() as conn:
            current = self._require_job(conn, job_id)
            if expected_token is not None:
                self._assert_owner(conn, job_id, expected_token)
            elif current["state"] in ACTIVE_STATES:
                raise LeaseLostError("Worker token required to update an active job")
            values = dict(fields)
            state = values.get("state", current["state"])
            if state not in JOB_STATES:
                raise ValueError("Unknown job state")
            if current["state"] not in ACTIVE_STATES and state in ACTIVE_STATES:
                raise ValueError("Use claim_next_job to start a job")
            if state == "queued" and current["state"] != "queued":
                raise ValueError("Use request_job_action to resume a job")
            if current["state"] == "pause_requested" and state == "running":
                values["state"] = "pause_requested"
            if current["state"] == "cancel_requested" and state in {"running", "pause_requested"}:
                values["state"] = "cancel_requested"
            if current["state"] == "cancel_requested" and state in TERMINAL_STATES | {"paused"}:
                values["state"] = "cancelled"
                values["reason"] = "cancel_requested"
            for key in ("progress", "checkpoint", "settings"):
                if key in values:
                    if not isinstance(values[key], dict):
                        raise ValueError(f"Job {key} must be an object")
                    combined = {**_loads(current[key], {}), **values[key]}
                    if key == "progress":
                        for counter, value in combined.items():
                            combined[counter] = _integer(value, counter)
                    values[key] = _json(combined)
            final_state = values.get("state", current["state"])
            if final_state not in ACTIVE_STATES:
                values["worker_token"] = None
            if final_state in TERMINAL_STATES and "finished_at" not in values:
                values["finished_at"] = utc_now()
            values["updated_at"] = utc_now()
            sql = ",".join(f'"{key}"=?' for key in values)
            conn.execute(f"UPDATE jobs SET {sql} WHERE id=?", (*values.values(), job_id))
            return self._decode_job(self._require_job(conn, job_id))

    def request_job_action(self, job_id, action):
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError("Job action must be pause, resume or cancel")
        with self._write() as conn:
            current = self._require_job(conn, job_id)
            state = current["state"]
            if action == "pause":
                if state == "queued":
                    target = "paused"
                elif state in {"running", "pause_requested"}:
                    target = "pause_requested"
                elif state == "paused":
                    return self._decode_job(current)
                else:
                    raise ValueError("This job cannot be paused")
            elif action == "resume":
                if state == "queued":
                    return self._decode_job(current)
                if state not in {"paused", "cancelled", "failed", "partial"}:
                    raise ValueError("Only paused, cancelled, failed or partial jobs can resume")
                target = "queued"
            else:
                if state == "cancelled":
                    return self._decode_job(current)
                if state == "completed":
                    raise ValueError("A completed job cannot be cancelled")
                target = "cancel_requested" if state in ACTIVE_STATES else "cancelled"
            now = utc_now()
            token = current["worker_token"] if target in ACTIVE_STATES else None
            finished = now if target == "cancelled" else None
            reason = {"pause": "Pause requested by user.", "resume": "Resume requested by user.", "cancel": "Cancel requested by user."}[action]
            conn.execute("UPDATE jobs SET state=?,worker_token=?,finished_at=?,reason=?,updated_at=? WHERE id=?", (target, token, finished, reason, now, job_id))
            self._job_log(conn, job_id, "info", reason)
            return self._decode_job(self._require_job(conn, job_id))

    @staticmethod
    def _job_log(conn, job_id, level, message):
        return conn.execute("INSERT INTO job_logs(job_id,level,message,created_at) VALUES(?,?,?,?)", (job_id, str(level).lower(), str(message), utc_now())).lastrowid

    def add_job_log(self, job_id, level, message, worker_token=None):
        with self._write() as conn:
            if worker_token is not None:
                self._assert_owner(conn, job_id, worker_token)
            else:
                self._require_job(conn, job_id)
            return self._job_log(conn, job_id, level, message)

    def get_job_logs(self, job_id, limit=100):
        limit = min(_integer(limit, "limit", 1), 10000)
        with self._read() as conn:
            rows = list(conn.execute("SELECT * FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit)))
            return [dict(row) for row in reversed(rows)]

    def upsert_candidate(self, job_id, source_key, url, preview_name="", query_index=0, worker_token=None):
        if not str(source_key or "").strip():
            raise ValueError("Candidate source key is required")
        query_index = _integer(query_index, "query index")
        with self._write() as conn:
            if worker_token is not None:
                self._assert_owner(conn, job_id, worker_token)
            else:
                self._require_job(conn, job_id)
            now = utc_now()
            conn.execute("""INSERT INTO job_candidates(job_id,source_key,url,preview_name,query_index,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id,source_key) DO UPDATE SET
                url=CASE WHEN excluded.url!='' THEN excluded.url ELSE job_candidates.url END,
                preview_name=CASE WHEN excluded.preview_name!='' THEN excluded.preview_name ELSE job_candidates.preview_name END,
                updated_at=excluded.updated_at""", (job_id, source_key, str(url or ""), str(preview_name or ""), query_index, now, now))
            return dict(conn.execute("SELECT * FROM job_candidates WHERE job_id=? AND source_key=?", (job_id, source_key)).fetchone())

    def list_candidates(self, job_id, state=None, query_index=None):
        clauses, values = ["job_id=?"], [job_id]
        if state is not None:
            if state not in {"pending", "done", "excluded", "error"}:
                raise ValueError("Unknown candidate state")
            clauses.append("state=?")
            values.append(state)
        if query_index is not None:
            clauses.append("query_index=?")
            values.append(_integer(query_index, "query index"))
        with self._read() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM job_candidates WHERE " + " AND ".join(clauses) + " ORDER BY id", values)]

    def update_candidate(self, candidate_id, **fields):
        token = fields.pop("worker_token", None)
        if set(fields) - {"state", "attempts", "error", "lead_id", "outcome"}:
            raise ValueError("Unsupported candidate fields")
        if "state" in fields and fields["state"] not in {"pending", "done", "excluded", "error"}:
            raise ValueError("Unknown candidate state")
        if "attempts" in fields:
            fields["attempts"] = _integer(fields["attempts"], "attempts")
        with self._write() as conn:
            row = conn.execute("SELECT * FROM job_candidates WHERE id=?", (candidate_id,)).fetchone()
            if row is None:
                raise KeyError(f"Candidate {candidate_id} does not exist")
            if token is not None:
                self._assert_owner(conn, row["job_id"], token)
            fields["updated_at"] = utc_now()
            sql = ",".join(f'"{key}"=?' for key in fields)
            conn.execute(f"UPDATE job_candidates SET {sql} WHERE id=?", (*fields.values(), candidate_id))
            return dict(conn.execute("SELECT * FROM job_candidates WHERE id=?", (candidate_id,)).fetchone())

    def cache_observation(self, source_key, data, job_id=None, worker_token=None):
        if not source_key:
            raise ValueError("Observation source key is required")
        # Cache facts, never pipeline state, scores, exclusions or assumptions.
        allowed = OBSERVED_FIELDS | {"phone_e164", "phone_valid", "phone_type", "review_age_is_sample"}
        normalized = prepare_lead(dict(data), self.config)
        observation = {key: value for key, value in normalized.items() if key in allowed}
        status = observation.get("website_status")
        if status not in {"found", "confirmed_absent"}:
            return False
        checked_at = observation.get("observed_at") or data.get("cached_at") or utc_now()
        if _when(checked_at) is None:
            raise ValueError("Observation timestamp must be ISO formatted")
        with self._write() as conn:
            if worker_token is not None:
                if job_id is None:
                    raise ValueError("Job ID is required with a worker token")
                self._assert_owner(conn, job_id, worker_token)
            conn.execute("""INSERT INTO observations(source_key,data,checked_at,website_status) VALUES(?,?,?,?)
                ON CONFLICT(source_key) DO UPDATE SET data=excluded.data,checked_at=excluded.checked_at,website_status=excluded.website_status""",
                (source_key, _json(observation), checked_at, status))
        return True

    def get_cached_observation(self, source_key, max_age_hours=168):
        try:
            hours = float(max_age_hours)
        except (TypeError, ValueError):
            raise ValueError("Cache age must be a nonnegative number") from None
        if not math.isfinite(hours) or hours < 0:
            raise ValueError("Cache age must be a nonnegative number")
        if hours == 0:
            return None
        with self._read() as conn:
            row = conn.execute("SELECT * FROM observations WHERE source_key=?", (source_key,)).fetchone()
            if row is None or row["website_status"] not in {"found", "confirmed_absent"}:
                return None
            checked = _when(row["checked_at"])
            if checked is None or checked < datetime.now(timezone.utc) - timedelta(hours=hours):
                return None
            data = _loads(row["data"], {})
            data["cached_at"] = row["checked_at"]
            return data

    def campaign_metrics(self):
        with self._read() as conn:
            results = []
            for campaign in conn.execute("SELECT * FROM campaigns ORDER BY id"):
                jobs = list(conn.execute("SELECT state FROM jobs WHERE campaign_id=?", (campaign["id"],)))
                leads = list(conn.execute("SELECT DISTINCT l.* FROM leads l JOIN lead_discoveries d ON d.lead_id=l.id WHERE d.campaign_id=?", (campaign["id"],)))
                new_leads = conn.execute("SELECT COUNT(DISTINCT lead_id) FROM lead_discoveries WHERE campaign_id=? AND is_new=1", (campaign["id"],)).fetchone()[0]
                statuses = [row["crm_status"] for row in leads]
                metric = {
                    "campaign_id": campaign["id"], "name": campaign["name"],
                    "jobs": len(jobs), "leads": len(leads), "new_leads": new_leads,
                    "completed_jobs": sum(row["state"] == "completed" for row in jobs),
                    "partial_jobs": sum(row["state"] == "partial" for row in jobs),
                    "failed_jobs": sum(row["state"] == "failed" for row in jobs),
                    "contacted": sum(bool(row["contacted"]) or row["crm_status"] in {"Contacted", "Interested", "Quoted", "Won"} for row in leads),
                    "archived": sum(bool(row["archived"]) for row in leads),
                    "do_not_contact": sum(bool(row["do_not_contact"]) for row in leads),
                    "deal_value": sum(float(row["deal_value"] or 0) for row in leads if row["crm_status"] == "Won"),
                    "pipeline_value": sum(float(row["deal_value"] or 0) for row in leads if row["crm_status"] in {"Interested", "Quoted"} and not row["archived"]),
                }
                for status in ("Interested", "Quoted", "Won", "Lost"):
                    metric[status.lower()] = statuses.count(status)
                results.append(metric)
            return results

    def job_discovery_counts(self, job_id):
        """Reconcile progress from committed saves after interrupted checkpoints."""
        with self._read() as conn:
            self._require_job(conn, job_id)
            row = conn.execute("""SELECT
                COUNT(DISTINCT CASE WHEN is_new=1 THEN lead_id END) AS saved,
                COUNT(DISTINCT CASE WHEN is_new=0 THEN lead_id END) AS refreshed
                FROM lead_discoveries WHERE job_id=?""", (job_id,)).fetchone()
            return dict(row)
