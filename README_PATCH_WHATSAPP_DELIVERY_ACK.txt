MEDMH AI WORKFORCE — WHATSAPP DELIVERY ACK + HONEST CRM STATUS
PCC UPDATE-ONLY ZIP — 2026-10-23

APPLIES TO
  MEDMH v3.9.1 with the previously supplied
  MedMH_WHATSAPP_CV_AND_FIRST_REPLY_PCC_PATCH.zip already applied.
  Every path in this ZIP is relative to the MEDMH project root.

WHY THE OLD APP SHOWED A REPLY THAT WAS MISSING IN WHATSAPP
  whatsapp-web.js sendMessage() can return a message ID, and message_create
  can fire when WhatsApp Web creates a LOCAL outgoing message, before the
  WhatsApp server accepts it. The previous gateway treated those as delivery
  proof. That caused ai_messages.status='sent' or 'Latest message' to show a
  generated/queued reply even when the recipient's chat had no outgoing bubble.

CHANGES
1. The WhatsApp /send endpoint now requires message_ack >= 1 from the SAME
   outgoing real WhatsApp Msg ID, with listeners installed before sendMessage.
   The gateway waits for at most 40 seconds for ACK. No ACK => uncertain.
   It NEVER automatically resends an uncertain text/PDF/image/voice attempt.
2. The 'message_create' event is kept ONLY as a fallback for missed incoming
   messages, not as proof that any outgoing message was sent.
3. Outgoing manual WhatsApp sends are synced into the CRM only after a real
   server ACK. The Python gateway refuses old queued local-only outbound
   event records that lack the ack field.
4. The AI dispatcher only marks sent if the gateway returns:
   sent=true, confirmedBy='server_ack', ack>=1.
5. A real WhatsApp message ID is retained on uncertain attempts. If an ACK
   arrives later, the existing record can be marked sent without adding a
   duplicate message. Queued/unconfirmed records cannot be promoted to sent
   merely because the model says 'Voici le document demande'.
6. Leads -> Latest message now labels unsent outgoing drafts:
   [QUEUED - not sent], [UNCONFIRMED - not verified], [NOT SENT: ...].
   This makes the database vs WhatsApp difference visible.

INSTALL
1. Stop START.bat and all active MEDMH services (Python bridge 8766, Node
   gateway 8767); ensure your worker sessions are not currently sending.
2. Back up the SQLite database from Settings.
3. Apply THIS ZIP via PCC Update / Patch ZIP. Do not extract it into a subfolder.
4. Start MEDMH normally and connect the WhatsApp worker until state READY.
5. In Leads, inspect the test lead's chat and Latest message status.
   Previously stored 'sent' labels from older software are not retroactively
   changed because we have no safe way to know whether the message reached
   WhatsApp at that time.
6. For a CV marked 'uncertain', visually verify on the recipient's WhatsApp
   that the PDF was NOT received. Then use AI Workers > Controls > Media send
   status > Allow future AI resend. The patch does NOT clear or blindly retry
   old uncertain sends. If previous lead was handed off, Resume after handoff.
7. Test with ONE authorized contact. Send a greeting once, then request the CV
   once; allow configured batching/delays and up to 40 seconds for a server ACK.

EXPECTED LOGS
  [AI media dispatch] ... attachment ...
  [WhatsApp] send outcome ... server_ack_confirmed server_ack attachment:document
  [AI media result] ... sent attachment

IF TRANSPORT IS STILL BROKEN
  [WhatsApp] send outcome ... uncertain server_ack_timeout ...
  [AI send not confirmed] ... reason server_ack_timeout
  [AI media result] ... uncertain attachment

  These mean WhatsApp Web did not provide a server acknowledgement. It may
  have silently failed, or it may arrive later. The patch deliberately avoids
  claiming delivery or repeating a potentially delivered document. Inspect
  the worker Chrome WhatsApp tab; restart/re-attach the worker as appropriate.
  The local sandbox cannot test actual delivery on a Windows WhatsApp session.

SAFETY
- No changes to WhatsApp pairing data, uploaded media, user profiles, leads,
  account secrets, API keys, Chromium/Chrome profile, or existing database schema.
- No npm install, no patching node_modules, no automatic retries, and no new
  bulk outreach behavior.
- Outbound messages still respect worker serialization, assigned leads, and
  the existing CRM handoff rules.

OFFLINE TESTS
  python -m unittest discover -s tests -p test_ai_media_library.py -q   (23)
  python -m unittest discover -s tests -p test_ai_instruction_profiles.py -q (6)
  python -m unittest discover -s tests -p test_server_ack_delivery.py -q  (4)
  node whatsapp/compat/test_media_id_fix.js                         (4)
  node whatsapp/compat/test_message_events.js                      (passed)
  node whatsapp/compat/test_server_ack.js                           (5)
  node --check whatsapp/server.js                                   (passed)
  python -m py_compile ai_server.py leadfinder/ai_agent.py leadfinder/ai_workers.py

PATCH FILES
  ai_server.py
  leadfinder/ai_agent.py
  leadfinder/ai_workers.py
  whatsapp/server.js
  whatsapp/compat/server_ack.js
  whatsapp/compat/test_server_ack.js
  tests/test_ai_media_library.py
  tests/test_server_ack_delivery.py
  README_PATCH_WHATSAPP_DELIVERY_ACK.txt
