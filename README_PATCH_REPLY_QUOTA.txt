MedMH AI Workforce v3.9.1 — Quota visibility and scheduler fairness

ROOT CAUSE EVIDENCE
- Incoming WhatsApp messages already reach and match assigned leads.
- The reply trace explicitly reports daily_limit for an assigned lead.
- The per-worker AI generation request cap is SEPARATE from token / new-contact limits.

CHANGES
- The reply scheduler respects the rolling 24-hour request cap before acquiring a generation lease.
- Each eligible pending lead gets a daily_limit / model_unavailable / token_limit trace when blocked instead of one older lead monopolizing scheduling.
- AI Worker Controls visibly shows requests used versus the shared request limit and how to change it.
- Correct the read-only diagnostic's false WhatsApp DOWN for missing GET /health.
- Add offline regression tests for the quota boundary, fairness, and blocked leads.

TO UNBLOCK REPLIES NOW
AI Workers -> Shared business knowledge and quotas -> Daily generation limit per worker.
Review the usage shown in the Controls tab. If exhausted, select an appropriate
higher generation-request cap and save; otherwise wait for the rolling window.
Do NOT confuse it with Maximum new contacts per UTC day or daily token cap.

FILES: leadfinder/ai_workers.py, leadfinder/ai_ui.py, tools/diagnose_ai_replies.py, tests/test_reply_quota.py, README.md.
DELETIONS: None. DB MIGRATIONS: None. PERSISTENT COMMANDS: None.
NO WHATSAPP SESSIONS, KEYS, LEADS, OR CUSTOMER MESSAGES ARE ALTERED BY THE PATCH.

TESTING
1. Stop app; backup leads.db, apply with PCC, restart.
2. AI Workers -> Controls: verify generation usage counter and worker readiness.
3. AI Workers -> Shared business knowledge and quotas: adjust only if exhausted.
4. Send a new message from a different authorized WhatsApp account.
5. Run RUN_AI_REPLY_DIAGNOSTICS.bat again: stage should progress beyond daily_limit.
6. Offline regression test (optional, within installed app venv):
   .venv\Scripts\python.exe -m unittest discover -s tests -p test_reply_quota.py -v

ROLLBACK
PCC can restore source files. The patch does not mutate data itself.
Changes made subsequently by the operator to daily generation limits are saved
in the existing SQLite settings and are not undone by source rollback.
