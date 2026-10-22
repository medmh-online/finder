MedMH v3.9.1 — Reply Diagnostics (PCC patch)

WHAT CHANGED
- Adds optional 'Why no AI reply?' check to the existing AI Workers / WhatsApp browser tab.
- Finds lead by normalized number without Gemini calls and explains missing assignments, duplicate/conflicting phone fields, disabled/handoff state, missing linked inbound, and key/connection state.
- Offers explicit one-click worker assignment only for a unique unassigned lead.
- Displays unmatched message counts and safe Gemini error categories.

WHAT DID NOT CHANGE
- No changes to automated message delivery, matching permissions, safety rules, WhatsApp sessions, databases, queues, or secrets.
- Does not guess opaque LID identities, resend, or contact anyone automatically.

FILES
leadfinder/ai_ui.py
leadfinder/reply_diagnostics.py
README.md

DATABASE
No migrations; read-only inspection and an existing explicit worker assignment command.

COMMANDS
python -m compileall -q leadfinder

TESTS
1. Restart MedMH using START.bat and go to http://127.0.0.1:8502 → AI Workers.
2. Choose your connected WhatsApp worker. Confirm state ready and enabled replies.
3. Open WhatsApp browser tab → Why no AI reply? and enter your saved lead number.
4. If unassigned, explicitly assign it. Send a NEW message from another account.
5. Check that the inbound message appears in Leads/AI monitor and Gemini replies.
6. If unmatched @lid, inspect WhatsApp identity logs; do not guess the ID.

ROLLBACK
Restore previous leadfinder/ai_ui.py and README.md; remove the new reply_diagnostics.py. No database rollback required.
