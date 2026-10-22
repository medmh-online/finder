# MedMH AI Workforce v3.9.1 — full consolidated Windows project

This is a complete clean project, **not a patch**. It combines the v3.9 Message Queue with all subsequent queue/installer fixes through verified `@lid` support. No databases, phone lists, credentials, Gemini keys or WhatsApp browser sessions are included.

## Install / start (Windows 10/11)

1. Extract the ZIP to a **new** folder, not over an existing installation.
2. Double-click **INSTALL.bat** once. It selects a compatible installed 64-bit CPython (3.11–3.14), installs prebuilt Python packages, Playwright Chromium, npm dependencies and the WhatsApp Web compatibility layer. Internet is required for the first install. Installation details go to **INSTALL_LOG.txt**.
3. Double-click **START.bat**. Keep that window open.
4. Open **http://127.0.0.1:8502** (local-only dashboard, no login).
5. Go to **AI Workers**. Create a worker, enter Gemini API keys (one per line), test/select a working model, open the worker's Chrome window, sign in to WhatsApp and attach AI. Only status **ready** can send.
6. Go to **Message Queue**, choose that worker, paste your numbers, type the message, and choose a country. Click **Normalize numbers with Gemini & preview**. Review the prepared batch, then click **Start**.

## Standalone Message Queue

- **No Leads or CRM records are created.** Number lists and sent history are saved separately in SQLite.
- Gemini uses the selected worker's existing key pool and model for **phone-number normalization**, not message rewriting. Your outgoing message remains unchanged.
- Original numbers, validated international numbers, and WhatsApp's **verified chat ID** are displayed. The ID is not guessed: after lookup it may be `212...@c.us` or an opaque `...@lid`.
- The WhatsApp worker must be **ready**. The number is checked for registration through its existing WhatsApp Web connection before sending.
- Each worker sends **one message at a time**, sharing its sending slot with AI replies. The next recipient uses the configured random interval; worker daily limits also apply.
- Queue controls: **Start, Pause, Resume, Cancel**. The independent `queue_runner.py` process saves its heartbeat, PID, database path and errors to a local status file.
- In-flight/uncertain sends are **never automatically resent** after failure. Repeated numbers are shown and retained as distinct entries—inspect them carefully before starting.
- Global **do-not-contact** requests are still respected. Public availability of a number alone is not permission for automated marketing; use this feature for recipients you're authorized to message.

## Diagnostic notes

- `Unsupported WhatsApp ID`: the original queue rejected WhatsApp's `@lid` IDs. This version accepts `@lid` only when returned by the verified number lookup, and still rejects mismatched `@c.us` phone numbers.
- If queue stays pending, check that worker is **ready**, the queue is running, and the dashboard's dispatcher heartbeat/PID and per-recipient **Detail** fields.
- Old `.venv` failures: this installer verifies installed Python and prefers binary wheels instead of trying to compile pandas. Windows Node.js version checking does not use nested JavaScript quoting.
- Closing **START.bat** stops the helper services; the dashboard can retain historical queue records but cannot send without them.

## Data safety

Fresh-install ZIP contains no old databases, accounts, keys or sessions. A new folder does **not** migrate previous data. Preserve your existing project folder as a backup. To migrate an existing installation, make a backup first and migrate its user-created local data and WhatsApp profiles carefully rather than deleting them.

## Testing and limitations

Source files pass Python bytecode compilation and Node.js syntax checks. A focused isolated test confirmed SQLite batch creation, lookup→verified `@lid`→send→sent status, no duplicate resend, mismatched ID rejection and additive migration. **Live Gemini API calls, QR pairing and actual WhatsApp delivery could not be tested here**, so test using one authorized phone number on your PC before larger batches. `whatsapp-web.js` is unofficial and can break with WhatsApp Web updates.
