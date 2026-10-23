MEDMH AI Workforce v3.9.1 — VOICE NOTE SELECTION + EXPLICIT CV RESEND
PCC UPDATE-ONLY PATCH

APPLIES TO
- The existing MEDMH installation after the AI Media Library / multiple profiles
  and WhatsApp Media Compatibility Root Fix patches.
- Do NOT use on a new, unpatched v3.9.1 installation.

CHANGED SOURCE FILES
- leadfinder/ai_agent.py
- leadfinder/ai_workers.py

TESTS
- tests/test_media_voice_and_explicit_resend.py
- tests/test_ai_media_library.py (updated original expectation for confirmed resend)

WHAT CHANGES
1. A previously confirmed uploaded PDF/image/audio is again eligible for a
   *second* send when the recipient explicitly requests the same file again.
   Two confirmed sends per lead per file is the maximum; requests beyond that
   are handled without automatically sending a third copy.
2. The general chat model never spontaneously resends previously delivered
   files. Only the semantic attachment-request classifier can initiate a
   second send after a new inbound request.
3. Files currently queued, sending or with an unconfirmed recent attempt
   remain protected from duplicate automatic sends. Earlier uncertain attempts
   superseded by a later confirmed send do not permanently block one new
   explicit repeat request.
4. The attachment router now understands requests for audible introductions,
   voice notes, 'presentation vocale', and Moroccan Romanized Darija phrases.
   If the first classifier recognizes a file request but omits the saved ID,
   an independent AI check can select a valid file from the active profile.
5. 'I don't have audio available' with a saved eligible voice file is treated
   as an incorrect apology worthy of an independent media check.
6. Audio uploads and actual transport keep the existing app policy, gateway
   message verification, SHA256 validation, profile isolation, opt-out and
   handoff behavior. The source patch does not contain any media files.

INSTALL
1. Back up the database and close MEDMH, including the WhatsApp gateway.
2. Import this ZIP through PCC; apply update only.
3. Run START.bat and check worker is READY.
4. In AI Workers > AI profiles and quotas, select the ACTIVE worker profile
   and verify the OGG voice note is uploaded, enabled, and kind = voice.
   The voice description should mention when it should be sent, e.g.
   'Envoyer cette introduction audio en note vocale quand un recruteur demande
    une presentation orale ou un message qu'il peut ecouter.'
5. In AI Workers > Controls > Check attachment readiness, enter your test lead
   ID to confirm the OGG voice is available and the CV can be requested again.
6. With ONE authorized test chat, explicitly request the recorded audio. Check
   the actual WhatsApp chat for the voice bubble.
7. Explicitly request the CV again. It may be sent for the second confirmed
   time, but no automatic third confirmed copy should be sent.

DIAGNOSTIC LOGS
[AI media router] ... requested True ... eligible N voice_eligible N
[AI media router recovery] ... matched_uploaded_file
[AI media dispatch] ... attachment
[WhatsApp media verified] ... voice bytes ...
[WhatsApp] send outcome ... attachment:voice

IMPORTANT
- An uploaded audio must appear under the same instruction profile currently
  activated for the worker. A file in another profile is intentionally blocked.
- The server accepts OGG/Opus notes via the existing media upload interface.
  OGG files encoded with other codecs need FFmpeg conversion on upload.
- Confirm the audio appears in WhatsApp before considering a send successful.
  The tests here are offline; we cannot access your authenticated local Chrome.
- No SQLite migrations, credentials, QR resets, WhatsApp session deletion, or
  changes to the existing uploaded files are performed by this patch.
