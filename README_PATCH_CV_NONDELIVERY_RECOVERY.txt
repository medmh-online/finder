MEDMH AI WORKFORCE — CV NON-DELIVERY RECOVERY
PCC UPDATE ONLY — October 23, 2026

WHAT THIS PATCH FIXES
- Prevents the repeating "L'envoi du document n'est pas encore confirme" response on every inbound CV request after a media attempt was marked uncertain.
- If the RECIPIENT explicitly reports that the attachment never arrived, Gemini can request ONE automatically queued media retry for the same lead/file. A simple repeated "send CV" does NOT authorize retry.
- Preserves the original uncertain send audit; never falsely marks it failed/sent to unlock it.
- Each file/lead has a unique durable recipient-retry record: repeated claims of non-delivery do not trigger infinite sends.
- If a late real server ACK arrives for the original file before the retry dispatches, cancel the queued retry.
- Prevents matching a plain-text outgoing echo to an attachment just because the captions match.
- Does not hand off a lead solely because the PDF was requested or unconfirmed.
- Updates the UI to explain the guarded retry and indicate whether the retry was already used.

INSTALL WITH PCC
1. Stop the local MedMH app and all related gateways/dispatchers.
2. Back up the SQLite database using your normal backup control.
3. Apply this ZIP via PCC, then restart normally.
4. Ensure the Job Hunting profile is active, CV is uploaded/enabled, and worker is Ready.
5. From an authorized test contact who genuinely did not receive the CV, send:
   "I did not receive the PDF. Please send the CV again."
6. Look for [AI attachment recipient retry] ... queued ... ;
   [AI media dispatch] ... attachment ... ; and
   [WhatsApp] send outcome ... server_ack_confirmed / uncertain ...
7. Check the recipient WhatsApp chat for an actual PDF bubble.

LIMITATIONS AND SAFETY
- THIS DOES NOT REPAIR A BROKEN WHATSAPP WEB MEDIA TRANSPORT.
- A prior 'uncertain' state is not proof of delivery or non-delivery. A second send has duplicate risk if the recipient is mistaken. We allow only one retry on explicit recipient non-delivery confirmation.
- A missing server ACK still results in 'uncertain' and must NOT be silently retried repeatedly.
- The app keeps existing DB, keys, worker profiles, media, leads and WhatsApp Chrome sessions. Adds only table ai_media_recipient_retries.
- No CV/media is bundled into this ZIP. Don't publish personal files to GitHub.
- This has been tested OFFLINE; WhatsApp delivery needs testing on your Windows setup.
- For continued failure, provide the startup [WhatsApp media compatibility] line, [AI media dispatch] line, and the [WhatsApp] send outcome / [WhatsApp attachment send failed] line. Those logs tell us whether the file actually reached the gateway and why the transport failed.

FILES UPDATED
leadfinder/ai_agent.py
leadfinder/ai_workers.py
leadfinder/ai_ui.py
tests/test_recipient_confirmed_media_retry.py
README_PATCH_CV_NONDELIVERY_RECOVERY.txt

TESTS
35 offline Python regression tests passed: media flow, recipient retry, profile isolation, duplicate prevention, late ACK cancellation, CRM truthfulness and gateway ACK handling.
