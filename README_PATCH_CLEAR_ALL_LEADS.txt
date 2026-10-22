MedMH AI Workforce — Clear All Leads (PCC patch)

Patch purpose:
- Add Clear all leads action to the Leads page, including strict typed confirmation.
- Create a verified SQLite backup before destructive changes.
- Clear every lead, even archived/blocked, and related CRM and AI messages/assignments.
- Preserve opt-outs in the separate global blocked-contact ledger BEFORE deletion.
- Preserve standalone Message Queue, worker configuration/Gemini keys, sessions, campaigns, search-job history and AI token totals.
- Refuse while jobs are queued/running, while assigned workers have AI replies enabled, or while an active AI task/send is in progress.
- Reset Streamlit lead-table/filter state after success.

Modified full files:
  app.py
  leadfinder/storage.py
  README.md
New file:
  tests/test_clear_all_leads.py
Deleted files: none
Database migrations: none (SQLite deletion occurs ONLY after the user explicitly confirms inside the UI)
Persistent commands: None

Validation:
- PCC commands use the previously verified Python compileall checks. Run the regression suite separately from a terminal: python -m unittest discover -s tests -p test_clear_all_leads.py -v.
- Test using http://127.0.0.1:8502 -> Leads -> Clear all leads.
- Confirm that the button requires DELETE N LEADS, that active work blocks deletion, and that backup is created before deletion.
- Verify Leads/Overview show zero records afterwards; verify AI Workers and Message Queue remain unchanged.

Important: This action is intentionally destructive. It does NOT undo previous sends. Future searches may recollect businesses. The global do-not-contact list remains in force.
Rollback: PCC can restore source files if the patch fails. A user-confirmed deletion is a separate database operation; recover previous leads by restoring the matching SQLite backup (while MedMH is fully stopped), not by reverting code alone.
