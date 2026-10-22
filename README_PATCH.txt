MedMH AI Workforce v3.9.1 — Canonical Incoming Number Matching (update only)

WHAT CHANGED
- Compares incoming WhatsApp sender and the lead phone using the same valid E.164 format.
- Uses the CURRENT visible Leads phone as the source of truth, not an outdated phone_e164 cache.
- Accepts international variants such as +212..., 00212..., 212... when valid, with spaces/dashes.
- Matches 06... national formats only when the lead's country code supports them (e.g., MA).
- For WhatsApp @lid identities, uses only the phone verified by WhatsApp; NEVER interprets the opaque LID as the phone number.
- Uses the same canonical lead phone throughout the worker's CRM and outbound utilities.
- Never matches a number assigned to multiple leads on the same worker: ambiguous records remain unmatched.

INSTALL
1. Close START.bat and all MedMH worker Chrome windows.
2. Back up your existing leadfinder/ai_workers.py file.
3. Extract this ZIP into the existing MedMH v3.9.1 project folder, allowing overwrite of leadfinder/ai_workers.py.
4. Run START.bat; connect WhatsApp and attach the AI worker.
5. Ensure the lead is assigned to that worker and AI isn't paused or handed off, then send an incoming test message.

NO DATA RESET
No database migrations or account/WhatsApp re-pairing are required.
The original phone spelling stays in the Leads table; matching only uses a computed canonical value.

DIAGNOSTICS
The terminal should log `[WhatsApp event] in ... matched lead <id>` when the incoming message is linked.
If it reports `unmatched chat`, WhatsApp may not have supplied a verifiable number for an @lid sender, or the phone may not match a unique assigned lead.
If a message matches but the AI is silent, inspect Pause AI, developer handoff, model/keys, token budget and reply/ignore logic. Phone matching alone cannot override those controls.

VERIFICATION
Python syntax checked; isolated SQLite tests simulated Moroccan phone variants, @c.us, verified @lid, stale cache, number edits, and duplicate-lead safeguards. Live WhatsApp/Gemini behavior was not tested.
