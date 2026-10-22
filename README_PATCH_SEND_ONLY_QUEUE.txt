MedMH AI Workforce - PCC SEND-ONLY MESSAGE QUEUE UPDATE

Changes
- Message Queue is now a one-click send form: worker, numbers, country,
  message, and min/max seconds between each recipient.
- Removed Queue monitoring history table, Inspect batch selector, historical
  recipient list, Refresh queue button and historical manual-review controls.
- Shows only live current-batch progress and Pause/Resume/Cancel.
- Automatically removes completed/cancelled standalone message batches,
  their recipient rows and messages, including existing old completed history.
- Stale unsent paused previews expire after 24 hours. Existing running and
  non-stale paused jobs remain accessible for safe continuation.
- Retains only short-lived anonymous daily send attempt counters; sending
  limits remain effective after historical rows are removed.
- Existing WhatsApp sessions, workers, Gemini keys, Leads, AI conversations,
  and global blocked contacts are untouched.

Source files included
- leadfinder/queue_ui.py
- leadfinder/message_queue.py
- tests/test_send_only_queue.py
- README.md
- README_PATCH_SEND_ONLY_QUEUE.txt
- PCC_COMMANDS.json

PCC deletion files: None.
PCC commands:
  python -m compileall -q leadfinder  (cwd: .)
Database: automatically creates standalone_queue_daily_usage during queue
startup and prunes historical terminal batches. No raw SQL patch.
Persistent commands: None.

IMPORTANT
- The user asked not to retain old Message Queue history. The first run
  PERMANENTLY REMOVES prior completed/cancelled standalone batch records in
  the live SQLite database. Back up the database first if those records matter.
- Active sending requires temporary SQLite state for crash-safe pacing and
  to avoid duplicate sends; active recipients still temporarily exist in DB.
- Pre-existing backups/WAL/system copies may still contain historical data.
- Unconfirmed send attempts remain non-retriable automatically.

Testing
1. Stop MedMH completely and back up the existing leads.db and related WAL
   files if old history must be retained.
2. In PCC, select your MedMH project and dry update, then apply this ZIP.
3. Restart START.bat; open http://127.0.0.1:8502 -> Message Queue.
4. Verify there is no Queue monitoring table and no old batch inspector.
5. Connect a READY worker. Enter one authorized test number and short message,
   set interval and choose Send messages.
6. Confirm current progress and pause/resume/cancel work; completed batch
   disappears and cannot be browsed; daily count remains unchanged.
7. Run `python -m unittest discover -s tests -p test_send_only_queue.py -v`
   after installing requirements; no live messaging occurs during tests.

Rollback
- PCC source rollback restores prior page logic, but automatically deleted
  historical SQLite rows CANNOT be recovered by rolling back code alone.
- Restore a separate database backup if you need the deleted history.
- Avoid testing with valuable paused or active batches and confirm only one
  queue runner is operating.
