# MedMH AI Workforce v3.9.1 — full consolidated Windows project

This is a complete clean project, **not a patch**. It combines the v3.9 Message Queue with all subsequent queue/installer fixes through verified `@lid` support. No databases, phone lists, credentials, Gemini keys or WhatsApp browser sessions are included.

## Install / start (Windows 10/11)

1. Extract the ZIP to a **new** folder, not over an existing installation.
2. Double-click **INSTALL.bat** once. It selects a compatible installed 64-bit CPython (3.11–3.14), installs prebuilt Python packages, Playwright Chromium, npm dependencies and the WhatsApp Web compatibility layer. Internet is required for the first install. Installation details go to **INSTALL_LOG.txt**.
3. Double-click **START.bat**. Keep that window open.
4. Open **http://127.0.0.1:8502** (local-only dashboard, no login).
5. Go to **AI Workers**. Create a worker, enter Gemini API keys (one per line), test/select a working model, open the worker's Chrome window, sign in to WhatsApp and attach AI. Only status **ready** can send.
6. Go to **Message Queue**, choose the connected worker, enter authorized recipient numbers and a message, set the send interval, and click **Send messages**. Only the active batch appears while sending.

## Standalone Message Queue — send-only mode

- Message Queue is **not a CRM** and does not create Leads or store a completed batch history. The old **Queue monitoring** table, batch selector, manual delivery review, and saved recipient-history table in the dashboard have been removed.
- Choose a **ready** WhatsApp worker, enter recipients and a message, select country and random interval, then click **Send messages**. Phone normalization is local using `phonenumbers`; it does not call Gemini. Messages are sent sequentially, with the chosen random delay **between recipients**, sharing a send slot with AI replies.
- The page shows **only the current send**, with aggregate progress, Pause / Resume / Cancel, and a queue-runner health warning when needed. There is no completed-batch or recipient log to browse.
- Numbers and message content are stored **temporarily in SQLite while an active/paused batch is being processed**. This is necessary for configurable delays, restart-safe progress and preventing automatic repeats after uncertain outcomes. The app automatically deletes completed/cancelled batches and their recipient rows; unused paused previews expire after 24 hours. A cancelled in-flight send is kept until it resolves or is marked uncertain so it is never resent automatically.
- When first started after this update, the queue also deletes previously saved completed/cancelled batch history. This is intentional and cannot be undone through the UI; back up your database beforehand if you want to keep those older records outside MedMH.
- To keep daily safety limits correct after removing recipient rows, the application retains **only short-lived anonymous per-worker/day attempt counters** in `standalone_queue_daily_usage`. These contain no phone numbers, message bodies, or WhatsApp chat IDs. All-time send histories are not retained. The global do-not-contact ledger and other AI/CRM data are unchanged.
- The batch data may still be present in pre-existing database backups, SQLite WAL files or other system backups; removing the saved history in MedMH does not erase copies outside the live queue tables. The current batch is not stored only in browser memory.
- Only WhatsApp-verified IDs (`@c.us` or provider-confirmed `@lid`) are used for sends. Unconfirmed attempts are **not retried automatically**, and WhatsApp registration does not imply consent. Send only to recipients you are authorized to message.

### Send-only queue regression checks

Run `python -m unittest discover -s tests -p test_send_only_queue.py -v` to test cleanup and daily limits without contacting WhatsApp. A successful Python syntax check alone does not verify actual delivery.


## Phone matching and AI usage

