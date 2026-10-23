"""Local-only Streamlit worker interface."""
from __future__ import annotations

import json
import streamlit as st
from .ai_workers import WorkerDB
from .ai_agent import prepare_draft
from .ai_research import cleaned_public_page
from .reply_diagnostics import render_reply_diagnostics
from .ai_runtime import Registry,encrypt_key,decrypt_key,test_models,node_api,app_paths


def bridge(path, data):
    try:
        return node_api(path,data,timeout=14)
    except Exception as exc:
        return {'state':'offline','error':str(exc)[:180]}


def render_workers(access,repo):
    account=access.require()
    paths=access.paths()
    db=WorkerDB(paths.database).initialize()
    Registry(app_paths().data)  # Local worker registry, not exposed to browser.
    st.header('AI Workers')
    st.caption('Independent WhatsApp workers and Gemini models. Each worker can activate any saved instruction profile.')
    settings=db.account_settings()
    with st.expander('AI profiles and quotas',expanded=False):
        profiles=db.instruction_profiles()
        by_id={p['id']:p for p in profiles}
        st.subheader('Saved instruction profiles')
        st.caption('To change the assistant’s purpose, create a separate profile instead of replacing an existing role. Saved conversations stay tied to their original profile.')
        chosen=st.selectbox('Choose a profile to edit',list(by_id),
            format_func=lambda pid:by_id[pid]['name'],key='ai_profile_edit_select')
        current=by_id[chosen]
        with st.form('ai_profile_edit_form'):
            profile_name=st.text_input('Profile name',value=current['name'],max_chars=80)
            profile_instructions=st.text_area('All AI instructions',value=current['instructions'],
                height=300,help='Put role, verified facts, speaking style, rules, and handoff instructions together here (max 16,000 characters).')
            save_profile=st.form_submit_button('Save profile',type='primary')
        if save_profile:
            try:
                access.require()
                db.update_instruction_profile(chosen,profile_name,profile_instructions)
                st.success('Profile saved. New Gemini calls will use these instructions.')
                st.rerun()
            except ValueError as exc:st.error(str(exc))
        with st.expander('Create another profile'):
            with st.form('ai_new_profile'):
                new_name=st.text_input('New profile name',placeholder='Job Hunting')
                new_instructions=st.text_area('All instructions for this profile',height=230,
                    placeholder='Describe the assistant, verified facts, tasks, conversation style and when to hand off...')
                if st.form_submit_button('Create profile'):
                    try:
                        access.require()
                        profile_id=db.create_instruction_profile(new_name,new_instructions)
                        st.success('Profile created. Select it on a worker to activate it.')
                        st.rerun()
                    except ValueError as exc:st.error(str(exc))
        with st.expander('Delete selected profile'):
            st.caption('Profiles used by workers or saved conversations cannot be deleted.')
            if st.button('Delete this unused profile',key='ai_delete_instruction_profile'):
                try:
                    access.require()
                    db.delete_instruction_profile(chosen)
                    st.session_state.pop('ai_profile_edit_select',None)
                    st.rerun()
                except ValueError as exc:st.error(str(exc))
        st.divider()
        with st.form('ai_generation_quota'):
            limit=st.number_input('Daily generation limit per worker (rolling 24h)',
                min_value=1,max_value=50000,value=int(settings['daily_ai_limit']))
            if st.form_submit_button('Save quota'):
                access.require()
                db.update_settings(daily_ai_limit=int(limit))
                st.success('Worker generation quota saved.')
                st.rerun()
    with st.expander('Create another WhatsApp worker',expanded=not bool(db.workers())):
        with st.form('ai_worker_create'):
            name=st.text_input('Worker name',placeholder='Sales Worker 1')
            if st.form_submit_button('Create worker'):
                access.require()
                worker_id=db.create_worker(name)
                Registry(app_paths().data).register(worker_id,account)
                st.success('Worker created. Connect WhatsApp and add a Gemini key.');st.rerun()
    workers=db.workers()
    if not workers:return
    names={w['id']:f"{w['name']} · {w['lead_count']} leads" for w in workers}
    picked=st.selectbox('Manage worker',list(names),format_func=lambda x:names[x])
    worker=db.worker(picked)
    Registry(app_paths().data).register(picked,account)
    active,connection,keys = st.tabs(['Controls','WhatsApp browser','Gemini keys + models'])
    with active:
        st.subheader(worker['name'])
        profiles=db.instruction_profiles()
        names={profile['id']:profile['name'] for profile in profiles}
        active_id=worker.get('instruction_profile_id')
        st.markdown('**Active AI instruction profile**')
        if active_id not in names:
            st.error('The active profile is missing. Choose another profile to resume AI replies.')
        proposed=st.selectbox('Profile for this worker',list(names),
            index=list(names).index(active_id) if active_id in names else 0,
            format_func=lambda pid:names[pid],key='worker_instruction_profile_select_'+picked)
        if st.button('Activate selected profile',key='activate_profile_'+picked,
                     disabled=proposed==active_id):
            try:
                access.require()
                db.activate_instruction_profile(picked,proposed)
                st.success('Profile activated. New conversations use it; older ones retain their original profile.')
                st.rerun()
            except ValueError as exc:st.error(str(exc))
        blocked_count=db.paused_profile_threads(picked)
        if blocked_count:
            st.info(f'{blocked_count} older conversation(s) belong to another profile and are paused while this profile is active. Switching back restores their eligibility (subject to normal reply limits).')
        st.caption('Profiles are reusable across workers. Switching does not overwrite conversation history or change other workers.')
        st.caption(f"New contacts initiated today: {db.daily_new_contact_used(picked)} / {worker['daily_new_contacts']} (UTC)")
        generation=db.generation_quota(picked)
        st.metric('AI generation attempts · rolling 24h', f"{generation['used']:,} / {generation['limit']:,}")
        st.caption('Separate from token usage and new-contact limits. Failed Gemini API calls also count to prevent unlimited retries.')
        if generation['remaining']==0:
            st.error('AI replies are BLOCKED by the shared generation limit. Open "Shared business knowledge and quotas" above and raise the "Daily generation limit per worker" if appropriate, or wait for older requests to expire.')

        budget=db.tokens_today(picked)
        limit_now=int(worker.get('daily_token_limit') or 10000)
        st.progress(min(1.0,budget['total']/max(1,limit_now)))
        u1,u2,u3=st.columns(3)
        u1.metric('Input tokens today',f"{budget['input']:,}")
        u2.metric('Output tokens today',f"{budget['output']:,}")
        u3.metric('Remaining local budget',f"{max(0,limit_now-budget['total']):,}")
        with st.form('ai_worker_settings'):
            label=st.text_input('Name',worker['name'])
            enabled=st.toggle('AI replies enabled',value=bool(worker['enabled']))
            batching=st.slider('Combine consecutive customer messages after (seconds)',2,15,int(worker['batching_seconds']))
            c1,c2=st.columns(2)
            daily=c1.number_input('Maximum new contacts per UTC day',1,100,int(worker['daily_new_contacts']))
            token_limit=c2.number_input('Daily input + output token limit',100,2000000,limit_now,step=1000)
            d1,d2=st.columns(2)
            delay_min=d1.number_input('Minimum reply delay (seconds)',0,60,int(worker.get('reply_delay_min') or 4))
            delay_max=d2.number_input('Maximum reply delay (seconds)',0,60,int(worker.get('reply_delay_max') or 30))
            if st.form_submit_button('Save worker controls'):
                try:
                    db.configure_worker(picked,name=label,enabled=int(enabled),batching_seconds=batching,
                        daily_new_contacts=int(daily),
                        daily_token_limit=int(token_limit),reply_delay_min=int(delay_min),reply_delay_max=int(delay_max))
                    st.success('Saved');st.rerun()
                except ValueError as exc:st.error(str(exc))
        st.subheader('AI usage · recent 14 days')
        logs=db.usage(picked,limit=2000)
        if logs:
            import pandas as pd
            from datetime import datetime,timezone
            usage=pd.DataFrame([{'Date':datetime.fromtimestamp(r['recorded_at'],timezone.utc).strftime('%Y-%m-%d'),
                'Input':r['input_tokens'],'Output':r['output_tokens']} for r in logs])
            trend=usage.groupby('Date')[['Input','Output']].sum().sort_index().tail(14)
            st.line_chart(trend)
            st.dataframe(trend.tail(7),width='stretch')
        else:st.caption('No Gemini usage yet.')
    with connection:
        st.subheader('Native WhatsApp Chrome — this worker')
        st.caption('One worker = one normal Chrome window and separate login profile. Pair directly in Chrome; no QR generated in the dashboard. The AI attaches to the already-open browser.')
        if st.button('1. Open native WhatsApp Chrome', key='ai_connect'):
            access.require()
            result=bridge('/open-browser',{'workerId':picked})
            if result.get('error'):st.error(result['error'])
            else:st.info('Opening this worker’s native Chrome window. If already open, use the Windows taskbar to select it.')
        status=bridge('/status',{'workerId':picked})
        state=status.get('state','unknown')
        st.write('Connection:', state)
        if status.get('number'):st.write('Connected WhatsApp number:',status['number'])
        if status.get('error'):st.warning(status['error'])
        if state=='ready':
            st.success('AI is connected. This worker has its own visible Chrome window. You can watch the conversations there.')
        elif state in ('browser_open','browser_only_error'):
            st.info('Scan WhatsApp Web\'s native QR in the Chrome window if required. Once your chats load, click Start AI below. This browser remains open.')
            st.warning('The browser alone does not connect Gemini. Until AI status says ready, automated replies are OFF.')
        elif state=='switching':
            st.info('Connecting AI to the already-open Chrome window, without closing the browser...')
        elif state in ('browser_starting','ai_starting','authenticated'):
            st.info('Please wait while this worker starts. Refresh status to check progress.')
        elif state=='browser_closed':
            st.info('Chrome was closed. Use Open WhatsApp to reopen the saved session.')
        elif state=='disconnected':
            st.info('Click Open WhatsApp to start a worker-specific Chrome window.')
        # These are persistent Python-gateway records, not WhatsApp Web read
        # receipts. No message text, phone number or API key is shown.
        try:
            with db.connect() as _con:
                _in=_con.execute("SELECT COUNT(*) AS n,MAX(created_at) AS latest FROM ai_messages WHERE worker_id=? AND direction='in'",(picked,)).fetchone()
                _lost=_con.execute("SELECT COUNT(*) AS n,MAX(sent_at) AS latest FROM ai_unmatched WHERE worker_id=? AND body NOT LIKE '[Outgoing] %'",(picked,)).fetchone()
                _out=_con.execute("SELECT COUNT(*) AS n FROM ai_messages WHERE worker_id=? AND direction='out' AND status IN ('queued','queued_reply','sending')",(picked,)).fetchone()
                _latest=_con.execute("SELECT t.stage,t.detail FROM ai_reply_trace t WHERE t.worker_id=? ORDER BY t.updated_at DESC LIMIT 1",(picked,)).fetchone()
            st.caption(f"AI gateway intake · Matched inbound: {_in['n']} · Unmatched inbound: {_lost['n']} · Outgoing waiting: {_out['n']}")
            if _latest:
                st.caption('Latest AI reply stage: '+str(_latest['stage'])+' — '+str(_latest['detail']))
            if state=='ready' and _in['n']==0 and _lost['n']==0:
                st.info('No inbound events are recorded by the AI gateway yet. A WhatsApp read receipt does not mean the AI received the event. Send a fresh test message and refresh this page.')
            elif _lost['n']:
                st.warning('Some incoming messages are unmatched; open Why no AI reply? below to check the sender identity and assigned lead.')
        except Exception as _exc:
            st.warning('AI gateway intake diagnostics unavailable: '+type(_exc).__name__)
        if status.get('tabStatus')=='logged_out':
            st.warning('WhatsApp shows its logged-out page. Reopen WhatsApp in this worker Chrome and finish signing in before attaching AI.')
        elif status.get('tabStatus')=='no_whatsapp_tab':
            st.warning('Chrome is running, but it has no WhatsApp Web tab. Open https://web.whatsapp.com inside this worker window.')
        if status.get('attachStage'):
            st.caption('AI attachment stage: '+status['attachStage'])
        can_attach=state in ('browser_open','browser_only_error') and status.get('browserWindowOpen') and status.get('tabStatus')=='whatsapp_tab_found'
        if st.button('2. Signed in — Attach AI without closing Chrome', key='ai_activate',disabled=not can_attach):
            access.require()
            result=bridge('/activate',{'workerId':picked})
            if result.get('error'):st.error(result['error'])
            else:
                st.info('Attachment requested. Refresh status; only READY means the AI is connected.')
                st.rerun()
        if st.button('Refresh WhatsApp status',key='ai_status_refresh'):
            st.rerun()
        x,y=st.columns(2)
        if x.button('Stop worker (keep saved login)',key='ai_stop'):
            access.require();reply=bridge('/stop',{'workerId':picked});st.info(reply.get('error','Stopped'));st.rerun()
        if y.button('Log out WhatsApp number',key='ai_logout'):
            access.require();reply=bridge('/logout',{'workerId':picked})
            if reply.get('error'):st.warning(reply['error'])
            else:st.info('Logged out');st.rerun()
        st.caption('Multiple workers can run simultaneously: each gets an independent Chrome profile, window and local debugging connection. Never pair multiple numbers in one profile.')
        st.warning('WhatsApp Web pairing can succeed even if whatsapp-web.js AI attachment fails. If status is not ready, the browser is manual only. No messages will be sent by AI. This unofficial integration may break or face restrictions.')
        render_reply_diagnostics(db, picked, status)
    with keys:
        st.subheader('Gemini API key pool')
        key_count=len(db.keys(picked))
        st.caption(f'{key_count} encrypted keys saved. Paste one Gemini API key per line to REPLACE the pool; leave blank to keep the saved pool.')
        saved_raw='\n'.join(decrypt_key(app_paths().data,k['encrypted_key']) for k in db.keys(picked,reveal=True))
        with st.form('ai_bulk_keys'):
            raw=st.text_area('Gemini keys · one per line',value=saved_raw,height=155,
                placeholder='AIza...\nAIza...\nAIza...')
            if st.form_submit_button('Save and test keys'):
                entries=list(dict.fromkeys(x.strip() for x in raw.splitlines() if x.strip()))
                if not entries:st.error('Paste at least one key.')
                elif len(entries)>20:st.error('Maximum 20 keys per worker.')
                elif any(len(x)<15 for x in entries):st.error('One or more keys look incomplete.')
                else:
                    from .ai_runtime import test_models
                    checked=[]
                    shared_models=[]
                    with st.spinner('Validating keys and finding working text models...'):
                        for item in entries:
                            try:
                                results=test_models(item, wanted_models=shared_models[:8] if shared_models else None)
                                checked.append((item,results))
                                if not shared_models:
                                    shared_models=[r['model'] for r in results if r.get('working')]
                            except Exception:
                                checked.append((item,[]))
                    if not any(any(m.get('working') for m in results) for _,results in checked):
                        st.error('No working model found; existing keys were kept unchanged.')
                    else:
                        db.replace_keys(picked,[(encrypt_key(app_paths().data,item),item[-4:]) for item,_ in checked])
                        for record,(_,tested) in zip(db.keys(picked),checked):
                            db.tested_models(record['id'],picked,tested)
                        st.success('Saved encrypted keys. Select a working model below.');st.rerun()
        st.caption('Keys are shown in this local-only form for editing. Keep the browser private and avoid screenshots containing keys.')
        if key_count:
            masked=', '.join('••••'+key['key_suffix'] for key in db.keys(picked))
            st.caption('Saved keys: '+masked)
            if st.button('Retest saved keys',key='ai_bulk_retest'):
                with st.spinner('Retesting available Gemini models...'):
                    shared=[]
                    for key in db.keys(picked,reveal=True):
                        try:
                            results=test_models(decrypt_key(app_paths().data,key['encrypted_key']), wanted_models=shared[:8] if shared else None)
                            db.tested_models(key['id'],picked,results)
                            if not shared:shared=[r['model'] for r in results if r.get('working')]
                        except Exception as exc:st.warning('One key unavailable: '+str(exc)[:90])
                st.rerun()
        models=db.working_models(picked)
        if models:
            selected=st.selectbox('Working Gemini model',models,index=models.index(worker['model_name']) if worker['model_name'] in models else 0)
            if st.button('Use selected model',type='primary',key='ai_model_choose'):
                db.select_working_model(picked,selected)
                st.success('Model configured for this worker');st.rerun()
        else:
            st.info('Add and test Gemini keys to populate the model selector.')
        st.caption('When a key fails, the worker can use another tested key compatible with the chosen model. Google API limits may apply to the whole Google Cloud project, not to individual keys.')

