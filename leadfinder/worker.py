"""Run saved collection jobs outside Streamlit, with leases and checkpoints."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import sys
import threading
import time
import uuid

from .scraper import AccessBlockedError, GoogleMapsScraper, ScraperError, TransientScrapeError
from .local_workspace import WorkspaceError


DEFAULT_JOB_SETTINGS = {
    "target_leads": 25, "max_candidates": 500, "max_minutes": 20,
    "only_without_website": True, "include_hosted_sites": True,
    "require_phone": True, "refresh_existing": False,
    "collect_review_sample": False, "headless": True,
    "delay_seconds": 2, "cache_hours": 168, "timeout_seconds": 30,
}
PROGRESS_KEYS = (
    "inspected", "saved", "duplicate", "refreshed", "excluded_website",
    "excluded_phone", "unknown", "errors", "queries_completed", "discovered",
)
HEARTBEAT_SECONDS = 15.0
MAX_ATTEMPTS_PER_RUN = 3
BACKOFF_BASE_SECONDS = 1.0


class _StopJob(BaseException):
    def __init__(self, state, reason, message=""):
        self.state, self.reason, self.message = state, reason, message
        super().__init__(message or reason)


class _LeaseLost(BaseException):
    pass


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def effective_settings(config, job):
    result = dict(DEFAULT_JOB_SETTINGS)
    for section in (config, config.get("scraper", {}), config.get("job_defaults", {}),
                    config.get("search_defaults", {}), job.get("settings", {})):
        if isinstance(section, dict):
            result.update({key: value for key, value in section.items() if key in result})
    for key in ("target_leads", "max_candidates"):
        result[key] = max(1, int(result[key]))
    result["max_minutes"] = max(0.001, float(result["max_minutes"]))
    result["delay_seconds"] = max(0.0, float(result["delay_seconds"]))
    result["timeout_seconds"] = max(2.0, float(result["timeout_seconds"]))
    result["cache_hours"] = max(0.0, float(result["cache_hours"]))
    return result


def eligibility(lead, settings):
    """Current settings are applied to factual observations, including cache."""
    status = lead.get("website_status", "unknown")
    website_type = lead.get("website_type", "Unknown")
    if settings["only_without_website"]:
        if status not in {"found", "confirmed_absent"}:
            return "unknown", "Website presence could not be verified."
        if website_type in {"Real", "Subdomain"}:
            return "excluded_website", "The business has a standalone website."
        if website_type == "Free" and not settings["include_hosted_sites"]:
            return "excluded_website", "Hosted website platforms are excluded by the current settings."
        if website_type not in {"None", "Free", "Social"}:
            return "unknown", "Website classification is unresolved."
    if settings["require_phone"] and not lead.get("phone_valid"):
        return "excluded_phone", "A valid telephone number was not found."
    return None, None


def _lead_keys(lead):
    keys = {lead.get("source_key")}
    url = lead.get("maps_link") or lead.get("source_url") or ""
    if url or lead.get("place_id") or (lead.get("name") and lead.get("address")):
        from .quality import canonical_source_key
        keys.add(canonical_source_key(
            url=url, place_id=lead.get("place_id", ""), name=lead.get("name", ""),
            address=lead.get("address", ""), country_code=lead.get("country_code", ""),
            phone_e164=lead.get("phone_e164", ""),
        ))
    return {key for key in keys if key}


def run_job(repo, job, config, scraper_factory=None, *, stop_event=None, authorization_check=None):
    """Run an already atomically claimed job and persist every terminal outcome.

    The saved-lead target is cumulative across resumes. Time and candidate
    budgets apply to this invocation; Resume explicitly authorizes a new
    bounded run. ``scraper_factory`` permits offline tests with a fake collector.
    Web-launched workers supply an account/version ``authorization_check``;
    logout does not stop them, while disable/reset pauses them at the next
    collection pulse or heartbeat.  Final checkpoint writes only release the
    already-owned job and preserve its progress after authorization changes.
    """
    job_id = job["id"]
    token = job.get("worker_token")
    if not token or job.get("state") not in {"running", "pause_requested", "cancel_requested"}:
        raise ValueError("run_job requires a job claimed by Repository.claim_next_job().")
    config = config or {}
    settings = effective_settings(config, job)
    queries = list(job.get("queries") or [])
    progress = {key: int((job.get("progress") or {}).get(key, 0) or 0) for key in PROGRESS_KEYS}
    # A process can stop after a lead transaction commits but before its next
    # checkpoint. Reconcile the cumulative target from committed discoveries.
    committed = repo.job_discovery_counts(job_id)
    for key in ("saved", "refreshed"):
        progress[key] = max(progress[key], int(committed.get(key, 0)))
    checkpoint = dict(job.get("checkpoint") or {})
    completed_queries = set(checkpoint.get("completed_query_indices") or [])
    started = time.monotonic()
    prior_elapsed = float(checkpoint.get("elapsed_seconds", 0) or 0)
    heartbeat_stop = threading.Event()
    heartbeat_failure = []
    authorization_failure = []
    stop_event = stop_event or threading.Event()
    last_poll = [0.0]
    run_attempted = set()
    known_candidates = {row["source_key"]: row for row in repo.list_candidates(job_id)}
    progress["discovered"] = len(known_candidates)
    progress["inspected"] = max(progress["inspected"], sum(int(row.get("attempts") or 0) > 0 for row in known_candidates.values()))
    for counter in ("duplicate", "excluded_website", "excluded_phone", "unknown"):
        progress[counter] = max(progress[counter], sum(row.get("outcome") == counter for row in known_candidates.values()))
    existing_by_key = {}
    for lead in repo.list_leads(include_archived=True):
        for key in _lead_keys(lead):
            existing_by_key[key] = lead
    query_failures = {}
    stalled_queries = set()
    state, reason, error = "partial", "results_exhausted", ""
    collector_factory = scraper_factory or GoogleMapsScraper

    def check_authorization():
        if authorization_failure:
            raise _StopJob("paused", "account_access_revoked", authorization_failure[0])
        if authorization_check is not None:
            try:
                authorization_check()
            except WorkspaceError:
                # An unsafe filesystem path cannot be used even to finalize a
                # checkpoint.  Leave the lease for ordinary stale recovery
                # after the operator repairs the path; never follow a redirect.
                stop_event.set()
                raise _LeaseLost("The workspace path can no longer be used safely.")
            except Exception:
                message = "Worker could not access the local database; check that the file is available, then resume the job."
                authorization_failure.append(message)
                stop_event.set()
                raise _StopJob("paused", "account_access_revoked", message)

    def pulse(force=False):
        check_authorization()
        if heartbeat_failure:
            raise _LeaseLost(str(heartbeat_failure[0]))
        if stop_event.is_set():
            raise _StopJob("paused", "worker_interrupted", "The worker stopped. Resume continues from saved progress.")
        now = time.monotonic()
        if force or now - last_poll[0] >= 0.2:
            live = repo.get_job(job_id)
            last_poll[0] = now
            if not live or live.get("worker_token") != token:
                raise _LeaseLost("The job lease belongs to another worker or has been released.")
            live_state = live.get("state")
            if live_state == "pause_requested":
                raise _StopJob("paused", "pause_requested", "Paused by the user. Resume keeps saved candidates and leads.")
            if live_state == "cancel_requested":
                raise _StopJob("cancelled", "cancel_requested", "Cancelled by the user. Existing saved leads were preserved.")
            if live_state != "running":
                raise _LeaseLost(f"The job is no longer running ({live_state}).")
        if now - started >= settings["max_minutes"] * 60:
            raise _StopJob("partial", "time_budget", "This run reached its time budget. Resume can continue.")

    def persist():
        checkpoint["elapsed_seconds"] = prior_elapsed + time.monotonic() - started
        checkpoint["completed_query_indices"] = sorted(completed_queries)
        checkpoint["run_candidates"] = len(run_attempted)
        progress["queries_completed"] = len(completed_queries)
        repo.update_job(job_id, worker_token=token, progress=dict(progress), checkpoint=dict(checkpoint))

    def log(level, message):
        repo.add_job_log(job_id, level, str(message), worker_token=token)

    def heartbeat():
        while not heartbeat_stop.wait(HEARTBEAT_SECONDS):
            try:
                check_authorization()
                repo.heartbeat_job(job_id, token)
            except _StopJob:
                return
            except _LeaseLost as exc:
                heartbeat_failure.append(exc)
                return
            except Exception as exc:
                heartbeat_failure.append(exc)
                return

    def wait_backoff(attempt):
        deadline = time.monotonic() + min(8.0, BACKOFF_BASE_SECONDS * 2 ** attempt)
        while time.monotonic() < deadline:
            pulse()
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def change_candidate(candidate, **fields):
        pulse(force=True)
        first_attempt = int(candidate.get("attempts") or 0) == 0 and int(fields.get("attempts") or 0) > 0
        repo.update_candidate(candidate["id"], worker_token=token, **fields)
        candidate.update(fields)
        if first_attempt:
            progress["inspected"] += 1

    def finish_candidate(candidate, outcome, *, lead_id=None, message="", candidate_state="done"):
        previous_outcome = candidate.get("outcome")
        change_candidate(candidate, state=candidate_state, outcome=outcome, error=message or None, lead_id=lead_id)
        counter = "errors" if outcome in {"access_blocked", "extraction_error"} else outcome
        if counter in {"duplicate", "excluded_website", "excluded_phone", "unknown", "errors"} and previous_outcome != outcome:
            progress[counter] += 1
        checkpoint["last_candidate_source_key"] = candidate["source_key"]
        persist()

    def process_candidate(collector, candidate, query):
        pulse(force=True)
        source_key = candidate["source_key"]
        if source_key not in run_attempted:
            if len(run_attempted) >= settings["max_candidates"]:
                raise _StopJob("partial", "candidate_budget", "This run reached its candidate budget. Resume can continue.")
            run_attempted.add(source_key)
        existing = existing_by_key.get(source_key) or repo.find_lead_by_source_key(source_key)
        if existing:
            existing_by_key[source_key] = existing
        if existing and not settings["refresh_existing"]:
            change_candidate(candidate, attempts=int(candidate.get("attempts") or 0) + 1)
            finish_candidate(candidate, "duplicate", lead_id=existing["id"])
            return
        # Refresh means a fresh detail read, not recycling the old observation.
        observation = None
        if not (existing and settings["refresh_existing"]) and settings["cache_hours"] > 0:
            observation = repo.get_cached_observation(source_key, max_age_hours=settings["cache_hours"])
            if observation and observation.get("website_status") not in {"found", "confirmed_absent"}:
                observation = None
        if observation is not None:
            observation = dict(observation)
            observation.update(city=query.get("city", ""), niche=query.get("niche", ""),
                               country=query.get("country", ""), country_code=query.get("country_code", ""))
            change_candidate(candidate, attempts=int(candidate.get("attempts") or 0) + 1)
        else:
            last_error = None
            for attempt in range(MAX_ATTEMPTS_PER_RUN):
                pulse(force=True)
                change_candidate(candidate, state="pending", attempts=int(candidate.get("attempts") or 0) + 1, error=None)
                persist()
                try:
                    observation = collector.collect(candidate, query)
                    if not isinstance(observation, dict) or not observation.get("name"):
                        raise TransientScrapeError("The collector returned no verified business name.")
                    break
                except AccessBlockedError as exc:
                    finish_candidate(candidate, "access_blocked", message=str(exc), candidate_state="error")
                    raise
                except (TransientScrapeError, ScraperError) as exc:
                    observation = None
                    last_error = str(exc)
                    log("warning", f"Candidate extraction attempt {attempt + 1}/{MAX_ATTEMPTS_PER_RUN} failed: {exc}")
                    if attempt + 1 < MAX_ATTEMPTS_PER_RUN:
                        wait_backoff(attempt)
            if observation is None:
                finish_candidate(candidate, "extraction_error", message=last_error or "Extraction failed.", candidate_state="error")
                return
        pulse(force=True)
        observation.setdefault("source_key", source_key)
        observation.setdefault("maps_link", candidate["url"])
        from .quality import prepare_lead
        lead = prepare_lead(observation, config=config)
        if lead.get("website_status") in {"found", "confirmed_absent"}:
            pulse(force=True)
            repo.cache_observation(source_key, observation, job_id=job_id, worker_token=token)
        lead_keys = _lead_keys(lead)
        existing = next((existing_by_key[key] for key in lead_keys if key in existing_by_key), None)
        if not existing:
            for key in lead_keys:
                existing = repo.find_lead_by_source_key(key)
                if existing:
                    existing_by_key[key] = existing
                    break
        if existing and not settings["refresh_existing"]:
            finish_candidate(candidate, "duplicate", lead_id=existing["id"])
            return
        exclusion, message = eligibility(lead, settings)
        # Refresh existing records even when the latest observations no longer
        # qualify them for this campaign. Repository preserves CRM/DNC/archive.
        if exclusion and not existing:
            finish_candidate(candidate, exclusion, message=message, candidate_state="excluded")
            return
        pulse(force=True)
        lead_id, created = repo.save_lead(
            lead, job_id=job_id, campaign_id=job.get("campaign_id"), query=query,
            worker_token=token,
        )
        progress["saved" if created else "refreshed"] += 1
        persisted = repo.get_lead(lead_id) or {**lead, "id": lead_id}
        for key in _lead_keys(persisted) | {source_key}:
            existing_by_key[key] = persisted
        finish_candidate(candidate, "saved" if created else "refreshed", lead_id=lead_id,
                         message=f"Refreshed; current filter: {message}" if exclusion else "")

    heartbeat_thread = threading.Thread(target=heartbeat, name=f"lead-job-{job_id}-heartbeat", daemon=True)
    try:
        pulse(force=True)
        repo.heartbeat_job(job_id, token)
        heartbeat_thread.start()
        log("info", f"Worker started: target {settings['target_leads']} new leads total; this run allows {settings['max_candidates']} candidates and {settings['max_minutes']:g} minutes.")
        if not queries:
            raise ValueError("The saved job contains no queries.")
        if progress["saved"] >= settings["target_leads"]:
            raise _StopJob("completed", "target_reached", "The cumulative new-lead target was already reached.")
        with collector_factory(settings=settings, pulse=pulse, log=log) as collector:
            for query_index, query in enumerate(queries):
                pulse(force=True)
                checkpoint.update(query_index=query_index, query=query)
                persist()
                # Persisted candidates are handled before a fresh traversal.
                pending = [row for row in known_candidates.values()
                           if int(row.get("query_index") or 0) == query_index and row.get("state") in {"pending", "error"}]
                for candidate in pending:
                    process_candidate(collector, candidate, query)
                    if progress["saved"] >= settings["target_leads"]:
                        raise _StopJob("completed", "target_reached", "The cumulative new-lead target was reached.")
                if query_index in completed_queries:
                    continue
                discovery_ok = False
                for attempt in range(MAX_ATTEMPTS_PER_RUN):
                    pulse(force=True)
                    try:
                        for incoming in collector.iter_candidates(query, seen_keys=set(known_candidates)):
                            pulse(force=True)
                            if len(run_attempted) >= settings["max_candidates"]:
                                raise _StopJob("partial", "candidate_budget", "This run reached its candidate budget. Resume can continue.")
                            key = incoming["source_key"]
                            if key in known_candidates:
                                continue
                            candidate = repo.upsert_candidate(
                                job_id, key, incoming["url"], preview_name=incoming.get("preview_name", ""),
                                query_index=query_index, worker_token=token,
                            )
                            known_candidates[key] = candidate
                            progress["discovered"] = len(known_candidates)
                            persist()
                            process_candidate(collector, candidate, query)
                            if progress["saved"] >= settings["target_leads"]:
                                raise _StopJob("completed", "target_reached", "The cumulative new-lead target was reached.")
                        discovery_ok = True
                        break
                    except AccessBlockedError:
                        raise
                    except TransientScrapeError as exc:
                        query_failures[query_index] = str(exc)
                        log("warning", f"Search attempt {attempt + 1}/{MAX_ATTEMPTS_PER_RUN} failed: {exc}")
                        if attempt + 1 < MAX_ATTEMPTS_PER_RUN:
                            wait_backoff(attempt)
                if discovery_ok:
                    query_failures.pop(query_index, None)
                    stop_reason = (getattr(collector, "last_discovery", {}) or {}).get("reason", "feed_end")
                    if stop_reason == "feed_stalled":
                        stalled_queries.add(query_index)
                    else:
                        completed_queries.add(query_index)
                else:
                    progress["errors"] += 1
                persist()
        error_candidates = [row for row in repo.list_candidates(job_id) if row.get("state") == "error"]
        if query_failures or error_candidates:
            state = "partial" if progress["saved"] or progress["refreshed"] else "failed"
            reason = "collection_errors"
            error = "; ".join(query_failures.values()) or f"{len(error_candidates)} candidates remain in an error state. Resume can retry them."
        elif stalled_queries:
            state, reason = "partial", "discovery_stalled"
            error = "The result feed stopped producing new links before an explicit end marker. Resume can retry discovery."
        else:
            state, reason = "partial", "results_exhausted"
    except _StopJob as exc:
        state, reason, error = exc.state, exc.reason, exc.message
    except _LeaseLost as exc:
        # Do not overwrite a new owner's job, checkpoint, or candidates.
        return {"id": job_id, "state": "lease_lost", "reason": str(exc), "progress": dict(progress)}
    except AccessBlockedError as exc:
        state = "partial" if progress["saved"] or progress["refreshed"] else "failed"
        reason, error = "access_blocked", str(exc)
    except KeyboardInterrupt:
        state, reason, error = "paused", "worker_interrupted", "The worker was interrupted. Saved progress can be resumed."
    except Exception as exc:
        state = "partial" if progress["saved"] or progress["refreshed"] else "failed"
        reason, error = "worker_error", f"{type(exc).__name__}: {exc}"
    finally:
        heartbeat_stop.set()
        if heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=2)
    live = repo.get_job(job_id)
    if live and live.get("worker_token") == token and live.get("state") == "cancel_requested":
        state, reason, error = "cancelled", "cancel_requested", "Cancelled by the user; saved leads were preserved."
    remaining = max(0, settings["target_leads"] - progress["saved"])
    summary = f"{reason}: saved {progress['saved']}/{settings['target_leads']} new leads; {remaining} remaining."
    checkpoint.update(
        elapsed_seconds=prior_elapsed + time.monotonic() - started,
        completed_query_indices=sorted(completed_queries),
        run_candidates=len(run_attempted), query_errors=query_failures,
        stalled_query_indices=sorted(stalled_queries), summary=f"{summary} {error}".strip(),
    )
    progress["queries_completed"] = len(completed_queries)
    try:
        # The final update is fenced, too. Paused/terminal states release the lease.
        log("error" if state == "failed" else "info", f"{summary} {error}".strip())
        repo.update_job(
            job_id, worker_token=token, state=state, reason=reason,
            error=(error or None) if reason in {"access_blocked", "collection_errors", "worker_error", "discovery_stalled"} else None,
            progress=dict(progress), checkpoint=checkpoint, finished_at=utc_now(),
        )
    except Exception as exc:
        live = repo.get_job(job_id)
        if not live or live.get("worker_token") != token:
            return {"id": job_id, "state": "lease_lost", "reason": str(exc), "progress": dict(progress)}
        raise
    return repo.get_job(job_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run queued Lead Finder jobs without Streamlit.")
    parser.add_argument("--db", required=True, help="Path to the SQLite lead database.")
    parser.add_argument("--config", required=True, help="Path to the JSON configuration.")
    args = parser.parse_args(argv)
    from .config import app_paths, load_config
    from .storage import Repository

    stop = threading.Event()
    previous_handlers = {}

    def on_signal(signum, frame):
        stop.set()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, on_signal)
        authorization_check = None
        config = load_config(args.config)
        repo = Repository(args.db, config=config)
        repo.initialize()
        token = uuid.uuid4().hex
        while not stop.is_set():
            # Recheck before every claim.  A worker started before a reset may
            # not consume later queued jobs after the account has changed.
            if authorization_check is not None:
                authorization_check()
            job = repo.claim_next_job(token)
            if not job:
                break
            result = run_job(repo, job, config, stop_event=stop, authorization_check=authorization_check)
            print(json.dumps({key: result.get(key) for key in ("id", "state", "reason", "progress")}), flush=True)
            if result.get("state") in {"paused", "lease_lost"} or result.get("reason") == "access_blocked":
                break
        return 0
    except Exception as exc:
        print(f"Worker could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
