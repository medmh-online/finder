MedMH v3.9.1 — READ-ONLY AI REPLY ROOT-CAUSE DIAGNOSTICS

WHAT CHANGED
- Added tools/diagnose_ai_replies.py: read-only check of the current live SQLite database, worker registry, localhost gateway health and WhatsApp event backlog.
- Added RUN_AI_REPLY_DIAGNOSTICS.bat: double-click runner using the app's existing .venv Python.
- Does not modify app.py, ai_server.py, the sending queue, any settings, or any databases.
- Does not call Gemini, send WhatsApp messages, or print phone numbers, chat contents or API keys.

AFTER APPLYING IN PCC
1. Keep MedMH running, so its local services can be tested.
2. Double-click RUN_AI_REPLY_DIAGNOSTICS.bat in your MedMH project folder.
3. Copy its output into ChatGPT. You may alternatively run RUN_AI_REPLY_DIAGNOSTICS.bat --lead 50 to focus on lead #50.
4. If your affected lead has a different ID, use --lead YOUR_ID.

NOTE
- The report displays partial worker IDs, lead IDs, timestamps and status flags. Inspect before sharing if you consider these private.
- It reads the SQLite file using SQLite mode=ro. It does not mutate runtime state.
- This is a diagnostic patch, not an unverified fix to messaging behavior.

FILES INCLUDED
- tools/diagnose_ai_replies.py
- RUN_AI_REPLY_DIAGNOSTICS.bat
- README_PATCH_AI_REPLY_DIAGNOSTICS.txt

FILES DELETED: none
COMMANDS: none
PERSISTENT COMMANDS: none
DATABASE CHANGES: none
ROLLBACK: Remove only the two installed diagnostic scripts if no longer needed.
