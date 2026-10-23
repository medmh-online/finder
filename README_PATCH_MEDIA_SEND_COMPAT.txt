MEDMH AI WORKFORCE — WHATSAPP MEDIA SEND COMPATIBILITY FIX
===========================================================
PCC UPDATE-ONLY ZIP; apply AFTER the AI Profiles and AI Media Library updates.
This is NOT a complete project installation.

SYMPTOMS
- whatsapp-web.js reports "Data passed to getter must include an id property"
  when sending PDF, image or voice messages.
- Gateway sometimes reports "send outcome ... uncertain no-id".
- "onChatUnreadCountEvent failed" / typing indicator errors may be unrelated
  WhatsApp Web compatibility warnings, not proof of media delivery failure.

ROOT CAUSE
- whatsapp-web.js 1.34.7's injected message constructor can copy MediaData's
  internal __x_id onto the outgoing message and invalidate its message key.
- Upstream fix: github.com/wwebjs/whatsapp-web.js/issues/201922 and PR #201923.

CHANGES
1) whatsapp/compat/media_id_fix.js (NEW)
   - Scoped, in-memory change to the library's injected sendMessage code at
     startup: delete message.__x_id after assembling the outgoing model.
   - Matches the verified 1.34.7 source layout, or recognizes an already-fixed
     version. Does NOT modify node_modules, WhatsApp session data, Chrome, or
     package files. If the layout changes, media sends fail closed with an
     actionable 503 error; ordinary text messages remain available.
2) whatsapp/server.js (UPDATED)
   - Uses that compatibility loader. All existing worker/queue logic preserved.
3) leadfinder/ai_agent.py (UPDATED)
   - No longer forces an automatic handoff just because a recruiter asks for
     a CV. Gemini can select the approved CV attachment and continue chatting;
     handoff still occurs for interviews, personal decisions, direct contact,
     or other handoff rules in the active AI profile.
4) Offline regression tests included under tests/ and whatsapp/compat/.

INSTALL
1. Verify whether the earlier CV appears in the recipient's WhatsApp chat.
   Messages with the 'uncertain' status might already have arrived.
2. In MEDMH Settings, BACK UP your database. Stop MEDMH and gateways.
3. Apply this ZIP using PCC (paths are relative to the project root).
4. Restart MEDMH normally with START.bat. No npm install required.
5. In the terminal, look for:
      [WhatsApp media compatibility] patched_in_memory
   or [WhatsApp media compatibility] already_fixed
6. In the Job Hunting AI profile, make sure the CV attachment description
   does NOT say to hand off immediately after sending.
   Suggested rule: 'When a recruiter requests the CV, send this PDF. Keep
   answering routine questions afterward; hand off only if human action is
   required.'
7. Test PDF first with ONE authorized contact; confirm the PDF is visible in
   WhatsApp Web itself, not merely marked sent in the dashboard.
8. Then test image and voice (voice requires Ogg/Opus).

NOTE ON DELIVERY UNCERTAINTY
- The patch fixes a known crash, but an outgoing message may still have no ID
  under some WhatsApp Web releases. "uncertain" remains an unconfirmed result,
  NOT proof of delivery or proof of failure. The app does NOT auto-retry such
  messages to avoid accidental duplicate CVs or other attachments.
- Other WhatsApp-internal warnings (e.g., onChatUnreadCountEvent) are not fixed
  by this scoped media patch and may require separate investigation if they
  interfere with new messages.

VALIDATION
  node --check whatsapp/server.js
  node whatsapp/compat/test_media_id_fix.js
  python -m unittest discover -s tests -p test_ai_media_library.py -v
  python -m unittest discover -s tests -p test_ai_instruction_profiles.py -v
  Offline locally: 4 JS tests + 10 media Python tests + 6 profile tests passed.
  Real live WhatsApp Windows delivery NOT verified; use an authorized test chat.

PRIVACY & ROLLBACK
- No CV, uploaded media, personal phone numbers, API keys, database, sessions,
  or secrets are in this ZIP. No database changes, installation or npm upgrade.
- Revert the changed source files using your PCC/source backup if required.
- Media is patched only IN MEMORY in the gateway process, not on disk.
