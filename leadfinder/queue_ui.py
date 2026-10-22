"""Send-only WhatsApp queue UI: current job only, no sent-message history."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import time

import streamlit as st

from .ai_workers import WorkerDB
from .message_queue import (
    active_batch, control, init_queue, prepare, queue_contacts_today,
    register_queue_worker,
)
from .ai_runtime import node_api


def _time(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if ts else '—'


@st.fragment(run_every=3.0)
def _current_send(access, worker_id):
    """Progress for the only active queue; no history, recipient log, or selector."""
    db = init_queue(WorkerDB(access.paths().database))
    current = active_batch(db, worker_id, st.session_state.get('sq_active_batch'))
    if not current:
        if st.session_state.pop('sq_active_batch', None):
            st.toast('Queue finished or cancelled. No send history is retained.')
            st.rerun()  # Refresh the main form so another send can be started.
        return
    st.session_state['sq_active_batch'] = current['id']
    st.subheader('Current sending')
    if current['state'] == 'paused':
        st.info('Sending is paused. Resume this batch or cancel it before starting a new one.')
    if current['state'] == 'running':
        status_path = access.paths().data / 'queue_dispatcher_status.json'
        try:
            dispatcher = json.loads(status_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            dispatcher = {}
        age = time.time() - float(dispatcher.get('heartbeat_at') or 0)
        if dispatcher.get('db_path') and dispatcher['db_path'] != str(access.paths().database.resolve()):
            st.error('Queue dispatcher uses a different database. Check the START.bat console.')
        elif not dispatcher.get('heartbeat_at') or age > 20:
            st.error('Queue dispatcher is not responding. Check the START.bat console.')
        elif dispatcher.get('last_error'):
            st.error('Queue dispatcher: ' + str(dispatcher['last_error'])[:200])
    counts = st.columns(4)
    counts[0].metric('Numbers', current['total'] or 0)
    # An attempted send without a WhatsApp ID is not proof of delivery.
    counts[1].metric('Sent / attempted', int(current['sent'] or 0) + int(current['uncertain'] or 0))
    counts[2].metric('Waiting', sum(int(current[x] or 0) for x in ('pending','checking','sending')))
    counts[3].metric('Skipped', current['skipped'] or 0)
    if current['state'] == 'running' and current['next_at']:
        remaining = max(0, int(current['next_at'] - time.time()))
        st.caption(f'Next recipient: {_time(current["next_at"])} · about {remaining}s remaining')
    a,b,c = st.columns(3)
    if a.button('Pause', disabled=current['state']!='running', key='sq_pause', width='stretch'):
        try:
            control(db,current['id'],'pause')
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if b.button('Resume', disabled=current['state']!='paused', key='sq_resume', width='stretch'):
        try:
            control(db,current['id'],'resume')
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if c.button('Cancel', key='sq_cancel', width='stretch'):
        try:
            control(db,current['id'],'cancel')
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    st.caption('Only the active batch is kept temporarily for delays and safe recovery. Completed batches are deleted automatically. Unconfirmed attempts are not retried.')


def render_queue(access):
    db = init_queue(WorkerDB(access.paths().database))
    st.header('Message Queue')
    st.caption('Send numbers through WhatsApp · no saved batch history · local number normalization')
    workers = db.workers()
    if not workers:
        st.info('Create a WhatsApp worker in AI Workers first.')
        return
    selected = st.selectbox('Send from worker', [w['id'] for w in workers],
         format_func=lambda wid:next(w['name'] for w in workers if w['id']==wid),key='sq_worker')
    worker = db.worker(selected)
    try:
        register_queue_worker(selected)
    except Exception as exc:
        st.error('Queue background registration failed: ' + str(exc)[:180])
    try:
        live = node_api('/status',{'workerId':selected},timeout=3)
    except Exception:
        live = {}
    metrics = st.columns(2)
    metrics[0].metric('WhatsApp',live.get('state','offline'))
    metrics[1].metric('Contacts attempted today',
        f"{db.daily_new_contact_used(selected) + queue_contacts_today(db,selected)} / {worker['daily_new_contacts']}")
    current = active_batch(db,selected,st.session_state.get('sq_active_batch'))
    ready = live.get('state')=='ready'
    if not ready:
        st.warning('WhatsApp is not READY. Connect the worker before sending.')
    with st.container(border=True):
        st.subheader('Send message')
        numbers = st.text_area('Numbers · one per line, comma or semicolon',height=135,
            placeholder='06 12 34 56 78\n00212 6 98 76 54 32\n+212 6 61 23 45 67',key='sq_numbers')
        country = st.selectbox('Default number country',['MA','FR','BE','ES','GB','US','DZ','TN'],key='sq_country')
        message = st.text_area('Message',height=95,key='sq_message',
            placeholder='Type the message to send exactly as written.')
        a,b = st.columns(2)
        lo = a.number_input('Minimum delay between numbers (seconds)',1,3600,5,key='sq_min')
        hi = b.number_input('Maximum delay between numbers (seconds)',1,3600,30,key='sq_max')
        if st.button('Send messages',type='primary',key='sq_send',width='stretch',
                     disabled=bool(current) or not ready):
            batch_id = None
            try:
                batch_id = prepare(db,selected,numbers,message,country,lo,hi)
                control(db,batch_id,'start')
                st.session_state['sq_active_batch'] = batch_id
                st.success('Sending started.')
                st.rerun()
            except Exception as exc:
                # If preparing succeeded but start failed, cancel the unsent
                # batch so it never lingers as an accidental send job.
                if batch_id:
                    try:control(db,batch_id,'cancel')
                    except Exception:pass
                st.error(str(exc)[:400])
        if current:
            st.caption('Finish or cancel the current send before starting another.')
    st.caption('Use only for recipients you are authorized to contact. Existing do-not-contact requests remain enforced.')
    _current_send(access,selected)
