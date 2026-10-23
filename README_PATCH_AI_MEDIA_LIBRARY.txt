MEDMH AI WORKFORCE — PROFILE MEDIA LIBRARY (PCC UPDATE-ONLY)
============================================================
Patch is designed for the MedMH_AI_Workforce_v3_9_1 installation AFTER
MedMH_MULTI_AI_INSTRUCTION_PROFILES_PCC_PATCH.zip has been applied.
It is NOT a full project. PCC ZIP root is the project root.

WHAT IT DOES
- AI Workers > AI profiles and quotas > select a profile > Uploaded attachments.
- Upload PDFs, PNG/JPG/WEBP images and Ogg/Opus voice recordings.
- MP3/M4A/WAV audio can be uploaded if FFmpeg is on the Windows PATH;
  it is converted locally to Ogg/Opus voice format.
- Per-upload display name and a clear natural-language 'when to send' rule.
- Each file belongs to ONE saved instruction profile. Other profiles cannot access it.
- Gemini receives only ID/name/kind/sending rule and can select zero or one
  attachment for a reply without manually approving each send.
- The backend validates profile membership, local file integrity, status,
  worker ownership, and previously sent attachments before invoking WhatsApp.js.
- Node gateway sends the document/image/voice via whatsapp-web.js, using a
  local loopback bridge command. It never accepts AI-provided file paths.
- No intentional repeats of the same attachment per lead. No automatic resend
  after an uncertain WhatsApp outcome. The normal worker send queue is reused.
- With a CV request, Gemini may choose a saved CV alongside the recruitment
  handoff. The CRM handoff flag is recorded BEFORE dispatch to prevent further
  AI messages, and WhatsApp send outcome is recorded afterward. An uncertain
  send requires manual checking and is NOT automatically retried.
- Voice messages are sent as actual WhatsApp voice notes. Audio messages do
  not support text captions in this implementation: voice note is sent alone.

FILES IN THIS PATCH
  leadfinder/ai_workers.py  (complete updated file)
  leadfinder/ai_agent.py    (complete updated file)
  leadfinder/ai_ui.py       (complete updated file)
  leadfinder/ai_media.py    (new local media storage helper)
  whatsapp/server.js       (complete updated gateway)
  run_ai.py                (pass media directory to gateway)
  .gitignore               (protect local ai_media/ from Git)
  tests/test_ai_media_library.py (new offline tests)
  README_PATCH_AI_MEDIA_LIBRARY.txt

IMPORTANT PRIVACY
- THIS ZIP CONTAINS NO CV OR OTHER ATTACHMENTS, NO ACCOUNT DATABASES,
  keys, phone numbers, session state or live messages.
- Uploaded media is stored under <LEADFINDER_DATA_DIR>/ai_media/ (or the
  project root's ai_media/ when a custom data directory is not configured).
- The ai_media/ folder is ignored by Git. It is sensitive local data; back it
  up privately when migrating machines. Existing worker sessions unaffected.
- Your attachments are not sent to Gemini: only the metadata and sending rules
  are included in the AI prompt.
- A CV contains personal information. Assign it only to the Job Hunting
  profile and set 'when to send' to 'only if recruiter asks for the CV'.
- Only use approved/authorized WhatsApp recipients. A number published on
  Google Maps is NOT automatic permission for cold outreach.

INSTALL
1. BACK UP leads.db and existing source files using Settings / your PCC backup.
2. Shut down MEDMH and its Node/Python gateways completely.
3. Verify the earlier AI Profiles PCC patch is already installed.
4. Apply this ZIP through PCC (ZIP root = project root).
5. Restart the usual START.bat.
6. Open AI Workers > AI profiles and quotas; select 'Job Hunting'.
7. Upload your CV PDF. Name 'Mohammed CV'. Description:
   'Only when the recruiter requests the CV. After sending, hand off to Mohammed.'
8. Optional: upload a portfolio preview image and a recorded Ogg/Opus voice.
9. Test with ONE authorized WhatsApp number before any wider use.

VALIDATION
  Python: .venv\Scripts\python.exe -m unittest discover -s tests -p test_ai_media_library.py -v
  Python: .venv\Scripts\python.exe -m unittest discover -s tests -p test_ai_instruction_profiles.py -v
  Node:   cd whatsapp && npm run check
Offline: 9 media tests and 6 existing profile tests passed here.
Actual Gemini calls, Windows FFmpeg conversion and WhatsApp delivery were NOT
verified in this environment. whatsapp-web.js is unofficial and may break.

DB AND ROLLBACK
First startup adds ai_profile_media and an ai_messages.media_id column.
These schema changes are additive. A source-only rollback does not revert
these SQLite schema additions; restore a pre-update DB backup for complete
rollback. Deleting a media entry removes its stored file and blocks its queued
unsent messages; already sent historical message metadata remains.

LIMITS
- One media file per AI reply; max 12 MB; media is not transcribed or analyzed.
- PDF and image captions depend on WhatsApp Web behavior/version.
- FFmpeg is needed for non-Ogg/Opus voice input.
- Very large/unsupported media, GIF, video and new voice generation are not
  part of this patch.
- Handoff can be recorded even if a subsequent media delivery is uncertain;
  inspect send status and WhatsApp in that case before following up manually.

PCC_COMMANDS.json is intentionally omitted. No install/repair command or
npm package modification is necessary. The previous Node dependencies suffice.
