"""Pure presentation helpers, shared by the UI and regression checks."""
from __future__ import annotations

from datetime import date, datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


def as_date(value: Any, timezone: str = "Africa/Casablanca") -> date | None:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, datetime):
        return (value.astimezone(ZoneInfo(timezone)) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (result.astimezone(ZoneInfo(timezone)) if result.tzinfo else result).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def priority_name(value: Any) -> str:
    text = str(value or "").lower()
    return "Hot" if "hot" in text else "Warm" if "warm" in text else "Cold" if "cold" in text else ""


def lead_frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty:
        return pd.DataFrame(columns=[
            "id", "name", "city", "country", "niche", "phone", "phone_type",
            "crm_status", "lead_score", "data_confidence", "lead_priority",
            "manual_priority", "effective_priority", "website_type", "website_status",
            "archived", "do_not_contact", "next_followup", "deal_value", "owner",
        ])
    for field in ["name", "city", "country", "country_code", "niche", "phone", "address", "notes", "owner", "email", "website_url", "manual_priority"]:
        if field not in frame:
            frame[field] = ""
        frame[field] = frame[field].fillna("").astype(str)
    defaults = {"website_type": "Unknown", "website_status": "unknown", "phone_type": "UNKNOWN", "crm_status": "New", "lead_priority": "Cold"}
    for field, default in defaults.items():
        if field not in frame:
            frame[field] = default
        frame[field] = frame[field].fillna(default).replace("", default)
    # The literal website type "None" is a real enum, not a missing value.
    for field in ["lead_score", "data_confidence", "deal_value"]:
        if field not in frame:
            frame[field] = 0
        frame[field] = pd.to_numeric(frame[field], errors="coerce").fillna(0)
    for field in ["archived", "do_not_contact", "phone_valid", "has_facebook", "has_instagram"]:
        if field not in frame:
            frame[field] = False
        frame[field] = frame[field].fillna(False).map(lambda v: v == 1 or str(v).strip().lower() in {"1", "1.0", "true"})
    if "next_followup" not in frame:
        frame["next_followup"] = None
    frame["effective_priority"] = [priority_name(m) or priority_name(p) or "Cold" for m, p in zip(frame["manual_priority"], frame["lead_priority"])]
    return frame


def filter_leads(frame: pd.DataFrame, *, search: str = "", countries: list | None = None,
                 cities: list | None = None, niches: list | None = None,
                 statuses: list | None = None, website_types: list | None = None,
                 phone_types: list | None = None, show_archived: bool = False,
                 show_do_not_contact: bool = True) -> pd.DataFrame:
    result=frame.copy()
    if result.empty:return result
    if not show_archived:result=result[~result['archived']]
    if not show_do_not_contact:result=result[~result['do_not_contact']]
    for field,values in [('country',countries),('city',cities),('niche',niches),
                         ('crm_status',statuses),('website_type',website_types),('phone_type',phone_types)]:
        if values:result=result[result[field].isin(values)]
    if search.strip():
        mask=pd.Series(False,index=result.index,dtype=bool)
        for field in ['name','city','country','niche','phone','phone_raw',
                      'phone_e164','address','notes','email']:
            if field in result:
                mask |= result[field].fillna('').astype(str).str.contains(
                    search.strip(),case=False,regex=False,na=False)
        result=result[mask]
    return result




def safe_csv(frame: pd.DataFrame) -> bytes:
    export = frame.copy()
    for field in export.columns:
        def format_cell(value):
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=False, default=str)
            if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
                return "'" + value
            return value
        export[field] = export[field].map(format_cell)
    return export.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def clean_cell(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (dict, list)):
        return value
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def edit_changes(original: pd.DataFrame, edited: pd.DataFrame, editable: list[str]) -> list[tuple[int, dict]]:
    original_by_id = original.set_index("id", drop=False)
    changes = []
    for _, row in edited.iterrows():
        lead_id = int(row["id"])
        if lead_id not in original_by_id.index:
            continue
        delta = {}
        for field in editable:
            new, old = clean_cell(row.get(field)), clean_cell(original_by_id.loc[lead_id, field])
            if new != old and not (new in ("", None) and old in ("", None)):
                delta[field] = new
        if delta:
            changes.append((lead_id, delta))
    return changes
