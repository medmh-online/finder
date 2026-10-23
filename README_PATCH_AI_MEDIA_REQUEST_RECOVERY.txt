MEDMH AI WORKFORCE — AI MEDIA REQUEST RECOVERY / DIAGNOSTICS
PCC update-only patch, 2026-10-23

PREREQUISITES
- Apply on top of the previously installed
  MedMH_AI_MEDIA_DELIVERY_INTEGRITY_PCC_PATCH.zip.
- Patch ZIP root matches the MEDMH project root.
- No npm install, schema migration, Chrome reset, or session logout required.
- Fully stop all MEDMH processes before applying; back up your SQLite DB.

WHAT THIS PATCH CORRECTS
- When Gemini says it cannot attach an AVAILABLE file, or proposes a premature
  handoff instead of responding to a request for an eligible uploaded file,
  the app requests one independent, focused Gemini media-routing decision.
- If that decision matches an eligible attachment, the backend selects its
  opaque saved media ID, queues the attachment, and uses a neutral caption.
- A CV request by itself continues the AI conversation; a real request for an
  interview, human contact, or personal decision still triggers a handoff.
- New Controls UI: "Check why a lead cannot receive uploaded media".
  Enter the lead ID to see active-profile files, missing/corrupt files, old
  sent/queued/uncertain attempts, profile mismatch, and handoff/paused state.
- Every selected file still passes existing profile isolation, SHA-256, lead
  status, WhatsApp worker ownership, and duplicate-send checks.
- No automatic WhatsApp retry after unconfirmed sends, avoiding duplicates.

HOW TO USE
1. Restart and go to AI Workers > AI profiles and quotas.
2. Choose the same profile active on your WhatsApp worker (Job Hunting).
3. Verify your CV PDF exists and is ENABLED; upload it there if absent.
4. Go to AI Workers > Controls > Check why a lead cannot receive uploaded media.
5. Enter the test lead ID (e.g. 77) and click "Check attachment readiness".
   - "No file" means upload to the ACTIVE profile.
   - "already_sent_or_pending: uncertain" means verify the actual recipient
     WhatsApp chat. If not delivered, use the EXISTING "Media send status"
     unlock control. Unlock never sends anything itself.
   - "lead_handoff" means manually "Resume after handoff" in Leads before
     further testing, but ONLY for your own authorized test contact.
   - "thread profile mismatch" means the conversation is pinned to another
     AI instruction profile; switch profiles or use a fresh test lead.
6. Ask the authorized test contact to request the CV again.
7. Observe [AI attachment review], [AI attachment decision], [AI media dispatch],
   [AI media result] and [WhatsApp] send outcome console lines.
8. Visually verify the actual PDF bubble in WhatsApp, not just text.

IMPORTANT LIMITATIONS
- This patch does NOT fix an absent uploaded PDF, an incompatible WhatsApp Web
  library, an offline worker, or a corrupt/unreadable file. It makes the cause
  easier to diagnose and reduces Gemini's false excuses / premature handoffs.
- If an older send is marked 'sent' or 'uncertain', it is hidden from automatic
  resending until appropriately handled. Do NOT re-send before checking chat.
- Gemini still makes the semantic attachment decision; the application never
  sends an unrelated file or uses arbitrary file paths given by the model.
- There is one extra Gemini decision only when media is available AND the first
  decision looks inconsistent or proposes a handoff. It counts toward quotas.
- There is NO change to stored CVs, other uploaded files, tokens, keys, leads,
  databases or WhatsApp sessions.
- Offline tests only; no actual connected Windows WhatsApp send was performed.

CHANGED FILES
  leadfinder/ai_agent.py
  leadfinder/ai_workers.py
  leadfinder/ai_ui.py
  tests/test_ai_media_library.py

TESTS
  .venv\\Scripts\\python.exe -m unittest discover -s tests -p test_ai_media_library.py -v
  .venv\\Scripts\\python.exe -m unittest discover -s tests -p test_ai_instruction_profiles.py -v
  18 media tests + 6 profile tests passed in isolated test runtime.
