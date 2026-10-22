MedMH AI Workforce — inbound-event fallback and reply visibility

What changed:
- React to incoming WhatsApp messages on message_create as a fallback to message.
- Deduplicate provider message IDs per worker so the two event hooks cannot trigger two Gemini replies.
- Continue preserving a queued inbound event on disk until the local Python gateway accepts it.
- Show matched/unmatched inbound counts, pending outbound and last AI reply stage in the WhatsApp worker screen.

Files: whatsapp/server.js, whatsapp/package.json, leadfinder/ai_ui.py, tests/test_whatsapp_inbound_events.js, README.md.
Files deleted: none.
Database: no schema changes; no existing data deleted.
Commands: npm run check; npm run test (both working_directory whatsapp), finite and required. The existing npm run test:inbound script is retained for manual use. PCC rejects custom script names containing a colon.
Persistent commands: none (restart START.bat yourself).

Test: stop MedMH first and apply using PCC. Restart, connect the worker, and send a NEW message from another WhatsApp account whose verified phone is assigned to the worker. Refresh AI Workers > WhatsApp browser; check AI gateway intake counters, then Why no AI reply?. If unmatched, verify the LID mapping rather than guessing the sender's number. Review terminal [WhatsApp inbound], [WhatsApp event], [reply], and [agent] lines if needed.

Rollback: PCC will back up the replaced files. Restoring the files removes this event fallback and diagnostic line without altering databases or WhatsApp sessions.

Important: This compatibility fix was statically checked and tested against synthetic events, NOT a live WhatsApp connection. If your existing issue is failed Gemini generation or unmatched @lid, the diagnostic identifies that separate blocker; this patch does not claim to resolve all causes.

Packaging correction: PCC v1.0.69.1 rejected the custom npm script name test:inbound before staging files. This corrected ZIP uses the conventional npm run test command. It has the same application logic as the original patch; no new changes to AI behavior beyond the original fix.
