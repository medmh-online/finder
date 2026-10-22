"""Read-only, local phone matching/reply diagnostics with explicit assignment action.

Never uses Gemini, guesses an opaque @lid identity, or sends WhatsApp messages.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import streamlit as st
from .ai_outreach import normalize_phone


def find_leads_for_phone(db, entered: str, region: str = "MA") -> dict:
    """Match user-entered phone locally across original and E.164 stored fields.

    Return match candidates but never infer @lid digits are phone numbers.
    """
    target = normalize_phone(entered, region)
    if not target:
        return {"normalized": "", "matches": [], "error": "Invalid or ambiguous phone number"}
    found = []
    with db.connect() as con:
        rows = con.execute("""SELECT l.id,l.name,l.phone,l.phone_e164,l.country_code,
            l.archived,l.do_not_contact,a.worker_id AS assigned_worker,
            COALESCE(ctrl.paused,0) AS ai_paused,
            COALESCE(ctrl.handoff,0) AS ai_handoff,
            thr.last_inbound,thr.replied_through,thr.retry_after,
            trace.stage AS reply_stage,trace.detail AS reply_detail,trace.updated_at AS trace_time,
            (SELECT MAX(m.id) FROM ai_messages m WHERE m.lead_id=l.id AND m.direction='in') AS last_inbound_id,
            (SELECT m.status FROM ai_messages m WHERE m.lead_id=l.id AND m.direction='out' ORDER BY m.id DESC LIMIT 1) AS last_out_status,
            perm.opted_out_at
            FROM leads l
            LEFT JOIN ai_assignments a ON a.lead_id=l.id
            LEFT JOIN ai_lead_control ctrl ON ctrl.lead_id=l.id
            LEFT JOIN ai_threads thr ON thr.lead_id=l.id
            LEFT JOIN ai_reply_trace trace ON trace.lead_id=l.id
            LEFT JOIN ai_permissions perm ON perm.lead_id=l.id""")
        for row in rows:
            values = {v for field in ('phone_e164','phone')
                      if (v := normalize_phone(row[field], row['country_code'] or region))}
            if target in values:
                item = dict(row)
                # Conflicting saved phones must not be silently linked to WhatsApp.
                item['conflicting_numbers'] = len(values) > 1
                found.append(item)
    return {"normalized": target, "matches": found, "error": ""}


def diagnose_match(item, worker_id: str) -> list[str]:
    """Reasons the worker might not respond, without changing permissions."""
    reasons = []
    if item['conflicting_numbers']:
        reasons.append('Saved phone and E.164 fields disagree. Correct the lead before matching.')
    if not item['assigned_worker']:
        reasons.append('This lead is not assigned to any AI worker.')
    elif item['assigned_worker'] != worker_id:
        reasons.append('This lead is assigned to a different worker.')
    if item['archived']:
        reasons.append('This lead is archived.')
    if item['ai_paused']:
        reasons.append('AI is paused for this lead.')
    if item['ai_handoff']:
        reasons.append('This lead is handed over to a human.')
    if not item['last_inbound_id']:
        reasons.append('No inbound message is linked to this lead.')
    elif item['last_inbound_id'] <= (item['replied_through'] or 0):
        reasons.append('Most recent inbound was already handled or intentionally ignored.')
    if item.get('reply_stage') in ('retry_scheduled','unconfirmed','send_failed','send_blocked'):
        reasons.append('Reply pipeline: '+str(item['reply_stage'])+' — '+str(item.get('reply_detail') or ''))
    if item['last_out_status'] in ('queued','queued_reply','sending'):
        reasons.append('A reply is queued or sending; check gateway status.')
    if item['last_out_status'] in ('failed','uncertain','blocked'):
        reasons.append('Most recent outgoing message was not confirmed sent.')
    return reasons


def _date(ts):
    if not ts:
        return '—'
    return datetime.fromtimestamp(float(ts),timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def render_reply_diagnostics(db, worker_id: str, live: dict) -> None:
    """Small, opt-in diagnostic within the existing WhatsApp worker tab."""
    with st.expander('Why no AI reply? · Check a lead', expanded=False):
        st.caption('Checks local phone matching, assignment, AI settings, and event history. Does not contact the number or spend Gemini tokens.')
        worker = db.worker(worker_id) or {}
        state = live.get('state','offline')
        if state != 'ready':
            st.error('WhatsApp AI connection is not READY (' + str(state) + ').')
        if not worker.get('enabled'):
            st.warning('AI replies are disabled. Enable them in Controls, then save.')
        if not worker.get('model_name') or not worker.get('model_key_id'):
            st.warning('No selected Gemini model/key. Configure one under Gemini keys + models.')
        if worker.get('model_name') and worker['model_name'] not in db.working_models(worker_id):
            st.warning('Selected Gemini model has no currently tested compatible key.')
        settings=db.account_settings()
        if db.daily_used(worker_id)>=int(settings['daily_ai_limit']):
            st.error('Daily Gemini generation-request limit is exhausted for this worker.')
        if not db.token_budget_available(worker_id):
            st.error('Daily token budget is too low for another AI reply (300-token reservation). Increase the worker limit or allow UTC-day reset.')

        with st.form('reply_diagnostics_phone_'+worker_id):
            number = st.text_input('Phone number already entered in Leads', key='reply_diag_number_'+worker_id,
                placeholder='+212 6 12 34 56 78')
            region = st.selectbox('Default country for local numbers', ['MA','FR','BE','ES','GB','US','DZ','TN'],
                key='reply_diag_country_'+worker_id)
            run = st.form_submit_button('Diagnose', type='primary')
        state_key = 'reply_diag_checked_'+worker_id
        if run:
            st.session_state[state_key] = (number, region)
        last = st.session_state.get(state_key)
        if not last:
            return
        try:
            result = find_leads_for_phone(db, *last)
        except Exception as exc:
            st.error('Cannot inspect lead database: ' + type(exc).__name__)
            return
        if result['error']:
            st.error(result['error'])
            return
        matches = result['matches']
        st.caption('Normalized locally: ' + result['normalized'])
        if not matches:
            st.error('No lead stores this normalized phone number. Check the country and saved phone fields.')
        elif len(matches)>1:
            st.error(f'{len(matches)} leads share this phone number. Resolve duplicates before automatic identity matching.')
        else:
            item = matches[0]
            st.write(f"Lead #{item['id']} · {item['name']}")
            reasons = diagnose_match(item, worker_id)
            if reasons:
                for reason in reasons: st.warning(reason)
            else:
                st.success('Lead assignment and reply state look eligible. Check whether its message reached the gateway.')
            st.caption('Latest linked inbound: ' + _date(item['last_inbound']))
            if item.get('reply_stage'):
                st.write('**Reply pipeline:** '+str(item['reply_stage']).replace('_',' ').title())
                if item.get('reply_detail'):
                    st.caption(str(item['reply_detail']))
                st.caption('Last pipeline update: '+_date(item.get('trace_time')))
            if item.get('retry_after') and float(item['retry_after'])>datetime.now(timezone.utc).timestamp():
                st.info('The last AI failure is on cooldown until '+_date(item['retry_after'])+
                        '. A new incoming message clears this cooldown.')
            if item.get('last_inbound_id') and not item.get('reply_stage'):
                st.info('No reply pipeline trace yet. Start the AI gateway with START.bat and send a new message.')
            if item.get('last_inbound_id') and item['last_inbound_id'] > int(item.get('replied_through') or 0):
                if worker.get('enabled') and state == 'ready' and not item.get('ai_paused') and not item.get('ai_handoff'):
                    st.info('Inbound is stored but still awaiting an AI decision or queued reply. Check the gateway terminal for [reply] or [agent loop] errors.')
            with db.connect() as con:
                pending = con.execute('''SELECT status,kind,created_at FROM ai_messages
                    WHERE lead_id=? AND direction='out' ORDER BY id DESC LIMIT 1''', (item['id'],)).fetchone()
            if pending:
                st.caption('Last outbound attempt: ' + str(pending['status']) + ' · ' + str(pending['kind']) + ' · ' + _date(pending['created_at']))

            if not item['assigned_worker'] and not item['conflicting_numbers'] and not item['archived']:
                if st.button('Assign this lead to this WhatsApp worker',key='reply_diag_assign_'+worker_id):
                    db.assign(worker_id,[int(item['id'])])
                    st.success('Assigned. Send a NEW inbound message from a different WhatsApp account to test.')
                    st.rerun()
        # Count by event type only; avoid exposing WhatsApp message contents.
        unmatched = db.unmatched(worker_id)
        if unmatched:
            st.warning('Messages in this list have NOT been linked to a lead. Gemini cannot reply until WhatsApp verifies the sender identity.')
            recent = unmatched[:8]
            types = ', '.join('LID' if str(x['chat_id']).endswith('@lid') else 'phone chat'
                              for x in recent)
            st.warning(f'{len(unmatched)} recent unmatched incoming event(s) for this worker. Types: {types}.')
            st.caption('Opaque WhatsApp @lid IDs must be resolved through a WhatsApp-verified phone mapping, not by guessing their digits.')
        if state == 'ready' and unmatched and any(str(x['chat_id']).endswith('@lid') for x in unmatched):
            st.caption('If these are your messages, WhatsApp may not yet expose their verified phone mapping.')
            if st.button('Retry matching unmatched WhatsApp identities', key='reply_diag_retry_'+worker_id):
                try:
                    from .ai_runtime import node_api
                    response = node_api('/recover-identities', {'workerId': worker_id}, timeout=8)
                    if response.get('started'):
                        st.info('Safe identity recovery started. Refresh this diagnostic after a few seconds. No messages are sent.')
                    elif response.get('running'):
                        st.info('Identity recovery is already running. Refresh after it finishes.')
                    elif response.get('cooldown'):
                        st.info('A recovery check was run recently. Try again in about two minutes.')
                    else:
                        st.warning('The WhatsApp worker did not start identity recovery.')
                except Exception as exc:
                    st.error('Cannot request identity recovery: ' + type(exc).__name__)
        # Show safe, category-only API failures, not key-bearing raw response text.
        with db.connect() as con:
            last_error = con.execute("""SELECT error FROM ai_usage
                WHERE worker_id=? AND error!='' ORDER BY id DESC LIMIT 1""",(worker_id,)).fetchone()
        if last_error:
            message=str(last_error[0]).casefold()
            category=('Quota/rate limit' if '429' in message or 'quota' in message else
                      'Access / key / location restriction' if any(k in message for k in ('401','403','location','permission')) else
                      'Model/API error')
            st.warning('Most recent Gemini failure category: '+category+'. Check the AI gateway console for details (keep your API keys private).')
        st.caption('Self-sent WhatsApp messages are ignored. Use a separate WhatsApp account; a new message may be needed after assignment.')
