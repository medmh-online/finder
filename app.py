#!/usr/bin/env python3
"""MedMH AI Workforce — local single-table leads dashboard."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import importlib.util
import hashlib
import json
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from leadfinder import __version__
from leadfinder.config import AppPaths, ConfigError, app_paths
from leadfinder.downloads import private_download
from leadfinder.local_workspace import LocalRepository as Repository, LocalWorkspace as WorkspaceAccess, WorkspaceError, PermissionDenied, SessionExpired
from leadfinder.quality import country_info, country_options
from leadfinder.views import as_date, clean_cell, filter_leads, lead_frame, safe_csv
from leadfinder.row_actions import (
    TABLE_EDITABLE, baseline_conflicts, capture_table_edits,
)


PAGES = ["Overview", "Discovery", "Leads", "AI Workers", "Message Queue", "Settings"]
ACTIVE_STATES = {"running", "pause_requested", "cancel_requested"}
LEAD_VIEW_KEYS = (
    "lead_filter_search", "lead_filter_countries", "lead_filter_cities", "lead_filter_niches",
    "lead_filter_statuses", "lead_filter_websites", "lead_filter_phones",
    "lead_filter_archived", "lead_filter_dnc",
    "lead_sort", "lead_page_size", "leads_page_number",
)
WEBSITE_LABELS = {
    "None": "No website", "Free": "Hosted website", "Subdomain": "Business subdomain",
    "Real": "Business website", "Social": "Social page only", "Unknown": "Not verified",
}


def notify(message: str, level: str = "success") -> None:
    st.session_state["_notice"] = (level, message)


def go_to(page: str, lead_id: int | None = None) -> None:
    capture_active_table()
    capture_lead_view()
    st.session_state["navigation"] = page
    if lead_id is not None:
        st.session_state["_lead_id"] = int(lead_id)
        st.session_state.pop("detail_lead_selector", None)
        reload_details(int(lead_id))


def go_to_lead_inline(lead_id: int) -> None:
    go_to("Leads")
    st.session_state["_lead_chat_dialog"] = int(lead_id)


def reload_details(lead_id: int) -> None:
    st.session_state.pop(f"_detail_snapshot_{lead_id}", None)
    key = f"_detail_revision_{lead_id}"
    st.session_state[key] = int(st.session_state.get(key, 0)) + 1


def reset_filters() -> None:
    capture_active_table()
    for key in list(st.session_state):
        if key.startswith("lead_filter_"):
            del st.session_state[key]
    st.session_state["leads_page_number"] = 1
    saved_view = st.session_state.get("_lead_view_state", {})
    for key in list(saved_view):
        if key.startswith("lead_filter_"):
            saved_view.pop(key, None)
    saved_view["leads_page_number"] = 1


def unique_lines(text: str, limit: int = 100) -> list[str]:
    output, seen = [], set()
    for line in text.splitlines():
        line = " ".join(line.strip().split())
        if line and line.casefold() not in seen:
            if len(line) > 200:
                raise ValueError("Keep each location or category under 200 characters.")
            output.append(line)
            seen.add(line.casefold())
    if len(output) > limit:
        raise ValueError(f"Use no more than {limit} entries at a time.")
    return output


def country_selector(label: str, current: str = "", key: str | None = None) -> str:
    countries = country_options()
    codes = [""] + sorted(countries, key=lambda code: countries[code])
    current = str(current or "").upper()
    return st.selectbox(label, codes, index=codes.index(current) if current in codes else 0,
                        format_func=lambda code: countries.get(code, "Choose a country"), key=key)


def current_workspace() -> WorkspaceAccess:
    return WorkspaceAccess()


def frame_for(repo: Repository) -> pd.DataFrame:
    return lead_frame(repo.list_leads(include_archived=True))


def short_table(frame: pd.DataFrame, columns: list[str], **kwargs) -> None:
    columns = [column for column in columns if column in frame]
    st.dataframe(frame[columns], hide_index=True, use_container_width=True, **kwargs)


def launch_pending(repo: Repository, paths: AppPaths) -> bool:
    if importlib.util.find_spec("playwright") is None:
        st.error("Collection needs Playwright. Install the requirements, then run: python -m playwright install chromium")
        return False
    try:
        current_workspace().launch_worker()
        return True
    except PermissionDenied:
        raise
    except (OSError, RuntimeError) as exc:
        st.error(f"The search is saved, but the worker could not start: {exc}")
        return False


def render_overview(repo: Repository, config: dict, paths: AppPaths) -> None:
    st.header("MedMH · Operations overview")
    st.caption("Live prospecting, pipeline and AI worker performance — all in one place.")
    all_leads = frame_for(repo)
    active = all_leads[~all_leads["archived"]] if not all_leads.empty else all_leads
    a,b,c,d=st.columns(4)
    a.metric('Active leads',len(active))
    b.metric('Interested',int(active['crm_status'].eq('Interested').sum()) if not active.empty else 0)
    c.metric('Won',int(active['crm_status'].eq('Won').sum()) if not active.empty else 0)
    d.metric('Outreach blocked',int(active['do_not_contact'].sum()) if not active.empty else 0)
    left,right=st.columns([1.7,1])
    with left:
        st.subheader('Recently updated businesses')
        if not active.empty:
            shortlist=active.sort_values(['updated_at','id'],ascending=[False,False]).head(12)
            short_table(shortlist,['name','city','phone','crm_status','updated_at','notes'],height=350)
        else:st.info('Start a discovery campaign to find businesses.')
    with right:
        st.subheader('Quick access')
        st.button('Leads & conversations',on_click=go_to,args=('Leads',),use_container_width=True)
        st.button('Discovery',on_click=go_to,args=('Discovery',),use_container_width=True)
        st.subheader('Recent search jobs')
        jobs=repo.list_jobs(limit=5)
        if jobs:
            st.dataframe(pd.DataFrame([{'Job':j['id'],'Status':j['state'],
                'New leads':(j.get('progress') or {}).get('saved',0)} for j in jobs]),
                hide_index=True,use_container_width=True)
        else:st.caption('No searches yet.')


def search_settings_fields(settings: dict, prefix: str) -> dict:
    result = deepcopy(settings)
    a, b, c = st.columns(3)
    result["target_leads"] = int(a.number_input("New leads target", 1, 10000, int(settings.get("target_leads", 25)), key=f"{prefix}_target", help="Cumulative target across all locations and categories in this job."))
    result["max_candidates"] = int(b.number_input("Candidate budget per run", 1, 50000, int(settings.get("max_candidates", 500)), key=f"{prefix}_candidates", help="A resumed job gets another bounded run. Previously processed candidates remain recorded."))
    result["max_minutes"] = int(c.number_input("Minutes per run", 1, 1440, int(settings.get("max_minutes", 20)), key=f"{prefix}_minutes"))
    a, b = st.columns(2)
    result["only_without_website"] = a.checkbox("Focus on businesses without a standalone website", value=bool(settings.get("only_without_website", True)), key=f"{prefix}_website")
    result["include_hosted_sites"] = b.checkbox("Also include hosted website platforms", value=bool(settings.get("include_hosted_sites", True)), key=f"{prefix}_hosted")
    result["require_phone"] = a.checkbox("Require a valid phone number", value=bool(settings.get("require_phone", True)), key=f"{prefix}_phone")
    result["refresh_existing"] = b.checkbox("Refresh businesses already in the database", value=bool(settings.get("refresh_existing", False)), key=f"{prefix}_refresh", help="Refresh observed details while retaining CRM history and contact preferences.")
    result["collect_review_sample"] = False
    result["headless"] = b.checkbox("Run the browser in the background", value=bool(settings.get("headless", True)), key=f"{prefix}_headless")
    a, b, c = st.columns(3)
    result["delay_seconds"] = float(a.number_input("Delay between businesses (seconds)", 0.5, 60.0, float(settings.get("delay_seconds", 2)), 0.5, key=f"{prefix}_delay"))
    result["cache_hours"] = int(b.number_input("Reuse checked observations for (hours)", 0, 8760, int(settings.get("cache_hours", 168)), key=f"{prefix}_cache"))
    result["timeout_seconds"] = int(c.number_input("Page timeout (seconds)", 5, 120, int(settings.get("timeout_seconds", 30)), key=f"{prefix}_timeout"))
    return result


def render_campaigns(repo: Repository, config: dict, paths: AppPaths) -> None:
    st.subheader("Campaign builder")
    st.caption("Group related locations and business categories. Every combination becomes a search with an explicit country.")
    campaigns = repo.list_campaigns()
    by_id = {int(c["id"]): c for c in campaigns}
    selected = st.selectbox("Campaign", [0] + list(by_id), format_func=lambda value: "Create a new campaign" if value == 0 else by_id[value]["name"], key="campaign_selector")
    campaign = by_id.get(selected, {})
    defaults = {**config["search_defaults"], **(campaign.get("settings") or {})}
    with st.form(f"campaign_form_{selected}"):
        name = st.text_input("Campaign name", value=campaign.get("name", ""), max_chars=120, placeholder="Website prospects — selected cities")
        country_code = country_selector("Country", campaign.get("country_code", ""), key=f"campaign_country_{selected}")
        left, right = st.columns(2)
        locations_text = left.text_area("Cities or districts — one per line", value="\n".join(campaign.get("locations") or []), height=140, placeholder="Casablanca\nRabat", key=f"campaign_locations_{selected}")
        niches_text = right.text_area("Categories or keyword variants — one per line", value="\n".join(campaign.get("niches") or []), height=140, placeholder="dentists\ndental clinics", key=f"campaign_niches_{selected}")
        st.subheader("Qualification and limits")
        settings = search_settings_fields(defaults, f"campaign_{selected}")
        left, right = st.columns(2)
        save_only = left.form_submit_button("Save campaign", use_container_width=True)
        save_and_start = right.form_submit_button("Save and start search", type="primary", use_container_width=True)
    if save_only or save_and_start:
        try:
            if not name.strip():
                raise ValueError("Give the campaign a name.")
            if not country_code:
                raise ValueError("Choose a country so searches and phone normalization use the correct region.")
            locations, niches = unique_lines(locations_text, 100), unique_lines(niches_text, 50)
            if not locations or not niches:
                raise ValueError("Add at least one location and one business category.")
            if len(locations) * len(niches) > 500:
                raise ValueError("Use at most 500 location/category combinations in one campaign.")
            info = country_info(country_code)
            data = {"name": name.strip(), **info, "locations": locations, "niches": niches, "settings": settings}
            if selected:
                data["id"] = selected
            campaign_id = repo.save_campaign(data)
            if save_and_start:
                queries = [{"city": city, "niche": niche, **info} for city in locations for niche in niches]
                job_id = repo.create_job(campaign_id=campaign_id, queries=queries, settings=settings)
                if launch_pending(repo, paths):
                    notify(f"Campaign saved. Job #{job_id} is queued and its worker has been started.")
                    st.session_state["_selected_job"] = job_id
                    st.rerun()
            else:
                notify("Campaign saved.")
                st.rerun()
        except PermissionDenied:
            raise
        except (ValueError, OSError, sqlite3.Error) as exc:
            st.error(str(exc))
    if campaigns:
        st.subheader("Saved campaigns")
        st.dataframe(pd.DataFrame([{"Name": c["name"], "Country": c.get("country", ""), "Locations": len(c.get("locations") or []), "Categories": len(c.get("niches") or []), "Search combinations": len(c.get("locations") or []) * len(c.get("niches") or [])} for c in campaigns]), hide_index=True, use_container_width=True)
    st.caption("Saved campaigns and their collection jobs are managed on this page.")


@st.fragment(run_every=3.0)
def job_monitor(repo: Repository, config: dict, paths: AppPaths) -> None:
    try:
        render_job_monitor(repo, config, paths)
    except (PermissionDenied, SessionExpired) as exc:
        st.error(str(exc))


def render_job_monitor(repo: Repository, config: dict, paths: AppPaths) -> None:
    repo.authorize()
    repo.recover_stale_jobs(seconds=180)
    jobs = repo.list_jobs(limit=100)
    if not jobs:
        st.info("No search jobs yet. Create a campaign to begin.")
        return
    rows = []
    for job in jobs:
        progress = job.get("progress") or {}
        rows.append({"Job": job["id"], "Status": job["state"].replace("_", " ").title(), "Saved": progress.get("saved", 0), "Inspected": progress.get("inspected", 0), "Queries": len(job.get("queries") or []), "Created": job.get("created_at", "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=min(320, 70 + 35 * len(rows)))
    by_id = {int(j["id"]): j for j in jobs}
    ids = list(by_id)
    preferred = st.session_state.get("_selected_job")
    selected = st.selectbox("Inspect job", ids, index=ids.index(preferred) if preferred in ids else 0, format_func=lambda value: f"Job #{value} · {by_id[value]['state'].replace('_', ' ')}", key="job_detail_selector")
    job = repo.get_job(selected)
    if not job:
        return
    state, progress = job["state"], job.get("progress") or {}
    settings = job.get("settings") or {}
    target = max(1, int(settings.get("target_leads", 25)))
    saved = int(progress.get("saved", 0))
    st.subheader(f"Job #{selected} — {state.replace('_', ' ').title()}")
    st.progress(min(1.0, saved / target), text=f"{saved} / {target} new qualified leads")
    summary = (job.get("checkpoint") or {}).get("summary")
    if summary:
        st.info(summary)
    elif job.get("reason"):
        st.info(str(job["reason"]).replace("_", " ").capitalize())
    if job.get("error"):
        st.error(job["error"])
    a, b, c, d, e = st.columns(5)
    for col, label, key in [(a, "Discovered", "discovered"), (b, "Inspected", "inspected"), (c, "Already known", "duplicate"), (d, "Refreshed", "refreshed"), (e, "Errors", "errors")]:
        col.metric(label, int(progress.get(key, 0)))
    st.caption(f"Excluded by website: {progress.get('excluded_website', 0)} · Excluded by phone: {progress.get('excluded_phone', 0)} · Unknown observations: {progress.get('unknown', 0)} · Queries complete: {progress.get('queries_completed', 0)} / {len(job.get('queries') or [])}")
    a, b, c = st.columns(3)
    if state in {"running", "queued"}:
        if a.button("Pause job", key=f"pause_job_{selected}"):
            try:
                repo.request_job_action(selected, "pause")
                notify("Pause requested. The worker will checkpoint at its next safe interruption point.")
                st.rerun()
            except PermissionDenied:
                raise
            except ValueError as exc:
                st.error(str(exc))
    if state in {"paused", "partial", "failed", "cancelled"}:
        if a.button("Resume job", key=f"resume_job_{selected}", type="primary"):
            try:
                repo.request_job_action(selected, "resume")
                if launch_pending(repo, paths):
                    notify("Resume requested. Saved progress and candidate outcomes will be reused.")
                    st.rerun()
            except PermissionDenied:
                raise
            except ValueError as exc:
                st.error(str(exc))
    if state in {"queued", "running", "paused", "pause_requested"}:
        if b.button("Cancel job", key=f"cancel_job_{selected}"):
            try:
                repo.request_job_action(selected, "cancel")
                notify("Cancellation requested. Previously saved leads are retained.")
                st.rerun()
            except PermissionDenied:
                raise
            except ValueError as exc:
                st.error(str(exc))
    if c.button("Start queued jobs", key=f"start_queued_{selected}"):
        if launch_pending(repo, paths):
            notify("Worker started. Queued jobs will run in order.")
            st.rerun()
    st.caption("Only one collection worker holds the database lease at a time. Paused and partial jobs wait for you to resume them.")
    with st.expander("Queries and limits"):
        st.dataframe(pd.DataFrame(job.get("queries") or []), hide_index=True, use_container_width=True)
        st.json(settings)
    logs = repo.get_job_logs(selected, limit=100)
    st.subheader("Run history")
    if logs:
        for entry in logs[-30:]:
            timestamp = entry.get("created_at") or entry.get("timestamp") or ""
            level = entry.get("level", "info").upper()
            st.text(f"{timestamp}  {level}  {entry.get('message', '')}")
    else:
        st.caption("Events will appear when the worker starts.")
    payload = json.dumps({"job": job, "logs": logs}, ensure_ascii=False, indent=2, default=str)
    private_download("Download job report", payload, file_name=f"job_{selected}.json", mime="application/json", authorize=repo.authorize, key=f"download_job_{selected}")


def render_jobs(repo: Repository, config: dict, paths: AppPaths) -> None:
    st.subheader("Search jobs")
    st.caption("Progress refreshes every three seconds. You can leave this page while a local worker is running.")
    job_monitor(repo, config, paths)


def clear_editor() -> None:
    st.session_state["_editor_revision"] = int(st.session_state.get("_editor_revision", 0)) + 1


def native_table_actions_available() -> bool:
    return callable(getattr(st.column_config, "ButtonColumn", None))


def capture_lead_view() -> None:
    saved = st.session_state.setdefault("_lead_view_state", {})
    for key in LEAD_VIEW_KEYS:
        if key in st.session_state:
            saved[key] = deepcopy(st.session_state[key])


def restore_lead_view() -> None:
    for key, value in st.session_state.get("_lead_view_state", {}).items():
        if key in LEAD_VIEW_KEYS and key not in st.session_state:
            st.session_state[key] = deepcopy(value)


def capture_active_table() -> None:
    active = st.session_state.get("_lead_table_widget")
    if not active or active["key"] not in st.session_state:
        return
    drafts, selected = capture_table_edits(
        active["rows"], st.session_state[active["key"]],
        st.session_state.get("_lead_table_drafts", {}),
        st.session_state.get("_lead_table_selected", []), active["timezone"],
        editable=active["editable"],
    )
    st.session_state["_lead_table_drafts"] = drafts
    st.session_state["_lead_table_selected"] = selected


def refresh_lead_table() -> None:
    capture_active_table()
    st.session_state.pop("_lead_table_widget", None)
    clear_editor()


def discard_table_drafts() -> None:
    st.session_state["_lead_table_drafts"] = {}
    st.session_state.pop("_lead_table_widget", None)
    clear_editor()
    notify("Unsaved table edits discarded. Saved leads were not changed.", "info")


def table_column_config(config: dict, all_leads: pd.DataFrame) -> dict:
    """Complete, compatible column definitions for the one-table Leads editor.

    This helper must live in this file: omitting it caused the v3.4 NameError.
    All column IDs correspond to source/database fields or explicitly generated
    monitoring fields; read-only columns stay disabled in the editor.
    """
    statuses = list(dict.fromkeys(
        [str(x) for x in config.get('crm_statuses', [])]
        + [str(x) for x in all_leads.get('crm_status', pd.Series(dtype=str)).dropna().tolist()]
    )) or ['New']
    currency = str(config.get('app', {}).get('currency', 'MAD'))
    return {
        'id': st.column_config.NumberColumn('ID', width='small', format='%d', help='Stable lead ID'),
        'name': st.column_config.TextColumn('Business', width='medium'),
        'phone': st.column_config.TextColumn('Phone', width='medium'),
        'phone_type': st.column_config.TextColumn('Phone type', width='small'),
        'phone_valid': st.column_config.CheckboxColumn('Phone valid'),
        'niche': st.column_config.TextColumn('Business category', width='medium'),
        'city': st.column_config.TextColumn('City', width='small'),
        'country': st.column_config.TextColumn('Country', width='small'),
        'address': st.column_config.TextColumn('Address', width='large'),
        'email': st.column_config.TextColumn('Email', width='medium'),
        'website_url': st.column_config.LinkColumn('Website', display_text='Open website'),
        'maps_link': st.column_config.LinkColumn('Map / Source', display_text='Open link'),
        'fb_link': st.column_config.LinkColumn('Facebook', display_text='View'),
        'instagram_link': st.column_config.LinkColumn('Instagram', display_text='View'),
        'website_type': st.column_config.TextColumn('Website type', width='small'),
        'rating': st.column_config.NumberColumn('Maps rating', min_value=0, max_value=5, format='%.1f'),
        'crm_status': st.column_config.SelectboxColumn('CRM stage', options=statuses, required=True),
        'updated_at': st.column_config.TextColumn('Last updated', width='medium'),
        'established_year': st.column_config.NumberColumn('Since (year)', min_value=1800, max_value=2100, format='%d',help='Known establishment year only; leave blank when not verified.'),
        'Input tokens': st.column_config.NumberColumn('Input tokens',width='small'),
        'Output tokens': st.column_config.NumberColumn('Output tokens',width='small'),
        'deal_value': st.column_config.NumberColumn(f'Deal value ({currency})', min_value=0, format='%.2f'),
        'notes': st.column_config.TextColumn('Notes', width='large'),
        'do_not_contact': st.column_config.CheckboxColumn('Outreach blocked', help='Read-only. Use Block outreach in the Action column to block contact.'),
        'archived': st.column_config.CheckboxColumn('Archived'),
        'Worker': st.column_config.SelectboxColumn('AI worker', options=['Unassigned']),
        'AI': st.column_config.TextColumn('AI status', width='small'),
        'Action': st.column_config.SelectboxColumn('Action', options=['—']),
        'Chats': st.column_config.NumberColumn('Messages', width='small'),
        'Latest message': st.column_config.TextColumn('Last message', width='medium'),
    }


def render_action_table(current: pd.DataFrame, all_leads: pd.DataFrame, config: dict,
                        access: WorkspaceAccess, repo: Repository) -> None:
    """Everything in one grid: CRM edits, worker assignment, AI actions and chat.

    Streamlit currently has no per-cell button widget inside st.data_editor.
    The Action dropdown is the closest reliable native one-table solution.
    """
    from leadfinder.ai_workers import WorkerDB
    from leadfinder.lead_grid_actions import ACTIONS, NO_ACTION, collect_operations, state_label

    db = WorkerDB(access.paths().database).initialize()
    workers = db.workers()
    worker_labels = {f"{w['name']} · {w['id'][:8]}": w['id'] for w in workers}
    worker_labels = {"Unassigned": "", **worker_labels}
    worker_by_id = {v: k for k, v in worker_labels.items()}
    worker_info = {w["id"]: w for w in workers}
    editable = TABLE_EDITABLE
    # Display key sales/worker information first; keep the rest in the same
    # horizontally scrollable table (no separate details page).
    cols = ["id", "name", "phone", "phone_type", "phone_valid", "niche", "city", "country",
            "crm_status", "established_year", "deal_value", "website_url", "maps_link",
            "email", "address", "website_type", "rating", "notes", "do_not_contact",
            "archived", "updated_at", "fb_link", "instagram_link"]
    cols = [col for col in cols if col in current.columns]
    signature = hashlib.sha256((str(current['id'].tolist()) + str(st.session_state.get('_editor_revision', 0))).encode()).hexdigest()[:12]
    key = f'lead_editor_{signature}'
    # The AI service updates CRM/hand-off state in the background. Don't show
    # a permanently cached "New / AI on" row after qualification completes.
    # If the user has unsaved edits, preserve them until they choose Refresh.
    ids_live = current['id'].astype(int).tolist()
    live_states = db.lead_ai_states(ids_live)
    live_summaries = db.lead_message_summaries(ids_live)
    freshness = hashlib.sha256(json.dumps([
        (int(row.id), str(row.crm_status), str(row.updated_at),
         str(live_states.get(int(row.id), {}).get('worker_id', '')),
         int(live_states.get(int(row.id), {}).get('handoff', 0)),
         int(live_states.get(int(row.id), {}).get('paused', 0)),
         int(live_summaries.get(int(row.id), {}).get('count', 0)))
        for row in current.itertuples(index=False)
    ], ensure_ascii=False, default=str).encode()).hexdigest()[:16]
    active = st.session_state.get('_lead_table_widget')
    unsaved = bool(st.session_state.get('_lead_table_drafts')) or bool(st.session_state.get('_lead_grid_failed_drafts'))
    if active and active.get('freshness') != freshness and not unsaved:
        st.session_state.pop('_lead_table_widget', None)
        clear_editor()
        key = f'lead_editor_{hashlib.sha256((str(current["id"].tolist()) + str(st.session_state.get("_editor_revision",0))).encode()).hexdigest()[:12]}'
        active = None
    if not active or active['key'] != key or key not in st.session_state:
        data = current[cols].copy().reset_index(drop=True)
        if 'maps_link' in data and 'source_url' in current:
            data['maps_link'] = data['maps_link'].fillna('').replace('',None)
            data['maps_link'] = data['maps_link'].combine_first(current['source_url'].reset_index(drop=True))

        saved_by_id = {int(row['id']): row for row in current[cols].to_dict('records')}
        ids = data['id'].astype(int).tolist()
        meta = db.lead_ai_states(ids)
        tokens = db.lead_token_totals(ids)
        summaries = db.lead_message_summaries(ids)
        qualification = db.qualification_briefs(ids)
        # Business and phone remain at the left edge; every control is
        # immediately alongside them, before the longer CRM columns.
        data.insert(3, 'Worker', [worker_by_id.get(meta.get(lid, {}).get('worker_id'), 'Unassigned') for lid in ids])
        data.insert(4, 'AI', [state_label(meta.get(lid), worker_info.get(meta.get(lid, {}).get('worker_id'), {}).get('enabled', 0)) for lid in ids])
        data.insert(5, 'Action', NO_ACTION)
        data.insert(6, 'Chats', [summaries.get(lid, {}).get('count', 0) for lid in ids])
        data.insert(7, 'Latest message', [summaries.get(lid, {}).get('preview', '') for lid in ids])
        data.insert(8, 'Developer brief', [qualification.get(lid, {}).get('summary', '') for lid in ids])
        data.insert(9, 'Input tokens', [tokens.get(lid, {}).get('input', 0) for lid in ids])
        data.insert(10, 'Output tokens', [tokens.get(lid, {}).get('output', 0) for lid in ids])
        for i, row in data.iterrows():
            for field, value in st.session_state.get('_lead_table_drafts', {}).get(int(row['id']), {}).get('changes', {}).items():
                if field in editable:
                    data.at[i, field] = value
        active = {'key': key, 'rows': data.to_dict('records'), 'editable': editable,
                  'timezone': config['app']['timezone'], 'worker_labels': worker_labels,
                  'freshness': freshness}
        for row in active['rows']:
            lead_id = int(row['id'])
            baseline = {field: saved_by_id[lead_id].get(field) for field in editable}
            baseline.update(st.session_state.get('_lead_table_drafts', {}).get(lead_id, {}).get('baseline', {}))
            row['_baseline'] = baseline
        st.session_state['_lead_table_widget'] = active

    data = pd.DataFrame(active['rows']).drop(columns=['_baseline'], errors='ignore')
    column_config = table_column_config(config, all_leads)
    column_config.update({
        'id': st.column_config.NumberColumn('ID', width='small'),
        'Worker': st.column_config.SelectboxColumn('Worker', options=list(worker_labels), width='medium', required=True),
        'AI': st.column_config.TextColumn('AI status', width='small'),
        'Action': st.column_config.SelectboxColumn('Action', options=list(ACTIONS), width='medium', required=True),
        'Chats': st.column_config.NumberColumn('Messages', width='small'),
        'Latest message': st.column_config.TextColumn('Latest message', width='medium'),
        'Developer brief': st.column_config.TextColumn('Developer brief', width='large'),
    })
    st.data_editor(data, key=key, hide_index=True, width='stretch', height=590,
                   disabled=[c for c in data.columns if c not in ['Worker', 'Action', *editable]],
                   column_config=column_config, on_change=capture_active_table)

    # Save CRM updates directly from the table, preserving existing optimistic
    # conflict checks; no separate edit/details/AI panel is needed.
    drafts = st.session_state.get('_lead_table_drafts', {})
    actions = []
    try:
        actions = collect_operations(st.session_state.get(key, {}), active['rows'], worker_labels)
    except ValueError as exc:
        notify(str(exc), 'error')
        st.session_state.pop('_lead_table_widget', None)
        clear_editor()
        st.rerun()
    if not drafts and not actions:
        return
    messages = []
    for lead_id, draft in list(drafts.items()):
        try:
            latest = repo.get_lead(lead_id)
            if latest is None:
                raise ValueError('Lead no longer exists')
            if baseline_conflicts(draft['baseline'], draft['changes'], latest, config['app']['timezone']):
                raise ValueError('The lead was edited elsewhere; reload before saving')
            repo.update_lead(lead_id, draft['changes'], expected_snapshot=latest)
            drafts.pop(lead_id, None)
        except (ValueError, OSError, sqlite3.Error) as exc:
            messages.append(f'Lead #{lead_id}: {exc}')
    if messages:
        # Keep the failed values for explicit recovery without entering an
        # infinite retry loop on each subsequent rerun.
        st.session_state['_lead_grid_failed_drafts'] = deepcopy(drafts)
        drafts = {}
    st.session_state['_lead_table_drafts'] = drafts
    for lead_id, changed_worker, worker_id, command in actions:
        try:
            if changed_worker:
                if worker_id:
                    db.assign(worker_id, [lead_id])
                    from leadfinder.ai_runtime import Registry, app_paths
                    Registry(app_paths().data).register(worker_id, access.require())
                else:
                    db.unassign([lead_id])
            if command:
                execute_grid_action(command, lead_id, db, repo, access)
        except Exception as exc:
            messages.append(f'Lead #{lead_id}, {command or "worker assignment"}: {str(exc)[:180]}')
    # Reset the editor action dropdown immediately to avoid repeated sends or
    # actions on the next Streamlit rerun.
    st.session_state.pop('_lead_table_widget', None)
    clear_editor()
    if messages:
        st.session_state['_lead_grid_errors'] = messages
    st.rerun()


def execute_grid_action(command, lead_id, db, repo, access):
    """Handle a deliberate, one-lead action with business checks intact."""
    from leadfinder.ai_runtime import app_paths, node_api
    from leadfinder.ai_agent import prepare_draft
    if command == 'Delete permanently':
        st.session_state['_confirm_delete_lead']=int(lead_id)
        return
    if command == 'Chat':
        st.session_state['_lead_chat_dialog'] = int(lead_id)
        return
    if command == 'Block outreach':
        repo.update_lead(lead_id, {'do_not_contact': True})
        return
    if command == 'Archive':
        repo.update_lead(lead_id, {'archived': True})
        return
    if command == 'Restore':
        repo.update_lead(lead_id, {'archived': False})
        return
    if command == 'Pause AI':
        db.set_lead_paused(lead_id, True)
        return
    if command == 'Resume AI':
        state = db.lead_ai_state(lead_id)
        if state and state.get('handoff'):
            raise ValueError('Human takeover is active: choose Resume after handoff.')
        db.set_lead_paused(lead_id, False)
        return
    if command == 'Human takeover':
        db.set_handoff(lead_id, True)
        return
    if command == 'Complete & hand off':
        # Existing CRM correction; don't send a duplicate developer promise.
        db.complete_existing_conversation(lead_id)
        return
    if command == 'Resume after handoff':
        db.set_handoff(lead_id, False)
        db.set_lead_paused(lead_id, False)
        return
    if command != 'Start discussion':
        raise ValueError('Unknown lead action')
    state = db.lead_ai_state(lead_id)
    if not state:
        raise ValueError('Assign a worker first using the Worker column.')
    if state.get('paused') or state.get('handoff'):
        raise ValueError('Resume this lead’s AI before starting.')
    worker = db.worker(state['worker_id']) or {}
    if not worker.get('enabled') or not worker.get('model_name'):
        raise ValueError('Worker must be enabled with a tested Gemini model.')
    if not db.can_message(lead_id, require_optin=True):
        raise ValueError('Automated first contact is unavailable for this lead.')
    if db.messages(lead_id, limit=1):
        raise ValueError('Conversation already exists; use Chat to review it.')
    try:
        status = node_api('/status', {'workerId': state['worker_id']}, timeout=3)
    except Exception as exc:
        raise ValueError('WhatsApp worker is offline.') from exc
    if status.get('state') != 'ready':
        raise ValueError('WhatsApp worker must be ready before starting.')
    text = prepare_draft(db, state['worker_id'], lead_id, app_paths().data)
    if not text.strip():
        raise ValueError('Gemini did not generate a usable introduction.')
    db.manual_outbound(state['worker_id'], lead_id, text)


@st.dialog('WhatsApp conversation', width='large')
def render_lead_chat_dialog(lead_id, access):
    """On-demand popup, not a second page/section below the lead table."""
    from leadfinder.ai_workers import WorkerDB
    db = WorkerDB(access.paths().database).initialize()
    lead = db.lead_label(lead_id) or {}
    st.markdown(f"**{lead.get('name') or 'Business'}** · Lead #{lead_id}")
    st.caption(f"{lead.get('phone') or 'No phone'} · {lead.get('city') or 'City not recorded'}")
    state = db.lead_ai_state(lead_id)
    brief = db.qualification_briefs([lead_id]).get(lead_id, {})
    if state and state.get('handoff'):
        st.success('Developer follow-up needed. AI replies stopped for this customer; check the current CRM stage in the Leads table.')
    if brief.get('summary'):
        st.markdown('**Developer brief:** ' + brief['summary'])
    history = db.messages(lead_id, limit=100)
    if not history:
        st.caption('No conversation yet.')
    else:
        with st.container(height=410):
            for msg in history:
                with st.chat_message('assistant' if msg['direction'] == 'out' else 'user'):
                    st.write(msg['body'])
                    if msg['direction'] == 'out':
                        st.caption(str(msg['status']))
    state = db.lead_ai_state(lead_id)
    c1, c2 = st.columns(2)
    if c1.button('Refresh chat', key=f'chat_reload_{lead_id}', use_container_width=True):
        st.rerun()
    if c2.button('Close', key=f'chat_close_{lead_id}', use_container_width=True):
        st.session_state.pop('_lead_chat_dialog', None)
        st.rerun()
    if state and state.get('handoff'):
        st.caption('AI is paused for this lead. To reopen it, use Resume after handoff in the Action column.')


@st.dialog('Delete this lead permanently?')
def confirm_delete_dialog(lead_id, repo, access):
    lead=repo.get_lead(lead_id)
    if not lead:
        st.warning('Lead was already deleted.')
        if st.button('Close'):st.session_state.pop('_confirm_delete_lead',None);st.rerun()
        return
    st.error('This cannot be undone. CRM data and associated AI messages will be removed.')
    st.write(f"Lead #{lead_id}: **{lead.get('name','')}**")
    confirm=st.text_input('Type DELETE to confirm',key=f'delete_confirm_{lead_id}')
    c1,c2=st.columns(2)
    if c1.button('Permanently delete',disabled=confirm!='DELETE',type='primary'):
        from leadfinder.ai_workers import WorkerDB
        db=WorkerDB(access.paths().database).initialize()
        with db.connect() as conn:
            ids=[r[0] for r in conn.execute('SELECT id FROM ai_messages WHERE lead_id=?',(lead_id,)).fetchall()]
        try:
            repo.delete_lead(lead_id,expected_snapshot=lead)
            with db.connect(True) as conn:
                if ids:
                    for start in range(0,len(ids),300):
                        group=ids[start:start+300]
                        conn.execute('DELETE FROM ai_messages WHERE id IN ('+','.join('?' for _ in group)+')',group)
                conn.execute('DELETE FROM ai_usage WHERE lead_id=?',(lead_id,))
            st.session_state.pop('_confirm_delete_lead',None)
            notify(f'Lead #{lead_id} permanently deleted')
            st.rerun()
        except (ValueError,sqlite3.Error) as exc:st.error(str(exc))
    if c2.button('Cancel'):
        st.session_state.pop('_confirm_delete_lead',None);st.rerun()


def render_leads(repo: Repository, config: dict, paths: AppPaths, access: WorkspaceAccess) -> None:
    restore_lead_view()
    st.header("Leads")
    capture_active_table()
    all_leads = frame_for(repo)
    if all_leads.empty:
        st.info("Your leads will appear here after a search. Create a campaign to begin.")
        st.button("Create campaign", on_click=go_to, args=("Discovery",))
        return
    search = st.text_input('Search businesses, phones, locations or notes',key='lead_filter_search')
    with st.expander('Filters · Sort / Pagination',expanded=False):
        a,b,c=st.columns(3)
        choices=lambda field,key: sorted(set(v for v in all_leads[field].dropna().unique().tolist() if v!='') | set(st.session_state.get(key,[])))
        countries=a.multiselect('Countries',choices('country','lead_filter_countries'),key='lead_filter_countries')
        cities=b.multiselect('Cities',choices('city','lead_filter_cities'),key='lead_filter_cities')
        niches=c.multiselect('Categories',choices('niche','lead_filter_niches'),key='lead_filter_niches')
        statuses=a.multiselect('CRM stages',choices('crm_status','lead_filter_statuses'),key='lead_filter_statuses')
        websites=b.multiselect('Website evidence',list(WEBSITE_LABELS),format_func=lambda v:WEBSITE_LABELS[v],key='lead_filter_websites')
        phones=c.multiselect('Phone types',choices('phone_type','lead_filter_phones'),key='lead_filter_phones')
        show_archived=a.checkbox('Include archived',key='lead_filter_archived')
        show_dnc=b.checkbox('Include blocked contacts',value=True,key='lead_filter_dnc')
        st.divider()
        sort_col,size_col,page_col=st.columns([2.2,1.0,0.9],gap='small')
        with sort_col:
            if st.session_state.get('lead_sort') not in ('Recently updated','Name','CRM stage'):
                st.session_state['lead_sort']='Recently updated'
            sort_label=st.selectbox('Sort by',['Recently updated','Name','CRM stage'],key='lead_sort')
        page_sizes=sorted({25,50,100,int(config['app'].get('page_size',25))})
        if st.session_state.get('lead_page_size') not in page_sizes:
            st.session_state['lead_page_size']=25
        with size_col:
            page_size=int(st.selectbox('Rows per page',page_sizes,key='lead_page_size',on_change=capture_active_table))
        filtered=filter_leads(all_leads,search=search,countries=countries,cities=cities,
                              niches=niches,statuses=statuses,website_types=websites,
                              phone_types=phones,show_archived=show_archived,
                              show_do_not_contact=show_dnc)
        page_count=max(1,(len(filtered)+page_size-1)//page_size)
        if int(st.session_state.get('leads_page_number',1))>page_count:
            st.session_state['leads_page_number']=page_count
        with page_col:
            page=int(st.number_input('Page',min_value=1,max_value=page_count,step=1,
                        key='leads_page_number',on_change=capture_active_table))
        st.button('Reset filters',on_click=reset_filters)
    if sort_label=='Recently updated':
        filtered=filtered.sort_values(['updated_at','id'],ascending=[False,False],na_position='last')
    elif sort_label=='Name':
        filtered=filtered.sort_values('name',key=lambda v:v.astype(str).str.casefold())
    else:
        filtered=filtered.sort_values(['crm_status','updated_at'],ascending=[True,False])
    st.caption(f"{len(filtered):,} matching leads · {len(all_leads):,} in the database · Page {page}/{page_count}")
    if filtered.empty:
        st.info("No leads match these filters. Try resetting filters or including archived leads.")
        return
    current = filtered.iloc[(page - 1) * page_size:page * page_size].copy()
    st.caption('Edit details, choose a worker, or use Action in the same row. Chat opens a popup. Status, website, maps and notes are included.')
    c1, c2 = st.columns([6, 1])
    c1.markdown('**Business directory**')
    c2.button('Refresh', key='lead_table_refresh', on_click=refresh_lead_table, use_container_width=True)
    render_action_table(current, all_leads, config, access, repo)
    for error in st.session_state.pop('_lead_grid_errors', []):
        st.error(error)
    failed = st.session_state.get('_lead_grid_failed_drafts', {})
    if failed:
        with st.expander('Edits not saved — review and try again'):
            for lid, draft in failed.items():
                st.write(f'Lead #{lid}')
                st.json(draft['changes'])
            if st.button('Dismiss unsaved edits', key='lead_grid_dismiss'):
                st.session_state.pop('_lead_grid_failed_drafts', None)
                st.rerun()
    pending_delete = st.session_state.get('_confirm_delete_lead')
    if pending_delete is not None:
        confirm_delete_dialog(int(pending_delete), repo, access)
    requested_chat = st.session_state.get('_lead_chat_dialog')
    if requested_chat is not None:
        render_lead_chat_dialog(int(requested_chat), access)


def render_analytics(repo: Repository, config: dict, paths: AppPaths, embedded: bool = False) -> None:
    st.subheader("Pipeline and analytics" if embedded else "Analytics")
    all_leads = frame_for(repo)
    if all_leads.empty:
        st.info("Analytics will appear once you have leads.")
        return
    include_archived = st.checkbox("Include archived leads in the overview", value=False)
    leads = all_leads if include_archived else all_leads[~all_leads["archived"]]
    today = datetime.now(ZoneInfo(config["app"]["timezone"])).date()
    won = leads[leads["crm_status"] == "Won"]
    pipeline = leads[~leads["crm_status"].isin(["Won", "Lost"]) & ~leads["do_not_contact"]]
    a, b, c, d = st.columns(4)
    a.metric("Leads", len(leads))
    b.metric("Interested", int(leads["crm_status"].eq("Interested").sum()))
    c.metric(f"Open pipeline ({config['app']['currency']})", f"{pipeline['deal_value'].sum():,.2f}")
    d.metric(f"Won value ({config['app']['currency']})", f"{won['deal_value'].sum():,.2f}")
    a, b = st.columns(2)
    with a:
        st.subheader("Current CRM stages")
        stages = leads["crm_status"].value_counts().reindex(config["crm_statuses"], fill_value=0).rename("Leads")
        st.bar_chart(stages, color="#147D64")
    with b:
        st.subheader("Website evidence")
        websites = leads["website_type"].map(lambda value: WEBSITE_LABELS.get(value, value)).value_counts().rename("Leads")
        st.bar_chart(websites, color="#3B82A0")
    st.caption("These charts show current states. Historical stage conversion rates require dated transition cohorts and are not inferred from these counts.")
    st.subheader("Campaign performance")
    metrics = repo.campaign_metrics()
    if metrics:
        result = pd.DataFrame(metrics)
        st.dataframe(result, hide_index=True, use_container_width=True)
        private_download("Export campaign metrics", safe_csv(result), file_name="campaign_metrics.csv", mime="text/csv", authorize=repo.authorize)
        st.caption("These metrics cover newly saved and explicitly refreshed leads. Existing businesses skipped without a refresh are counted in the job's Already known total. A refreshed lead can appear in more than one campaign, so campaign counts can overlap.")
    else:
        st.caption("Imported leads are retained without inventing a campaign. New searches record their campaign and query sources.")
    st.subheader("Data quality")
    a, b, c, d = st.columns(4)
    a.metric("Valid phone", int(leads["phone_valid"].sum()))
    b.metric("Unverified website", int(leads["website_status"].isin(["unknown", "error"]).sum()))
    c.metric("Do not contact", int(leads["do_not_contact"].sum()))
    d.metric("With a website", int(leads["website_url"].astype(str).str.len().gt(0).sum()))
    a, b = st.columns(2)
    for container, field, title in [(a, "city", "Locations"), (b, "niche", "Categories")]:
        with container:
            st.subheader(title)
            grouped = leads.groupby(field, dropna=False).agg(leads=("id", "size")).sort_values("leads", ascending=False)
            st.dataframe(grouped, use_container_width=True)


def render_settings(repo: Repository, config: dict, paths: AppPaths) -> None:
    access = current_workspace()
    st.header("Settings")
    st.caption('Local settings for this PC.')
    with st.form("settings_form"):
        new_config = deepcopy(config)
        st.subheader("Workspace")
        a, b, c = st.columns(3)
        new_config["app"]["currency"] = a.text_input("Deal-value currency", value=config["app"]["currency"], max_chars=3).upper()
        new_config["app"]["timezone"] = b.text_input("Time zone", value=config["app"]["timezone"], help="Use an IANA time zone, for example Africa/Casablanca or Europe/Paris.")
        new_config["app"]["page_size"] = int(c.number_input("Rows per page", 25, 500, int(config["app"]["page_size"]), 25))
        with st.expander("Default search settings"):
            new_config["search_defaults"] = search_settings_fields(config["search_defaults"], "default_search")
        with st.expander("Classification and CRM options"):
            hosted_domains_text = st.text_area("Hosted website domains — one per line", value="\n".join(config["free_site_indicators"]))
            social_domains_text = st.text_area("Social and directory link domains — one per line", value="\n".join(config["fake_domains"]))
            statuses_text = st.text_area("CRM statuses — one per line", value="\n".join(config["crm_statuses"]))
        save = st.form_submit_button("Save settings", type="primary")
    if save:
        settings_written = False
        try:
            new_config["free_site_indicators"] = unique_lines(hosted_domains_text, 200)
            new_config["fake_domains"] = unique_lines(social_domains_text, 200)
            new_config["crm_statuses"] = unique_lines(statuses_text, 50)
            access.save_config(new_config)
            settings_written = True
            clear_editor()
            notify("Settings saved.")
            st.rerun()
        except PermissionDenied:
            raise
        except (ConfigError, ValueError, OSError, sqlite3.Error) as exc:
            if settings_written:
                st.error(f"Settings were saved, but a later step failed: {exc}.")
            else:
                st.error(str(exc))
    st.subheader("Database backup")
    st.caption("Create a consistent SQLite snapshot before a major cleanup. Automatic migration backups are kept alongside the database.")
    if st.button("Create backup"):
        try:
            st.session_state["_backup_name"] = access.create_backup()
            st.success("Backup created.")
        except PermissionDenied:
            raise
        except (ValueError, OSError, sqlite3.Error) as exc:
            st.error(str(exc))
    backup_name = st.session_state.get("_backup_name")
    if backup_name:
        try:
            private_download("Download latest backup", access.read_backup(backup_name), file_name=backup_name, mime="application/vnd.sqlite3", authorize=access.require)
        except FileNotFoundError:
            st.session_state.pop("_backup_name", None)
            st.info("That backup is no longer available. Create a new backup to download it.")
    st.caption("This snapshot contains the local lead database. Store the backup securely.")
    with st.expander("Installation information"):
        user = access.require()
        st.caption(f'App version: {__version__} · Local installation')
        st.write("Your campaigns, leads and settings are stored locally on this computer.")
        private_download("Download configuration", json.dumps(config, ensure_ascii=False, indent=2), file_name="config.json", mime="application/json", authorize=access.require)



def render_ai_operations(access: WorkspaceAccess) -> None:
    """Monitor local workers, WhatsApp verification, conversations and token usage."""
    from leadfinder.ai_workers import WorkerDB
    from leadfinder.ai_runtime import node_api
    db=WorkerDB(access.paths().database).initialize()
    workers=db.workers()
    if not workers:
        st.info('No AI workers configured yet. Create one under AI Workers.')
        return
    with db.connect() as con:
        stats=con.execute("""SELECT
            (SELECT count(*) FROM ai_messages WHERE direction='in') incoming,
            (SELECT count(*) FROM ai_messages WHERE direction='out' AND status='sent') sent,
            (SELECT count(*) FROM ai_messages WHERE status IN ('queued','queued_reply','sending')) pending,
            (SELECT count(*) FROM ai_lead_control WHERE paused=1) paused,
            (SELECT count(*) FROM ai_lead_control WHERE handoff=1) handoffs,
            (SELECT coalesce(sum(input_tokens),0) FROM ai_usage) input_tokens,
            (SELECT coalesce(sum(output_tokens),0) FROM ai_usage) output_tokens,
            (SELECT count(*) FROM ai_whatsapp_checks WHERE status='registered') registered,
            (SELECT count(*) FROM ai_whatsapp_checks WHERE status='not_registered') not_registered
            """).fetchone()
        recent=con.execute("""SELECT l.name,m.direction,m.status,m.body,m.created_at
            FROM ai_messages m LEFT JOIN leads l ON l.id=m.lead_id
            ORDER BY m.id DESC LIMIT 12""").fetchall()
    st.subheader('AI workforce monitor')
    c1,c2,c3,c4,c5=st.columns(5)
    c1.metric('Workers',len(workers))
    c2.metric('Received messages',stats['incoming'])
    c3.metric('Sent messages',stats['sent'])
    c4.metric('Queued',stats['pending'])
    c5.metric('Need human',stats['handoffs'])
    c1,c2,c3,c4=st.columns(4)
    c1.metric('AI paused chats',stats['paused'])
    c2.metric('Verified WhatsApp',stats['registered'])
    c3.metric('Not registered',stats['not_registered'])
    c4.metric('Gemini tokens',f"{stats['input_tokens']+stats['output_tokens']:,}")
    rows=[]
    for w in workers:
        try: live=node_api('/status',{'workerId':w['id']},timeout=2)
        except Exception: live={}
        rows.append({'Worker':w['name'],'WhatsApp':live.get('state','offline'),
            'Gemini':w.get('model_name') or 'Not configured',
            'Leads':w['lead_count'],'AI replies':'On' if w['enabled'] else 'Off',
            'New contacts today':f"{db.daily_new_contact_used(w['id'])}/{w['daily_new_contacts']}",
            'Tokens today / limit':f"{db.tokens_today(w['id'])['total']:,}/{int(w.get('daily_token_limit') or 10000):,}"})
    st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
    with db.connect() as con:
        daily=con.execute('''SELECT date(recorded_at,'unixepoch') AS day,
            SUM(input_tokens) AS input, SUM(output_tokens) AS output, COUNT(*) AS calls,
            SUM(CASE WHEN error!='' THEN 1 ELSE 0 END) AS failures
            FROM ai_usage WHERE recorded_at>=strftime('%s','now','-30 days')
            GROUP BY day ORDER BY day''').fetchall()
        model_usage=con.execute('''SELECT model,SUM(input_tokens) AS input,SUM(output_tokens) AS output,
            COUNT(*) AS calls FROM ai_usage GROUP BY model ORDER BY input+output DESC LIMIT 10''').fetchall()
        activity=con.execute('''SELECT date(created_at,'unixepoch') AS day,
            SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END) AS inbound,
            SUM(CASE WHEN direction='out' AND status='sent' THEN 1 ELSE 0 END) AS outbound
            FROM ai_messages WHERE created_at>=strftime('%s','now','-30 days')
            GROUP BY day ORDER BY day''').fetchall()
    st.subheader('Gemini token usage · last 30 days (UTC)')
    if daily:
        trend=pd.DataFrame([dict(x) for x in daily]).set_index('day')
        st.line_chart(trend[['input','output']])
        c1,c2,c3=st.columns(3)
        c1.metric('Total input tokens',f"{stats['input_tokens']:,}")
        c2.metric('Total output tokens',f"{stats['output_tokens']:,}")
        c3.metric('Generation errors (30 days)',int(trend['failures'].sum()))
        st.caption('Requests, tokens and errors are measured from API metadata; failures without a Gemini response may not report token usage.')
        st.dataframe(trend[['calls','failures','input','output']].tail(7),width='stretch')
    else:st.info('No Gemini token history yet.')
    c1,c2=st.columns(2)
    with c1:
        st.subheader('Usage by model')
        if model_usage:
            models=pd.DataFrame([dict(r) for r in model_usage]).set_index('model')
            st.bar_chart(models[['input','output']])
        else:st.caption('No model activity yet.')
    with c2:
        st.subheader('Conversation volume · 30 days')
        if activity:
            volume=pd.DataFrame([dict(r) for r in activity]).set_index('day')
            st.line_chart(volume[['inbound','outbound']])
        else:st.caption('No conversation activity yet.')

    with st.expander('Recent conversation activity'):
        if recent:
            st.dataframe(pd.DataFrame([{'Lead':x['name'] or 'Unmatched',
                'Direction':x['direction'],'Status':x['status'],
                'Message preview':str(x['body'])[:85],
                'Time':datetime.fromtimestamp(x['created_at'],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
                for x in recent]),hide_index=True,use_container_width=True)
        else: st.caption('No conversations yet.')

def main() -> None:
    st.set_page_config(page_title='MedMH AI Workforce', page_icon='💬',layout='wide',initial_sidebar_state='expanded',menu_items={'Get help':None,'Report a bug':None,'About':None})
    st.markdown('''<style>
        #MainMenu, footer, [data-testid="stDeployButton"], [data-testid="stToolbar"],
        [data-testid="stDecoration"], [data-testid="stStatusWidget"] {display:none!important;}
        .block-container {padding-top:1.3rem;padding-bottom:1.2rem;}
        </style>''',unsafe_allow_html=True)
    pages=PAGES
    with st.sidebar:
        st.title('MedMH AI Workforce')
        st.caption('Local desktop dashboard · no login')
        if st.session_state.get('navigation') not in pages:
            st.session_state['navigation']=PAGES[0]
        page=st.radio('Navigation',pages,key='navigation',label_visibility='collapsed')
        st.caption('Local-only · 127.0.0.1')
    notice=st.session_state.pop('_notice',None)
    if notice:
        level,message=notice
        getattr(st,level if level in {'success','info','warning','error'} else 'info')(message)
    try:
        access=current_workspace()
        paths=access.paths()
        config=access.load_config()
        repo=access.repository().initialize()
        capture_active_table()
        capture_lead_view()
        with st.sidebar:
            running=[job for job in repo.list_jobs(limit=100) if job['state'] in ACTIVE_STATES]
            if running: st.caption(f'{len(running)} active search jobs')
        if page=='AI Workers':
            from leadfinder.ai_ui import render_workers
            render_workers(access,repo)
        elif page=='Message Queue':
            from leadfinder.queue_ui import render_queue
            render_queue(access)
        elif page=='Leads':
            render_leads(repo,config,paths,access)
        elif page=='Discovery':
            st.header('Discovery')
            build, runs = st.tabs(['Campaigns and search settings', 'Running jobs and logs'])
            with build: render_campaigns(repo,config,paths)
            with runs: render_jobs(repo,config,paths)
        elif page=='Overview':
            render_overview(repo,config,paths)
            st.divider()
            render_analytics(repo,config,paths,embedded=True)
            render_ai_operations(access)
        else:
            render_settings(repo,config,paths)
    except (ConfigError,OSError,sqlite3.Error,WorkspaceError) as exc:
        st.error(f'Could not open the local workspace: {exc}')

if __name__=='__main__':
    main()
