MEDMH AI WORKFORCE — WHATSAPP MEDIA COMPATIBILITY ROOT FIX
PCC UPDATE-ONLY PATCH — 2026-10-23

WHY THIS PATCH IS NECESSARY
The supplied real Windows console logs showed:
  [WhatsApp media compatibility] unsupported Cannot safely locate end of outgoing media message object
  [AI media dispatch] ... attachment ...
  [AI media gateway error] ... HTTP 503: WhatsApp media compatibility unavailable ...

Thus Gemini DID select the uploaded PDF, but the gateway refused it BEFORE
calling whatsapp-web.js sendMessage. The prior MEDMH in-memory workaround
mistakenly depended on an exact source comment/indentation after the outgoing
message object, so an installed build with different formatting disabled media.

WHAT CHANGES
1. whatsapp/compat/media_id_fix.js
   - Safely identifies the outgoing 'const message = { ... }' expression inside
     window.WWebJS.sendMessage using balanced JS braces.
   - Verifies that it contains the expected original message ID, media spread,
     and extra-options spread before making any change.
   - Inserts the upstream fix 'delete message.__x_id;' after construction.
   - Removes the brittle dependency on the following comment/indentation.
   - Keeps the patch in memory; NO node_modules edits, npm install, Chrome
     session reset, WhatsApp worker recreation or QR re-pairing required.
   - Refuses the operation safely if the installed injected source is
     substantially different from what is supported.
2. leadfinder/ai_agent.py
   - Classifies exactly the known HTTP 503 "WhatsApp media compatibility unavailable"
     pre-send rejection as FAILED, not UNCERTAIN; the gateway never sent any
     bytes in that case. Unconfirmed transport outcomes remain UNCERTAIN.
3. Tests for source layout and precise pre-send status classification.

INSTALL USING PCC
1. Back up your leads.db using Settings -> Create backup.
2. Completely stop MEDMH (including queue dispatcher and WhatsApp gateway).
3. Apply this ZIP as a PCC update-only patch.
4. Restart MEDMH using START.bat.
5. Verify the console prints exactly one of:
     [WhatsApp media compatibility] patched_in_memory
     [WhatsApp media compatibility] already_fixed
   If it still prints 'unsupported', copy the complete line. Do NOT modify
   WhatsApp internals, reinstall node_modules or reconnect your QR yet.
6. Under AI Workers -> Controls, run the existing WhatsApp media gateway check
   on the CV file. It should now pass the compatibility step.
7. PRIOR ATTEMPTS: The previous HTTP 503 errors may have left older CV messages
   as UNCERTAIN in SQLite. Only after you verify the recipient never got the PDF,
   use Controls -> Media send status -> the relevant message ->
   'I checked the recipient chat and this file did NOT arrive' ->
   'Allow future AI resend for this file'. This changes the historical status;
   it does NOT immediately send. No automatic mass reset is performed.
8. For a test worker with the matching Job Hunting profile active and no
   active handoff, ask for the CV once from an authorized test contact.
9. Check the recipient chat for an actual PDF bubble; expected gateway logs:
     [WhatsApp media verified] ... document bytes ...
     [WhatsApp] send outcome ... server_ack_confirmed ... attachment:document
   A remaining [WhatsApp attachment send failed] message will pinpoint a
   different transport failure, not the fixed media-compatibility startup gate.

TESTS
- Source-code patch regression: five different source layouts, repeated patch,
  actual JS object runtime, ID preservation, and refusal of unsafe layouts.
- Scoped CommonJS loader integration with a mock whatsapp-web.js 1.34.7 package.
- 43 Python integration/regression tests passed on the reconstructed latest
  project from the previous series of MEDMH patches.
- Existing WhatsApp server ACK and message event tests passed.
- Python/Node.js syntax checks passed.

LIMITATION
No real Chrome/WhatsApp live sending was possible in this environment. This
patch removes the observed deterministic HTTP 503 gate but does not guarantee
successful end-to-end WhatsApp delivery. Send one test PDF and review its ACK.

FILES INCLUDED
  whatsapp/compat/media_id_fix.js
  whatsapp/compat/test_media_id_fix.js
  leadfinder/ai_agent.py
  tests/test_media_pre_send_failure.py
  README_PATCH_WHATSAPP_MEDIA_COMPAT_ROOT_FIX.txt

NO USER DATA BUNDLED
No database, CV or other uploaded files, credentials, Gemini keys, WhatsApp
sessions, node_modules, or Chrome profiles are included in this ZIP.
