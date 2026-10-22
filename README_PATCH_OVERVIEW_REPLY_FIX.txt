MedMH AI Workforce — Overview date filters / Worker UI cleanup / Reply troubleshooting
===========================================================================

PATCH SCOPE (complete replacement files at project-relative paths):
- app.py: optional From/To reporting dates (All time by default), local-time inclusive-day boundaries, filters for lead creation dates, job listing, Gemini usage and message activity.
- leadfinder/overview_filters.py: pure, tested local-time reporting window and lead cohort helper.
- leadfinder/ai_ui.py: removes screenshot-highlighted automatic introduction toggle, explanatory disclaimer, and automatic bulk WhatsApp verification/assignment controls. Keeps AI replies, reply delay, worker model, and manual identity diagnostics.
- leadfinder/ai_agent.py: stops scheduling old automatic first introductions / automatic bulk assignment; gives first incoming greetings a reply instruction instead of treating them as disposable acknowledgments.
- leadfinder/reply_diagnostics.py: last outgoing result and pending-reply diagnostic; manual bounded and verified WhatsApp @lid identity recovery button. No AI calls from diagnostics.
- whatsapp/server.js: authorized recovery endpoint for already-unmatched chats using existing WhatsApp-verified matching. Requires READY. Background, throttled (2 minutes) and non-sending.
- tests/test_overview_date_filter.py: five offline reporting-range tests.
- README.md: updated local setup/troubleshooting and reporting semantics.

DELETIONS: None.
DATABASE MIGRATIONS: None. Existing databases and settings are untouched.
PERSISTENT COMMANDS: None. Restart the application normally with START.bat.

FINITE COMMANDS: See PCC_COMMANDS.json. Required syntax checks:
1. python -m compileall -q app.py (working directory .)
2. python -m compileall -q leadfinder (working directory .)
3. npm run check (working directory whatsapp)

MANUAL TESTING:
1. Stop the application and close running services; use PCC Normal Mode / dry update first.
2. Apply the patch ZIP; start START.bat and browse http://127.0.0.1:8502.
3. AI Workers > Controls: verify three screenshot-highlighted sections are absent, while AI replies enabled and delays are preserved.
4. Overview: both date fields initially empty = All time. Set one date or both; confirm the local calendar date changes lead counts and message/token activity. An invalid reversed range must show an error.
5. With a connected READY worker, assign one test lead to that worker and send a NEW greeting from another WhatsApp account. The first greeting should now receive a response if a verified inbound event reaches the lead and Gemini is available.
6. If not, run Why no AI reply? to inspect matched versus unmatched messages, API failures and last send status. If @lid is unmatched, use the rate-limited recovery button, then refresh diagnostics. If WhatsApp cannot verify the mapping, no automatic reply occurs (safety boundary).
7. Do not interpret automatic or 'uncertain' WhatsApp send attempts as provider-confirmed delivery.

ROLLBACK:
Apply PCC restore for the previous snapshot if any issue occurs; source-only rollback should preserve SQLite data and WhatsApp sessions. Do not delete runtime queues or databases. Old automatic outreach/assignment settings remain stored, but this patch does not execute their schedulers.

LIMITATIONS:
Code can be syntax-tested and date filtering tested offline. Live WhatsApp message reception, number verification, and Gemini delivery must be tested on your connected Windows installation; these cannot be guaranteed from a source export.
