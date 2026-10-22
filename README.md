# MedMH AI Workforce v3.9.1 — full consolidated Windows project

This is a complete clean project, **not a patch**. It combines the v3.9 Message Queue with all subsequent queue/installer fixes through verified `@lid` support. No databases, phone lists, credentials, Gemini keys or WhatsApp browser sessions are included.

## Install / start (Windows 10/11)

1. Extract the ZIP to a **new** folder, not over an existing installation.
2. Double-click **INSTALL.bat** once. It selects a compatible installed 64-bit CPython (3.11–3.14), installs prebuilt Python packages, Playwright Chromium, npm dependencies and the WhatsApp Web compatibility layer. Internet is required for the first install. Installation details go to **INSTALL_LOG.txt**.
3. Double-click **START.bat**. Keep that window open.
4. Open **http://127.0.0.1:8502** (local-only dashboard, no login).
5. Go to **AI Workers**. Create a worker, enter Gemini API keys (one per line), test/select a working model, open the worker's Chrome window, sign in to WhatsApp and attach AI. Only status **ready** can send.
6. Go to **Message Queue**, choose that worker, paste your numbers, type the message, and choose a country. Click **Normalize numbers locally & preview**. Review the prepared batch, then click **Start**.

## Standalone Message Queue

- **No Leads or CRM records are created.** Number lists and sent history are saved separately in SQLite.
- The queue uses the installed `phonenumbers` Python library to **normalize numbers locally without any Gemini call**. Your outgoing message remains unchanged. The worker's Gemini model remains relevant for AI conversation replies, not for preparing the queue.
- Original numbers, validated international numbers, and WhatsApp's **verified chat ID** are displayed. The ID is not guessed: after lookup it may be `212...@c.us` or an opaque `...@lid`.
- The WhatsApp worker must be **ready**. The number is checked for registration through its existing WhatsApp Web connection before sending.
- Each worker sends **one message at a time**, sharing its sending slot with AI replies. The next recipient uses the configured random interval; worker daily limits also apply.
- Queue controls: **Start, Pause, Resume, Cancel**. The independent `queue_runner.py` process saves its heartbeat, PID, database path and errors to a local status file.
- In-flight/uncertain sends are **never automatically resent** after failure. Repeated numbers are shown and retained as distinct entries—inspect them carefully before starting.
- Global **do-not-contact** requests are still respected. Public availability of a number alone is not permission for automated marketing; use this feature for recipients you're authorized to message.


## Phone matching and AI usage

- Incoming WhatsApp messages are matched to a lead **locally**, using the saved lead's ISO country and canonical E.164 number. Local forms (`06...`), explicit international prefixes (`00212...`, `+212...`) and spaced/formatted numbers are resolved without Gemini. Both `phone` and `phone_e164` are checked; conflicting valid identities are rejected rather than guessed.
- The match is scoped to leads **assigned to that WhatsApp worker**. An ambiguous duplicate number is not auto-linked. Existing opt-outs, permissions and reply gating remain in force. A successful match may still lead to a Gemini call to **write the actual conversational reply**.
- An `@lid` identifier is **not a phone number**. A message using it requires a previously linked chat or a number verified by WhatsApp's identity-mapping APIs. Never substitute the digits of an `@lid` ID for a phone number.
- The standalone Message Queue also normalizes with `phonenumbers` locally and does not need a Gemini model for queue preparation. Its WhatsApp registration checks and sending still require a ready worker.

### Matching regression tests

After installing the project dependencies, run `python -m unittest discover -s tests -p test_phone_normalization.py -v` from the project root. These tests use only temporary in-memory SQLite data; they do not contact Gemini, WhatsApp, or real recipients.

## Diagnostic notes

- `Unsupported WhatsApp ID`: the original queue rejected WhatsApp's `@lid` IDs. This version accepts `@lid` only when returned by the verified number lookup, and still rejects mismatched `@c.us` phone numbers.
- If queue stays pending, check that worker is **ready**, the queue is running, and the dashboard's dispatcher heartbeat/PID and per-recipient **Detail** fields.
- Old `.venv` failures: this installer verifies installed Python and prefers binary wheels instead of trying to compile pandas. Windows Node.js version checking does not use nested JavaScript quoting.
- Closing **START.bat** stops the helper services; the dashboard can retain historical queue records but cannot send without them.

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
