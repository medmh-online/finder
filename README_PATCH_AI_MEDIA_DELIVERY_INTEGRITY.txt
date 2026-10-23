MEDMH AI WORKFORCE — AI MEDIA DELIVERY INTEGRITY FIX
==================================================
PCC UPDATE-ONLY PATCH. Requires previously applied AI Profiles + AI Media
Library + WhatsApp Media Compatibility patches. Not a full project ZIP.

WHY
A recruiter's screenshot shows that Gemini said "Voila son CV" but WhatsApp
only showed a text message. On the next turn the AI said no CV was available.
The previous implementation allowed Gemini to propose a file attachment in
plain language while returning media_id=null. It also hid attachments with a
prior uncertain send without explaining that status to Gemini.

WHAT CHANGES
1) leadfinder/ai_agent.py
   - Explicitly annotate text-only vs confirmed attached WhatsApp history.
   - Provide Gemini with prior saved file-send status (confirmed/unconfirmed).
   - Forbid false file-delivery claims without a selected approved media ID.
   - If a draft makes such a claim, ask Gemini once to correct it BEFORE
     queueing any reply. If still unsupported, do not send misleading text.
   - Logs: [AI attachment decision], [AI media dispatch], [AI media result].
   - Keep Job Hunting conversations active after CV sends; normal handoffs
     remain governed by your active instructions and CRM decisions.
   - Verify the gateway accepted the attachment reference before treating a
     returned WhatsApp message ID as an attachment send confirmation.
2) leadfinder/ai_workers.py
   - Read-only per-lead historical media send statuses for Gemini.
   - Recent per-worker media delivery status for dashboard review.
   - Controlled operator unlock: an 'uncertain' send may be marked failed
     ONLY after the operator checks the recipient's WhatsApp chat and explicitly
     confirms the attachment did not arrive. This DOES NOT send anything.
3) leadfinder/ai_ui.py
   - New 'Media send status' expander under worker controls. A checked
     confirmation can re-enable the approved file for a later eligible inbound
     reply when a previous send was uncertain.
4) whatsapp/server.js
   - Returns mediaAccepted=true/false and logs whether each send attempted
     an attachment or text only. Existing compatibility patch preserved.
5) tests/test_ai_media_library.py
   - Regression coverage for screenshot, past uncertain sends, operator unlock,
     dispatch confirmation checks and original media features.

INSTALL
1. If the recipient actually received the previous CV, DO NOT resend.
2. Back up your leads.db using MEDMH Settings. Stop MEDMH and gateways.
3. Apply this ZIP through PCC. Restart with START.bat.
4. Check AI Workers -> AI profiles and quotas -> Job Hunting:
   the CV must be listed under 'Uploaded attachments for selected profile',
   ENABLED, and the worker must have Job Hunting activated.
   Text instructions alone do NOT upload the CV; use the file uploader.
5. Chat with ONE authorized test contact: "Pouvez-vous envoyer le CV ?"
   - [AI attachment decision] ... selected allowed 1  (ID chosen)
   - [AI media dispatch] ... attachment             (actual file job)
   - [WhatsApp] send outcome ... confirmed attachment:document (gateway ID)
   - [AI media result] ... sent attachment            (app status)
   If the decision says text_only or allowed 0, no file was sent.
6. If status is UNCERTAIN, check WhatsApp Web FIRST. Only if the file truly
   was not received, open AI Workers -> worker controls -> Media send status,
   check 'I checked ... did NOT arrive' and choose 'Allow future AI resend'.
   Then send a NEW inbound test message; the AI may select it again.
   DO NOT manually resend uncertain files without checking first.

NOTES
- 'confirmed' refers to a returned WhatsApp message ID, not a read receipt.
- Actual WhatsApp browser/media delivery cannot be verified offline here.
- There is no automatic retry of uncertain sends, to avoid duplicate CVs.
- Uploaded CV/images/audio remain on your local machine and are NOT in ZIP.
- No database migration, session reset, npm reinstall, Chrome profile change,
  quota change or API-key handling change.
- Revert via PCC source backup if necessary; database records are unchanged.

OFFLINE TESTS
  python -m unittest discover -s tests -p test_ai_media_library.py -v
  python -m unittest discover -s tests -p test_ai_instruction_profiles.py -v
  node --check whatsapp/server.js
  python -m py_compile leadfinder/ai_agent.py leadfinder/ai_workers.py leadfinder/ai_ui.py

Result: 15 AI media tests and 6 AI profile tests pass.