- Incoming WhatsApp messages are matched to a lead **locally**, using the saved lead's ISO country and canonical E.164 number. Local forms (`06...`), explicit international prefixes (`00212...`, `+212...`) and spaced/formatted numbers are resolved without Gemini. Both `phone` and `phone_e164` are checked; conflicting valid identities are rejected rather than guessed.
- The match is scoped to leads **assigned to that WhatsApp worker**. An ambiguous duplicate number is not auto-linked. Existing opt-outs, permissions and reply gating remain in force. A successful match may still lead to a Gemini call to **write the actual conversational reply**.
- An `@lid` identifier is **not a phone number**. A message using it requires a previously linked chat or a number verified by WhatsApp's identity-mapping APIs. Never substitute the digits of an `@lid` ID for a phone number.
- The standalone Message Queue also normalizes with `phonenumbers` locally and does not need a Gemini model for queue preparation. Its WhatsApp registration checks and sending still require a ready worker.

### Matching regression tests

After installing the project dependencies, run `python -m unittest discover -s tests -p test_phone_normalization.py -v` from the project root. These tests use only temporary in-memory SQLite data; they do not contact Gemini, WhatsApp, or real recipients.

## Diagnostic notes

- `Unsupported WhatsApp ID`: the original queue rejected WhatsApp's `@lid` IDs. This version accepts `@lid` only when returned by the verified number lookup, and still rejects mismatched `@c.us` phone numbers.
- If an active send remains waiting, verify that the worker is **ready** and read the current-send dispatcher warning and START.bat console. Completed recipient-level detail is not saved.
- Old `.venv` failures: this installer verifies installed Python and prefers binary wheels instead of trying to compile pandas. Windows Node.js version checking does not use nested JavaScript quoting.
- Closing **START.bat** stops helper services. An active queue remains temporarily stored for safe resume, but completed queue history is removed.

## Data safety

Fresh-install ZIP contains no old databases, accounts, keys or sessions. A new folder does **not** migrate previous data. Preserve your existing project folder as a backup. To migrate an existing installation, make a backup first and migrate its user-created local data and WhatsApp profiles carefully rather than deleting them.

## Testing and limitations

Source files pass Python bytecode compilation and Node.js syntax checks. A focused isolated test confirmed SQLite batch creation, lookup→verified `@lid`→send→sent status, no duplicate resend, mismatched ID rejection and additive migration. **Live Gemini API calls, QR pairing and actual WhatsApp delivery could not be tested here**, so test using one authorized phone number on your PC before larger batches. `whatsapp-web.js` is unofficial and can break with WhatsApp Web updates.

## Source layout and services

- `START.bat` runs `run_ai.py`, which launches the Streamlit dashboard (`launch.py` / `app.py`), the Python AI bridge (`ai_server.py`, loopback port 8766), the queue runner (`queue_runner.py`), and the **one canonical** WhatsApp gateway (`whatsapp/server.js`, loopback port 8767).
- `leadfinder/` contains the shared Python modules for discovery, CRM, AI conversations, queues, and persistence. The previously duplicated `leadfinder/whatsapp/server.js` was consolidated into `whatsapp/server.js` and removed; do not launch or recreate the old copy.
- The canonical WhatsApp gateway includes bounded, verified `@lid` identity recovery for assigned contacts when WhatsApp cannot directly expose a sender phone. This checks assigned registration mappings and does not send messages itself.

## Local data and repository housekeeping

- Keep the existing `leads.db` and other SQLite databases, Gemini encryption keys, bridge token, WhatsApp sessions, Chrome worker profiles, and pending event files when updating an existing installation. These are installation-specific data, not disposable source files.
- `INSTALL_LOG.txt` is installer output. `queue_dispatcher_status.json` is a queue-runner heartbeat. Both are generated at runtime and ignored for **new** Git additions; the cleanup patch does not delete them.
- `.venv/`, `.streamlit/secrets.toml`, and `whatsapp/session_backups/` are also excluded from new Git additions. Git ignore rules do **not** automatically untrack files that were already committed; review tracked runtime files and their history before removing them from version control.
- `PROPOSED_PRICING_FOR_BUSINESS_KNOWLEDGE.txt` is a manual, unapproved pricing reference, not active application configuration; it was retained to avoid losing business notes. Never treat its estimates as approved client quotes.

## Source checks (no live WhatsApp or Gemini activity)

