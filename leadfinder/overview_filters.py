"""Single local-time date window applied consistently to the Overview reports."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd


def date_window(start: date | None, end: date | None, tz_name: str):
    if start and end and start > end:
        raise ValueError('From must be earlier than or equal to To.')
    tz = ZoneInfo(tz_name)
    low = datetime.combine(start, time.min, tz).astimezone(timezone.utc).timestamp() if start else 0.0
    high = datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(timezone.utc).timestamp() if end else 99999999999.0
    return (low, high)


def filter_leads_by_created_at(frame: pd.DataFrame, start: date | None, end: date | None, tz_name: str):
    """Created-time cohort; current CRM values are not claimed as historical snapshots."""
    if (start is None and end is None) or frame.empty:
        return frame
    if 'created_at' not in frame.columns:
        return frame.iloc[0:0]
    low, high = date_window(start, end, tz_name)
    created = pd.to_datetime(frame['created_at'], utc=True, errors='coerce', format='mixed')
    ts = created.map(lambda x: x.timestamp() if pd.notna(x) else float('nan'))
    return frame.loc[ts.ge(low) & ts.lt(high)]
