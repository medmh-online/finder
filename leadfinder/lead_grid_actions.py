"""Pure helpers for a single editable Leads grid.

Streamlit sends edits by displayed row index, not by lead ID. Always resolve
those indices using the exact snapshot rendered with the widget. The command
column is ephemeral and never persisted as a CRM field.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

NO_ACTION = "—"
ACTIONS = (
    NO_ACTION, "Chat", "Start discussion", "Pause AI", "Resume AI",
    "Human takeover", "Complete & hand off", "Resume after handoff", "Block outreach", "Archive", "Restore", "Delete permanently",
)


def collect_operations(widget_state: object, rows: Sequence[Mapping], valid_workers: Mapping[str, str]):
    """Return ordered (lead_id, worker_id_or_none, action_or_none) requests.

    A missing worker edit is not a request to unassign. A blank worker edit is.
    Work with the captured row snapshot to avoid applying edits to the wrong
    lead after sorting or pagination changes.
    """
    if not isinstance(widget_state, Mapping):
        return []
    patches = widget_state.get("edited_rows") or {}
    if not isinstance(patches, Mapping):
        return []
    operations = []
    for index, snapshot in enumerate(rows):
        patch = patches.get(index, patches.get(str(index), {}))
        if not isinstance(patch, Mapping):
            continue
        worker_id = None
        changed_worker = False
        if "Worker" in patch:
            selected = str(patch["Worker"])
            if selected not in valid_workers:
                raise ValueError("The selected worker is no longer available; refresh the Leads table.")
            if selected != snapshot.get("Worker", "Unassigned"):
                changed_worker = True
                worker_id = valid_workers[selected]
        action = patch.get("Action", NO_ACTION)
        if action not in ACTIONS:
            raise ValueError("Unknown action; refresh the Leads table.")
        if changed_worker or action != NO_ACTION:
            operations.append((int(snapshot["id"]), changed_worker, worker_id, action if action != NO_ACTION else None))
    return operations


def state_label(assignment, worker_enabled=True):
    if not assignment:
        return "Unassigned"
    if assignment.get("handoff"):
        return "Developer follow-up" if assignment.get('handoff_reason') in ('qualified','requested_human') else "Human takeover"
    if assignment.get("paused"):
        return "Paused"
    return "AI on" if worker_enabled else "Worker off"
