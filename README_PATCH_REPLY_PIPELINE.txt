MedMH AI Workforce — Incoming Reply Reliability and Diagnostics

Modified files (full replacements):
- leadfinder/ai_workers.py — persistent reply trace and bounded retry cooldown,
  inbound resets retry, queued/sent delivery stages, additive schema upgrade.
- leadfinder/ai_agent.py — increased Gemini structured-reply output budget,
  malformed JSON/empty replies remain pending instead of being ignored;
  first-time ignored greeting is re-evaluated once by Gemini; failure backoff.
- leadfinder/reply_diagnostics.py — shows exact per-lead pipeline stage and
  cooldown so operators can distinguish unmatched/blocked/pending/Gemini failure.
- README.md — documents troubleshooting and behavior.
- tests/test_ai_reply_pipeline.py — isolated offline regression tests.

PCC_DELETE.txt: none
PCC_COMMANDS.json: python -m compileall -q leadfinder (required, working dir .)
Persistent commands: none.
Database: automatic additive SQLite migration: ai_threads.retry_after and
ai_reply_trace table. Existing leads, keys, WhatsApp sessions and queues
are retained; no raw SQL patch required.

Testing:
- Stop MedMH and back up leads.db before PCC update. Apply in Normal Mode.
- Restart via START.bat and verify worker is READY and enabled, and a model
  is selected; send a fresh greeting from a different WhatsApp account.
- Open AI Workers > WhatsApp browser > Why no AI reply? > Diagnose.
- If no linked inbound and unmatched @lid messages exist, use the existing
  WhatsApp-verified identity retry; no guessed phone mappings.
- If trace says retry scheduled, examine Gemini model/key/budget. No automatic
  retry storm; after cooldown a retry may generate a reply.
- If trace says queued but not sent, inspect WhatsApp status and START console.
- If trace says sent, WhatsApp gateway confirmed a message ID, not final delivery.

Security: no new outreach, no auto-assignment, no resending of unconfirmed sends.
Rollback: use PCC snapshot to restore old source. Additive SQLite fields and
trace table remain harmless after source rollback. Existing customer data intact.

Limitations: live WhatsApp/Gemini integration cannot be verified from a static
project handoff. A no-reply symptom alone does not establish the exact cause.
