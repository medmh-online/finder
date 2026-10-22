MEDMH AI WORKFORCE v3.9.1 — SAFE SOURCE CLEANUP / GATEWAY CONSOLIDATION

PURPOSE
Reduce provably redundant source without removing application features or private data. This patch is based on the 2026-10-22 PCC v1.0.68.7 handoff. Apply to that matching installation; preview with PCC dry update first.

WHAT CHANGED
- Kept a single canonical Node.js WhatsApp gateway in whatsapp/server.js.
- Preserved the old duplicate gateway's previously inactive, bounded verified @lid recovery logic in the canonical gateway. No new outbound messaging is introduced by the identity lookup.
- Removed the unlaunched, redundant leadfinder/whatsapp/server.js.
- Extended .gitignore for generated installer output, queue heartbeat, local virtual environments, Streamlit secrets, and WhatsApp session backups.
- Updated README.md with authoritative entry points, data-safety notes, and quick checks.

FILES INCLUDED (complete project-relative files)
whatsapp/server.js
.gitignore
README.md
README_PATCH_MEDMH_SAFE_CLEANUP.txt
PCC_DELETE.txt
PCC_COMMANDS.json

FILES DELETED
leadfinder/whatsapp/server.js

COMMANDS (PCC_COMMANDS.json)
1. [required, validation, cwd=whatsapp] npm run check (timeout 120s)

PERSISTENT COMMANDS
None. Do not restart or create duplicate WhatsApp gateway processes automatically.

DATABASE
No migrations or schema changes. No .db, tokens, session profiles, logs, backups, messages, keys, or queue data in this patch.

DOCUMENTATION
README.md updated to explain canonical entry points and cleanup/data exclusions.

MANUAL TESTING
1. In PCC Normal Mode, run dry update; inspect exactly one deletion and no SQL.
2. Stop running app processes before applying to avoid running a stale Node.js process with the old source.
3. Apply update; confirm npm run check passes.
4. Start START.bat and open http://127.0.0.1:8502; verify Overview, Discovery, Leads, AI Workers, Message Queue, Settings all load.
5. With an already-authorized WhatsApp worker, verify reconnect/ready, an existing @c.us conversation, and an assigned @lid contact, if one is available. Never test sends to unconsenting contacts.
6. Confirm stored leads, campaigns, keys, event queue and histories remain intact.
7. Inspect git status before any push.

ROLLBACK
PCC should restore the previous whatsapp/server.js and deleted legacy file from its recovery snapshot if the required command fails. If using Quick Mode, be aware that a successful patch may be accepted/pushed automatically; use Normal Mode for a first live trial. A previous PCC version had a rollback defect; use the fixed version and retain a separate project backup.

LEFT UNTOUCHED INTENTIONALLY
INSTALL_LOG.txt is an existing installer log, queue_dispatcher_status.json is live queue state, PROPOSED_PRICING_FOR_BUSINESS_KNOWLEDGE.txt is user business reference material, and all credentials, SQLite DBs, events, backups, and sessions are preserved. Ignores only affect untracked files. No UI features, jobs, worker logic, or lead records are removed.
