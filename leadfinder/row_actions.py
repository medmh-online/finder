"""Stable row identities and draft handling for the lead table.

These helpers have no Streamlit or database side effects. Widget row numbers
are resolved only against the snapshot that created that widget.
"""
from __future__ import annotations

from copy import deepcopy
from numbers import Integral
from typing import Any, Mapping, Sequence

from .views import as_date, clean_cell


TABLE_EDITABLE = (
    "name", "niche", "city", "address", "phone", "email", "website_url", "notes",
    "crm_status", "deal_value", "established_year",
)
QUICK_EDITABLE = ("crm_status", "phone", "notes")
ROW_LABELS = {
    "View": "view", "Edit": "edit", "Contact / links": "more",
    "Archive": "archive", "Restore": "restore", "Delete permanently": "delete",
}


def comparable(value: Any, field: str, timezone: str = "Africa/Casablanca") -> Any:
    if field == "next_followup":
        return as_date(value, timezone)
    value = clean_cell(value)
    if field in {"phone", "manual_priority", "crm_status", "owner", "notes"}:
        return str(value or "")
    return value


def field_changes(baseline: Mapping, values: Mapping, fields: Sequence[str],
                  timezone: str = "Africa/Casablanca") -> dict:
    changes = {}
    for field in fields:
        new = comparable(values.get(field), field, timezone)
        old = comparable(baseline.get(field), field, timezone)
        if new != old:
            changes[field] = new.isoformat() if field == "next_followup" and new else new
    return changes


def baseline_conflicts(baseline: Mapping, changes: Mapping, latest: Mapping,
                       timezone: str = "Africa/Casablanca") -> list[str]:
    """Allow unrelated concurrent edits and already-applied identical values."""
    return [field for field, wanted in changes.items()
            if comparable(latest.get(field), field, timezone) != comparable(baseline.get(field), field, timezone)
            and comparable(latest.get(field), field, timezone) != comparable(wanted, field, timezone)]


def quick_values(lead: Mapping, timezone: str = "Africa/Casablanca") -> dict:
    values = {field: comparable(lead.get(field), field, timezone) for field in QUICK_EDITABLE}
    values["phone"] = str(lead.get("phone_raw") or lead.get("phone") or "")
    return values


def resolve_row_click(snapshot_ids: Sequence[int], payload: Any) -> tuple[int, str] | None:
    try:
        row, label = payload["row"], payload["label"]
    except (KeyError, TypeError, IndexError, AttributeError):
        return None
    if isinstance(row, bool) or not isinstance(row, Integral) or not 0 <= row < len(snapshot_ids):
        return None
    if not isinstance(label, str) or label not in ROW_LABELS:
        return None
    return int(snapshot_ids[row]), ROW_LABELS[label]


def capture_table_edits(snapshot_rows: Sequence[Mapping], widget_state: Any,
                        drafts: Mapping[int, Mapping], selected_ids: Sequence[int],
                        timezone: str = "Africa/Casablanca", *, editable: Sequence[str] = TABLE_EDITABLE
                        ) -> tuple[dict[int, dict], list[int]]:
    """Capture a data_editor patch, keeping drafts from other pages intact.

    The input snapshot already contains any draft values shown in the editor.
    Its ``_baseline`` map retains the saved values when drafts were overlaid,
    including after a user reverts a draft and a callback captures it again.
    Selection changes never become database changes.
    """
    result = deepcopy(dict(drafts))
    selected = set(int(value) for value in selected_ids)
    try:
        patches = widget_state["edited_rows"] or {}
    except (KeyError, TypeError, IndexError, AttributeError):
        return result, sorted(selected)
    if not isinstance(patches, Mapping):
        return result, sorted(selected)
    for index, source in enumerate(snapshot_rows):
        lead_id = int(source["id"])
        patch = patches.get(index, patches.get(str(index), {}))
        patch = patch if isinstance(patch, Mapping) else {}
        if bool(patch.get("Select", source.get("Select", False))):
            selected.add(lead_id)
        else:
            selected.discard(lead_id)
        if not editable:
            continue
        previous = result.get(lead_id, {})
        previous_baseline = previous.get("baseline", {})
        snapshot_baseline = source.get("_baseline", {})
        baseline = {field: previous_baseline.get(field, snapshot_baseline.get(field, source.get(field))) for field in editable}
        values = {field: patch.get(field, source.get(field)) for field in editable}
        changes = field_changes(baseline, values, editable, timezone)
        if changes:
            result[lead_id] = {"baseline": {field: baseline[field] for field in changes}, "changes": changes}
        else:
            result.pop(lead_id, None)
    return result, sorted(selected)
