"""Standalone WhatsApp number-list queue; no CRM leads are created or modified."""
from __future__ import annotations
from datetime import datetime,timezone
import json
import time
import pandas as pd
import streamlit as st
from .ai_workers import WorkerDB
from .ai_outreach import GlobalContactLedger
from .message_queue import init_queue,prepare,batches,entries,control,queue_contacts_today,register_queue_worker
from .ai_runtime import app_paths,node_api


def _time(ts):
    return datetime.fromtimestamp(float(ts),timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if ts else '—'


def render_queue(access):
    db=init_queue(WorkerDB(access.paths().database))
    st.header('Message Queue')
    st.caption('Standalone number lists · no Leads or CRM records · uses existing WhatsApp workers and Gemini keys')
    workers=db.workers()
    if not workers:
        st.info('Create a WhatsApp worker in AI Workers first.')
        return
    selected=st.selectbox('Send from worker',[w['id'] for w in workers],
         format_func=lambda wid:next(w['name'] for w in workers if w['id']==wid),key='sq_worker')
    worker=db.worker(selected)
    # A manually created queue worker may never have visited the AI Workers page.
    try:
        register_queue_worker(selected)
    except Exception as exc:
        st.error('Queue background registration failed: '+str(exc)[:180])
    try:live=node_api('/status',{'workerId':selected},timeout=3)
    except Exception:live={}
    metrics=st.columns(3)
    metrics[0].metric('WhatsApp',live.get('state','offline'))
    metrics[1].metric('New contacts today',f"{db.daily_new_contact_used(selected)+queue_contacts_today(db,selected)} / {worker['daily_new_contacts']}")
    tokens=db.tokens_today(selected)
    metrics[2].metric('Gemini tokens today',f"{tokens['total']:,} / {worker['daily_token_limit']:,}")
    if live.get('state')!='ready':
        st.warning('WhatsApp is not READY. Prepare the queue now; sending begins only after this worker connects.')
    with st.container(border=True):
        st.subheader('Prepare numbers')
        numbers=st.text_area('Numbers · one per line, comma or semicolon',height=135,
            placeholder='06 12 34 56 78\n00212 6 98 76 54 32\n+212 6 61 23 45 67',key='sq_numbers')
        country=st.selectbox('Default number country',['MA','FR','BE','ES','GB','US','DZ','TN'],key='sq_country')
        st.caption('Gemini uses the selected worker’s model and key pool to normalize phone numbers once. Invalid or ambiguous numbers are rejected. Original order is preserved.')
        msg=st.text_area('Message · sent exactly as written',height=95,key='sq_message',
            placeholder='Type the message to send unchanged to each valid number.')
        a,b=st.columns(2)
        lo=a.number_input('Minimum gap between sends (seconds)',1,3600,5,key='sq_min')
        hi=b.number_input('Maximum gap between sends (seconds)',1,3600,30,key='sq_max')
        if st.button('Normalize numbers with Gemini & preview',type='primary',key='sq_prepare'):
            try:
                batch=prepare(db,selected,numbers,msg,country,lo,hi)
                st.session_state['sq_inspect']=batch
                st.success('Saved number preview. Review below, then Start.')
                st.rerun()
            except Exception as exc:st.error(str(exc)[:400])
    st.caption('This page does not add leads or require CRM permission records. Use it for recipients you are authorized to message. Existing do-not-contact requests are still respected.')
    history=batches(db,50)
    top=st.columns([4,1])
    top[0].subheader('Queue monitoring')
    if top[1].button('Refresh queue',key='sq_refresh',width='stretch'):
        st.rerun()
    if not history:
        st.info('No queues prepared yet.')
        return
    summary=[{'Created':_time(b['created_at']),'Worker':b['worker_name'],
        'State':b['state'],'Sent':b['sent'] or 0,'Pending':(b['pending'] or 0)+(b['checking'] or 0)+(b['sending'] or 0),
        'Skipped':b['skipped'] or 0,'Uncertain':b['uncertain'] or 0,'Batch':b['id'][:10]} for b in history]
    st.dataframe(pd.DataFrame(summary),hide_index=True,width='stretch')
    lookup={b['id']:b for b in history}
    wanted=st.session_state.get('sq_inspect')
    if wanted not in lookup:wanted=history[0]['id']
    batch_id=st.selectbox('Inspect batch',list(lookup),index=list(lookup).index(wanted),
        format_func=lambda key:f"{_time(lookup[key]['created_at'])} · {lookup[key]['worker_name']} · {lookup[key]['state']} · {key[:8]}",key='sq_inspect')
    batch=lookup[batch_id]
    if batch['state']=='running':
        # Read the dedicated queue process heartbeat, not the AI server port.
        # The AI gateway can be healthy even if its queue dispatcher is down.
        status_path=access.paths().data/'queue_dispatcher_status.json'
        try:
            dispatcher=json.loads(status_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            dispatcher={}
        heartbeat=float(dispatcher.get('heartbeat_at') or 0)
        age=time.time()-heartbeat if heartbeat else None
        if dispatcher.get('db_path') and dispatcher['db_path']!=str(access.paths().database.resolve()):
            st.error('Queue dispatcher is using a DIFFERENT database. Check the console paths. No queue sends can happen until these match.')
        elif not heartbeat or age>20:
            st.error('Independent queue process is NOT reporting. Check the START.bat window for [queue dispatcher] messages; it should show a PID and database path.')
        elif dispatcher.get('last_error'):
            st.error('Queue dispatcher error: '+str(dispatcher['last_error'])[:250])
        else:
            phase=dispatcher.get('phase','unknown')
            st.caption(f'Queue process alive · PID {dispatcher.get("pid", "?")} · {phase} · heartbeat {int(max(0,age))}s ago')
            checked=float(batch.get('last_polled_at') or 0)
            polled_age=time.time()-checked if checked else None
            if polled_age is None or polled_age>20:
                st.warning('Queue process is alive, but this batch was not polled recently. If phase is polling, it may be waiting for a WhatsApp response; check the console.')
    m=st.columns(4)
    m[0].metric('Numbers',batch['total'] or 0)
    m[1].metric('Sent',batch['sent'] or 0)
    m[2].metric('Waiting',(batch['pending'] or 0)+(batch['checking'] or 0)+(batch['sending'] or 0))
    m[3].metric('Skipped / uncertain',(batch['skipped'] or 0)+(batch['uncertain'] or 0))
    if batch['next_at'] and batch['state']=='running':
        remaining=max(0,int(batch['next_at']-datetime.now(timezone.utc).timestamp()))
        st.caption(f'Next recipient: {_time(batch["next_at"])} · around {remaining}s remaining')
    cols=st.columns(4)
    for col,label,action in zip(cols,['Start','Pause','Resume','Cancel'],['start','pause','resume','cancel']):
        possible=(action in ('start','resume') and batch['state']=='paused') or (action in ('pause','cancel') and batch['state']=='running')
        if col.button(label,key='sq_'+action,disabled=not possible,width='stretch'):
            try:control(db,batch_id,action);st.rerun()
            except Exception as exc:st.error(str(exc))
    items=entries(db,batch_id)
    st.dataframe(pd.DataFrame([{
        'Original':item['raw_number'],'Normalized':item['e164'] or '—',
        'Verified WhatsApp ID':item.get('verified_chat_id') or 'Not checked yet',
        'Occurrence':item['occurrence'] or '—','Status':item['status'],
        'Detail':item['reason'],'Attempted':_time(item['attempted_at'])} for item in items]),
        hide_index=True,width='stretch')
    # An ID-less provider response is intentionally kept uncertain even if
    # the user can see delivery in WhatsApp. Permit explicitly recording human
    # verification, NEVER a resend or a fabricated WhatsApp message ID.
    uncertain=[item for item in items if item['status']=='uncertain']
    if uncertain:
        st.warning(f'{len(uncertain)} send(s) have no provider-confirmed message ID. Do not retry automatically.')
        ids=[int(item['id']) for item in uncertain]
        selected_id=st.selectbox('Review an uncertain send',ids,
            format_func=lambda iid:next((f"Item {iid} · {x['raw_number']} · {x['reason']}" for x in uncertain if int(x['id'])==iid),str(iid)),
            key='sq_uncertain_item_'+batch_id)
        reviewed=st.checkbox('I checked this exact conversation in WhatsApp and saw the outgoing message.',
            key='sq_confirm_manual_'+batch_id+'_'+str(selected_id))
        if st.button('Record delivery verified manually (do not send again)',
                     key='sq_manual_confirm_'+batch_id,disabled=not reviewed):
            with db.connect(True) as c:
                updated=c.execute("UPDATE standalone_message_items SET status='sent',reason='Manually confirmed in WhatsApp (no provider message ID)',updated_at=? WHERE id=? AND batch_id=? AND status='uncertain'",
                       (time.time(),selected_id,batch_id)).rowcount
            if updated:
                st.success('Manual confirmation recorded. No WhatsApp message was sent by this action.')
                st.rerun()
            else:
                st.error('This record changed since it was displayed. Refresh the queue.')
    duplicates=sum(1 for item in items if item['occurrence']>1)
    if duplicates:st.warning(f'{duplicates} repeated number(s) detected; they remain separate entries, as in your original sender.')
    st.caption('Pause/resume is persistent. Uncertain deliveries require manual review and are never automatically resent. WhatsApp registration is checked before each send.')
