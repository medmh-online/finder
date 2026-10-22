MEDMH AI WORKFORCE — LOCAL PHONE NORMALIZATION PATCH

WHY
- Inbound phone-to-lead comparison already uses phonenumbers, not Gemini.
- This patch tightens deterministic matching and removes the actual Gemini
  normalization call from standalone Message Queue batch preparation.

FILES INCLUDED (complete modified/new files)
- leadfinder/ai_outreach.py
- leadfinder/ai_workers.py
- leadfinder/message_queue.py
- leadfinder/queue_ui.py
- tests/test_phone_normalization.py
- README.md
- README_PATCH_LOCAL_PHONE_NORMALIZATION.txt
- PCC_COMMANDS.json (validation instruction only; not installed)

WHAT CHANGED
1. Canonical E.164 normalization for local, +CC, CC and 00CC formats.
2. Match both saved lead phone fields. Conflicting valid stored numbers are not
   auto-matched; duplicates across assigned leads remain blocked.
3. Preserve worker assignment, existing aliases, LID verification and opt-outs.
4. Queue phone normalization is now completely local. No Gemini key/model
   needed for queue preparation; normal reply generation still uses Gemini.
5. Add in-memory SQLite and phone-format regression tests.

DELETIONS: None
DATABASE: No schema changes or data migrations.
PERSISTENT COMMANDS: None
FINITE PCC COMMANDS: python -m compileall -q leadfinder; python -m compileall -q tests

HOW TO TEST
1. Stop MedMH services and apply in PCC Normal Mode after preview.
2. Start MedMH at http://127.0.0.1:8502.
3. Run manually in project venv:
   python -m unittest discover -s tests -p test_phone_normalization.py -v
4. Message Queue: prepare a batch using 06..., 00212..., and +212...
   forms. Confirm equivalent numbers produce identical normalized values,
   without increased Gemini normalization token usage. Do not start sending
   unless testing with consented numbers.
5. Assign a lead with 06... to an AI worker. From the same verified WhatsApp
   phone, send a test inbound message. Only its assigned worker should reply,
   if all original reply rules/consent requirements are satisfied.
6. Test one WhatsApp @lid chat with a verified WhatsApp phone mapping; an
   unverified opaque @lid must remain unmatched.

ROLLBACK
- Restore these source files via PCC snapshot if necessary. Existing
  databases, queued messages, WhatsApp sessions, and secrets are untouched.
- Reverting source restores Gemini-based standalone queue preparation.

LIMITATIONS
- Static compile tests can run without dependencies, but behavioral tests
  require the installed phonenumbers library. Live WhatsApp/Gemini calls are
  not performed during this patch build.
- Cannot infer ownership from opaque WhatsApp @lid digits, guess invalid
  phone numbers, or resolve two leads with the exact same phone automatically.