From the project root after installing dependencies, run `cd whatsapp` then `npm run check` to syntax-check the canonical WhatsApp gateway. Python source can be syntax-checked without starting workers with `python -m compileall -q app.py ai_server.py leadfinder`. These checks do not verify delivery, pairing, or API access.

## Clearing all leads (local dashboard)

On the **Leads** page choose **Clear all leads**, then type the exact displayed phrase (`DELETE N LEADS`). The operation includes archived and blocked leads. It deletes leads, their CRM history, AI assignments and linked conversations, while retaining search campaigns/job history, worker settings, Gemini keys, WhatsApp sessions, active standalone Message Queue batches (finished history is purged), token usage totals and the persistent installation-wide do-not-contact ledger. Historical job candidate references are cleared. Future search jobs can rediscover the same businesses.

Before clearing, stop/cancel queued or active Discovery jobs and disable AI workers with assigned leads. PCC updates alone do not restore an already-cleared live database; the action creates a verified SQLite backup in the project's `backups/` directory prior to deletion. A failed transactional clear preserves the original database and keeps its backup. Back up that directory securely if you need the original lead records later. Do not remove global blocked-contact records when resetting leads.


## Troubleshooting AI workers not replying

Open **AI Workers → WhatsApp browser → Why no AI reply? · Check a lead**.
Enter a lead phone number and its default country, then **Diagnose**. The tool checks
local phone normalization, duplicate/conflicting records, assignment to the selected worker,
AI enablement, model configuration, unmatched incoming events, and recent Gemini errors.
It does not call Gemini or send messages.

A lead must be explicitly assigned to the worker that receives WhatsApp messages.
A newly assigned lead should be tested with a **new incoming message from another WhatsApp account**.
The worker must report **ready**, not merely have a signed-in Chrome window.
An opaque `@lid` is not a phone number; only a WhatsApp-verified mapping can associate it with a lead.
If the message was received but remains unanswered, inspect the AI gateway console for errors.
"I can see the lead in Leads" alone does not establish message identity or a valid worker assignment.

## Overview date range and inbound-reply troubleshooting

The Overview page provides optional **From** and **To** dates, initially blank (**All time**). Dates use the configured local timezone; To includes the complete selected calendar day. Leads, pipeline, and website-quality charts filter by each lead's creation date and display the lead's **current** CRM stage, not a historical snapshot. Conversation and token reports filter by event timestamp, and the current AI worker counts/state remain current. Campaign performance remains **lifetime** and is explicitly labeled when dates are selected.

The two deprecated automatic operations have been removed from AI Worker Controls: automatic first introductions and automatic bulk WhatsApp registration/assignment. Their background schedulers are disabled; manual first contact remains subject to the application's existing consent protections. Incoming-message identity verification for assigned leads is still available, since it is needed for legitimate replies.

If a connected worker does not reply, open **AI Workers → WhatsApp browser → Why no AI reply? · Check a lead**. Verify the assigned worker and model, whether the inbound message is linked, and whether the last outgoing reply is pending, sent, or uncertain. If there are unmatched `@lid` messages, **Retry matching unmatched WhatsApp identities** performs a rate-limited verification using WhatsApp's reported identities. It never sends a message or infers a phone number from LID digits. If WhatsApp cannot verify the identity, the conversation is left unmatched and no AI reply is sent. A separate test WhatsApp account must send the inbound message; messages sent from the worker's own account are ignored.

The worker AI prompt now explicitly asks Gemini to answer a **first greeting** rather than silently ignore it. This is still subject to worker readiness, safe identity matching, Gemini availability, and conversation/opt-out rules. Use a test chat and check the console for `[reply]`, `[agent]`, and `[WhatsApp identity]` errors if it remains unanswered.

Offline test: `python -m unittest discover -s tests -p test_overview_date_filter.py -v`. The update does not change database schemas, saved worker keys, sessions, or customer data.
