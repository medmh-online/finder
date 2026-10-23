MEDMH AI WORKFORCE — WHATSAPP MEDIA CONFIRMATION + FIRST INBOUND FIX
PCC UPDATE-ONLY ZIP — 2026-10-23

INSTALL TARGET
- Apply on top of MedMH_AI_CV_ATTACHMENT_AUTOSEND_FIX_PCC_PATCH.zip.
- These are complete project-relative files; do not unzip in a subfolder.
- Shut down MEDMH, Python bridge (8766), Node WhatsApp gateway (8767) and worker Chrome AI attachment before applying. Keep saved worker Chrome pairing.
- Back up your existing SQLite database before installation.
- Use PCC Update/Patch ZIP workflow. No npm install, no reset, no schema migration required.
- This ZIP intentionally contains no CV, keys, database, credentials, WhatsApp pairing data or uploaded attachments.

FIXES
1. WhatsApp event IDs and send receipts now support both '_serialized' and '$1' identifier shapes used by newer WhatsApp Web builds. A real matching document/image/voice echo may confirm media when sendMessage omits the old ID format.
2. Both 'message' and 'message_create' are used for INBOUND messages, deduplicated by a stable ID. This reduces missed first-message events without double replies or heuristic greeting handling.
3. Uploaded PDF/image sends bypass getChatById typing calls that trigger opaque 'r:r' errors on some WhatsApp Web versions. All configured reply delays still apply.
4. CV requests are NOT human handoffs, even when matching media is disabled, missing or held by a previous uncertain send. AI continues normally; genuine interview/direct-human requests still hand over.
5. Media-sending exceptions are logged as [WhatsApp attachment send failed], and no uncertain attachment is blindly retried.
6. The media file upload placeholder now describes the desired continue-after-CV behavior.

HOW TO TEST
1. Confirm WhatsApp worker state is READY and active profile is Job Hunting.
2. Confirm CV file is enabled under the active profile.
3. VERY IMPORTANT: For the specific test lead, open AI Workers > Controls > Media send status. If a CV attempt is 'uncertain', verify visually that the PDF is absent from WhatsApp; check the confirmation box and click 'Allow future AI resend for this file'. Without this, the old uncertain attempt continues blocking automatic sends by design. The patch does NOT clear it for you.
4. If a previous test was handed off, manually resume the AI for that test lead in Leads.
5. From your own authorized test contact, send ONE greeting. Check [WhatsApp inbound] and [WhatsApp event] logs, then a normal Gemini reply. Respect any configured 'Combine consecutive customer messages' and reply delay (these can add seconds).
6. Send a CV request and watch [AI media router], [AI media dispatch] attachment, [WhatsApp media verified], [WhatsApp] send outcome confirmed, and [AI media result] sent attachment.
7. Check that WhatsApp visibly contains a PDF/document bubble. A 'confirmed' ID means WhatsApp accepted an outgoing message; it does not prove the recipient downloaded it.
8. If no PDF appears, send the [WhatsApp attachment send failed], [WhatsApp] send outcome, [AI media router], [AI media dispatch], and [AI media result] log lines, as well as Media Gateway preflight status. These identify a remaining WhatsApp Web compatibility failure rather than a missing Gemini prompt.

SAFE BEHAVIOR
- Does not automatically retry uncertain sends, avoiding duplicate CVs.
- Does not change Chrome sessions, contact assignments, old conversations, worker keys or media files.
- A message must have a REAL WhatsApp message ID to be marked sent; a synthetic fallback event ID is never used as delivery confirmation.
- Inbound events processed through two callbacks share stable deduplication so a first greeting cannot produce duplicate automated replies.

OFFLINE TESTS
- python -m unittest discover -s tests -p test_ai_media_library.py -q  (23 passed)
- python -m unittest discover -s tests -p test_ai_instruction_profiles.py -q  (6 passed)
- node whatsapp/compat/test_message_events.js (passed)
- node whatsapp/compat/test_media_id_fix.js (4 passed)
- node --check whatsapp/server.js
- python -m py_compile leadfinder/ai_agent.py leadfinder/ai_ui.py

Actual connected WhatsApp delivery cannot be validated in this offline environment.

PATCH FILES
  leadfinder/ai_agent.py
  leadfinder/ai_ui.py
  whatsapp/server.js
  whatsapp/compat/message_events.js
  whatsapp/compat/test_message_events.js
  tests/test_ai_media_library.py
  README_PATCH_MEDIA_FIRST_MESSAGE_FIX.txt
