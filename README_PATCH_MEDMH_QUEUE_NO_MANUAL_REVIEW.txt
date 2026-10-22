MEDMH AI WORKFORCE v3.9.1 — MESSAGE QUEUE UI SIMPLIFICATION
PCC update-only patch (ZIP root = project root)

WHAT CHANGED
- Removed the entire manual 'Review an uncertain send' selector, checkbox and confirmation button.
- Counts attempts without provider-confirmed message IDs in the displayed Sent total.
- Displays these individual rows as 'sent (unconfirmed)', not as confirmed delivery.
- Shows Skipped separately (not-registered and blocked recipients are NOT counted as sent).
- Applies to existing batches as well as new ones; no database migration/backfill is needed.
- No changes to actual WhatsApp sending, automatic retry safety, number checks, rate limits or delays.

FILES MODIFIED
- leadfinder/queue_ui.py
FILES DELETED
- None
DATABASE CHANGES
- None. The stored 'uncertain' state remains untouched, keeping crash/retry protection.
COMMANDS
- python -m compileall -q leadfinder (working_directory=., required=true)
PERSISTENT COMMANDS
- None
DOCUMENTATION
- README.md unchanged. No setup, port, service, installation or bootstrap changes.

TESTING
1. Stop MedMH services; in PCC use dry update, then update on this ZIP.
2. Restart MedMH via START.bat and open http://127.0.0.1:8502.
3. Navigate to Message Queue as the local operator (no user login is configured).
4. Open an existing batch with uncertain outcomes.
5. The former manual review UI must be gone; Sent includes uncertain attempts.
6. Rows previously uncertain show 'sent (unconfirmed)'; six skipped numbers still show skipped.
7. Confirm pause/resume, new batch creation and delay settings remain unchanged.
8. Verify on desktop and optionally mobile; no theme change (existing light theme).

ROLLBACK
- PCC restore from the pre-update backup, or restore the prior leadfinder/queue_ui.py.
- Do not remove/modify any database, queue state, WhatsApp profiles, keys, or leads.
- An attempted send without confirmation is NOT guaranteed to have been delivered.
