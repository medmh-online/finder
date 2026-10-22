PCC TEST ONLY — MEDMH AI WORKFORCE v3.9.1
==================================================
This patch exercises PCC safely. Do not use Quick mode or auto-publish these disposable test fixtures.
No application source, config, API key, WhatsApp session, database, or user data is changed.
The ZIP root maps directly to the selected project root.
No persistent commands or database migrations.

STEP 1 / PREPARATION
Creates two disposable text files in the project root:
  PCC_TEST_EDIT_ME.txt
  PCC_TEST_DELETE_ME.txt

Commands: none. Deletions: none. Database: unchanged.
PCC should show both files as NEW, and offer its usual project backup and acceptance flow.

Test after accepting:
- Open the project folder; both files must exist.
- PCC_TEST_EDIT_ME.txt must contain state=before.
- PCC_TEST_DELETE_ME.txt must contain state=present.
- Next apply PCC_02_FULL_TEST.zip.

Rollback: use PCC history/restore to revert this ZIP.
README.md: no change needed; fixtures do not change installation or behavior.
