MEDMH AI WORKFORCE — CV / ATTACHMENT AUTO-SEND FIX
PCC UPDATE-ONLY ZIP · 2026-10-23

PREREQUISITES
- Install on top of the previously installed MEDMH AI Media Request Recovery patch
  (MedMH_AI_MEDIA_REQUEST_RECOVERY_PCC_PATCH.zip).
- Media Library and WhatsApp Media Compatibility patches must already be installed.
- Use your PCC Update/Patch ZIP workflow; this is NOT a full app install.
- Completely close the app and its gateway processes and back up leads.db first.
- Never overwrite existing DB, profiles, keys, local media or WhatsApp sessions.

WHAT WAS WRONG
- Previously, Gemini generated a text response first; it could apologize,
  advertise the portfolio, or choose handoff without ever selecting a PDF.
- A second media review only ran for specific model-generated excuses/handoffs.
  This missed natural-language requests that produced other replies.
- WhatsApp media-send failures could appear as vague "uncertain" statuses.
- The gateway only used message_create echoes for text messages, not for
  verified outgoing document/image/voice events with WhatsApp IDs.

WHAT THIS PATCH DOES
1. For profiles with uploaded files, a separate Gemini routing decision now
   examines the MOST RECENT inbound message first. It selects from uploaded
   files within that profile (never a model-supplied disk path), respecting
   per-file rules and allowed profile permissions.
2. If an eligible file is selected and no explicit human intervention is
   requested, the backend queues the file together with a brief caption in
   ONE WhatsApp message. The main model cannot override this with an apology
   or an inappropriate developer handoff.
3. A CV request alone does NOT turn AI off. The conversation stays active.
   Interviews, calls, personal decisions and salary negotiations still use
   the existing human handoff behavior.
4. If a file was already sent, is pending, disabled, or has an unconfirmed
   send, no automatic duplicate is sent. The AI states the situation without
   triggering a needless handoff.
5. Node /send verifies and logs actual file availability/integrity. An
   outgoing attachment message_create event with a MsgKey may confirm a send
   when WhatsApp.js returns no ID, provided its media type/caption matches.
6. NEW AI Workers > Controls > Check WhatsApp media gateway allows a non-send
   test of an uploaded profile file against the actual Node.js gateway. It
   reports media availability and media-compatibility status. This test does
   not send a WhatsApp message or modify conversation state.
7. Transport errors now log [AI media gateway error] and uncertainty logs
   [AI media delivery unconfirmed], instead of silently hiding the details.

INSTALL & TEST WITH ONE AUTHORIZED TEST CONTACT
1. Shut down MedMH fully, backup, apply this ZIP via PCC, then restart.
2. Verify WhatsApp worker says READY, with the Job Hunting profile active.
3. In AI Workers > AI profiles and quotas, verify that CV.pdf is UPLOADED
   AND ENABLED for that same profile. Writing CV instructions is NOT uploading.
4. In AI Workers > Controls, open Check WhatsApp media gateway, select the CV
   and click Check media readiness in WhatsApp.js. Expected: mediaReady=true.
5. In AI Workers > Controls, use Check attachment readiness for the intended
   lead ID. Resolve mismatched profile, handoff/paused state, and any old
   uncertain send BEFORE requesting the CV again.
6. For an old handoff, manually choose Resume after handoff in Leads for your
   authorized test lead only.
7. From that authorized test contact, request CV again. Look for logs:
      [AI media router] ... requested True ... selected True ... eligible 1
      [AI media dispatch] ... attachment ...
      [WhatsApp media verified] ... document ... bytes ...
      [WhatsApp] send outcome ... attachment:document
      [AI media result] ... sent attachment
8. Verify a real PDF/document bubble in the recipient chat. A sent status
   confirms a WhatsApp message ID, not that the file was read by its recipient.
9. If preflight fails or no PDF arrives, report the exact new log lines and
   preflight result; live compatibility cannot be proven by offline tests.

SAFETY AND DATA
- Does not touch WhatsApp Chrome profiles, sessions, tokens, credentials,
  keys, media files, SQLite database or contact assignments.
- Does not automatically retry uncertain sends; confirm absence in WhatsApp
  before using the existing manual unlock control.
- Keeps profile isolation, file SHA-256 checks and opted-out protections.
- No schema migration, npm install or reinstall necessary.
- The extra Gemini media-routing call counts towards per-worker AI usage
  whenever the active profile has at least one uploaded file.
- If no file has been uploaded, this patch cannot send one.

CHANGED FILES
  leadfinder/ai_agent.py
  leadfinder/ai_ui.py
  whatsapp/server.js
  tests/test_ai_media_library.py

LOCAL OFFLINE TESTS
  python -m unittest discover -s tests -p test_ai_media_library.py -v
  node whatsapp/compat/test_media_id_fix.js
  python -m py_compile leadfinder/ai_agent.py leadfinder/ai_ui.py
  node --check whatsapp/server.js

21 media-pipeline Python tests passed.
4 media-ID compatibility Node tests passed.
Python compilation and Node syntax checks passed.
Actual WhatsApp delivery cannot be exercised outside your connected PC.
