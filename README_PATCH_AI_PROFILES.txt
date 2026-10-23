MEDMH AI WORKFORCE — REUSABLE AI INSTRUCTION PROFILES
=================================================

WHAT CHANGED
- AI Workers now offers multiple named profiles. Each has a single textarea
  containing all its instructions; no separate business, portfolio, services,
  prices or sales instructions fields are shown.
- Profiles can be created, edited, deleted when unused, and independently
  activated per worker. All workers may reuse the same saved profile.
- The existing shared business knowledge is imported into the first profile
  without deleting the old settings row or keys, leads, sessions or messages.
- Incoming conversations are pinned to their initial profile. Switching a
  worker to another profile pauses older conversations with a different
  purpose, avoiding sales/job-hunting instruction crossover.
- CRM handoff confirmation is purpose-neutral and queued only when handoff is
  recorded. AI role instructions no longer force all workers to be sales reps.
- The generation-attempt quota remains global per worker in its own field.

FILES MODIFIED
leadfinder/ai_workers.py
leadfinder/ai_ui.py
leadfinder/ai_agent.py
README.md

FILES CREATED
tests/test_ai_instruction_profiles.py
README_PATCH_AI_PROFILES.txt

FILES DELETED
None.

PCC_COMMANDS.json
Not included: no automatic install, build or persistent process needed.
No standalone SQL migration file: the app performs additive SQLite schema
changes on first startup through WorkerDB.initialize().

PERSISTENT COMMANDS
None.

DATABASE
Creates ai_instruction_profiles and adds instruction_profile_id to the
ai_workers and ai_threads tables on first launch. User data remains intact.
IMPORTANT: PCC's source rollback may not reverse the SQLite schema changes.
Back up your real leads.db before installing and do not delete its WAL/SHM
files while MedMH is running.

TEST
1. Exit MEDMH completely and back up its database.
2. Apply this ZIP using PCC normal/dry update (ZIP root = project root).
3. Restart via START.bat and open http://127.0.0.1:8502.
4. AI Workers -> AI profiles and quotas. Verify imported settings have
   survived. Rename imported profile if desired.
5. Create Job Hunting; paste verified CV and job-search instructions.
6. Create or edit MEDMH Sales with catalog/prices/instructions in ONE textarea.
7. Select worker ana; activate one profile. Check that another worker's
   profile does not change. Send a permitted inbound test chat.
8. Switch profiles; check older conversations are shown as paused for the
   other profile. Activate the first profile to make them eligible again.
9. Ask an assigned test chat for human contact or CV; verify CRM handoff.
10. Optional offline tests: .venv\Scripts\python.exe -m unittest discover
    -s tests -p test_ai_instruction_profiles.py -v
11. Check existing workers' token limits and WhatsApp sessions are intact.

SAFETY
Only complete modified/new files are in the ZIP. It never contains a live
CV, API keys, customer chats, contacts, WhatsApp browser/session profiles,
or the SQLite DB itself. No automatic outreach is introduced or enabled.

ROLLBACK
Stop MedMH, restore the prior source files and a known-good pre-update
SQLite backup if you need to revert schema as well. Restoring source only
leaves additive profile tables/columns in place and usually remains compatible,
but is not a full database rollback.
