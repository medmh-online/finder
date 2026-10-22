MEDMH — Owner-approved shared business knowledge & friendly AI tone (PCC patch)

CHANGES
- New owner-approved service catalog, exact indicative MAD price ranges, 0% discount.
- Romanized Moroccan Darija/French-friendly reply guidance with situational,
  non-repetitive AI assistant suggestions ("we can build you one like this").
- AI prompt includes enough context for the FULL service / price / style rules
  (older truncation hid most of the saved knowledge).
- Shared knowledge is updated ONCE on next project start, not reset on reruns.
  Previous business knowledge values are saved in ai_knowledge_revisions.
- AI Worker knowledge text areas enlarged for editing.
- Updated README and offline tests.

FILES INCLUDED
leadfinder/business_knowledge.py (NEW)
leadfinder/ai_workers.py
leadfinder/ai_agent.py
leadfinder/ai_ui.py
tests/test_medmh_knowledge.py (NEW)
README.md
README_PATCH_MEDMH_KNOWLEDGE.txt

FILES DELETED: None
PCC_COMMANDS.json: not required (no pre-start task; source check optional).
PERSISTENT COMMANDS: None
DATABASE: additive ai_knowledge_revisions table in initialize();
          one-time update of existing ai_account_settings only.
          No lead, message, session or token-history changes.

TO APPLY
1. Stop MedMH and back up leads.db (Settings -> Create backup).
2. Apply this ZIP with PCC to the existing project root, then restart START.bat.
3. AI Workers -> Shared business knowledge and quotas: verify the new
   MEDMH services, price estimates and friendly sales instructions.
4. Send a new message from a separate authorized WhatsApp account.
   Check concise response and one relevant AI-assistant suggestion; ask
   for prices to verify ranges are only shared upon request.
5. Test developer handoff to ensure it is recorded before final confirmation.
   Also verify quota, worker connection, and existing lead chats unchanged.

ROLLBACK
Restoring just code will NOT automatically undo knowledge saved to the live
database on first startup. Restore the SQLite backup or manually restore the
previous settings in ai_knowledge_revisions.previous_settings_json.
Do not delete your database, sessions, Gemini keys or WhatsApp profiles.
Live Gemini/WhatsApp behaviors remain untested here.
