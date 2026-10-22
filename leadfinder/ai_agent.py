"""Consent-aware, persistent AI conversation worker.

The process is independent of Streamlit sessions and runs only against workers
registered in this local installation. All messaging originates through the
local WhatsApp bridge; no message is generated from a scraped number alone.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from .ai_runtime import Registry,decrypt_key,generate,node_api,app_paths
from .ai_outreach import GlobalContactLedger


def human_history(messages):
    # Label source explicitly; untrusted WhatsApp messages cannot instruct the
    # agent to override rules or reveal keys.
    return [{'role':'user' if m['direction']=='in' else 'model',
             'parts':[{'text':str(m['body'])[:750]}]}
             for m in messages if m['status'] not in ('failed','blocked','uncertain','superseded')]


def parse_ai_decision(result):
    """Structured decision; no WhatsApp send for ignore/handoff."""
    clean=str(result or '').strip()
    if clean.startswith('```'):
        clean=clean.split('\n',1)[-1].rsplit('```',1)[0].strip()
    try:
        value=json.loads(clean)
        if isinstance(value,dict):
            action=str(value.get('action') or 'reply').strip().lower()
            if action not in {'reply','ignore','stop','handoff','complete'}:action='ignore'
            body=str(value.get('reply') or '').strip()
            if bool(value.get('opt_out')):action='stop'
            return action,body
    except (ValueError,TypeError):
        pass
    # Do not accidentally send a JSON-like malformed instruction or an essay.
    if clean.startswith('{') or len(clean)>600:return 'ignore',''
    return 'reply',clean


def decision_facts(result):
    """Extract only structured conversation facts supplied by the model.

    Facts are used as the developer brief, never as proof of an agreement.
    """
    clean=str(result or '').strip()
    if clean.startswith('```'):
        clean=clean.split('\n',1)[-1].rsplit('```',1)[0].strip()
    try:
        payload=json.loads(clean)
    except (ValueError,TypeError):
        return {}
    return payload.get('qualification',{}) if isinstance(payload,dict) and isinstance(payload.get('qualification',{}),dict) else {}


def completion_message(lead,messages,facts=None):
    """Confirm the known scope once, then hand off without a made-up ETA."""
    facts=facts or {}
    service=str(facts.get('service') or '').strip()[:45]
    scope=str(facts.get('requirements') or '').strip()[:70]
    brief=' / '.join(x for x in (service,scope) if x)[:100]
    last=next((m.get('body','') for m in reversed(messages) if m.get('direction')=='in'),'')
    if str(lead.get('country_code') or '').upper()=='MA':
        if any('\u0600'<=x<='\u06ff' for x in last):
            return ('واخا، فهمت: '+brief+'. ' if brief else 'واخا، فهمت. ')+'المطور ديال MedMH غادي يتواصل معاك هنا.'
        return ('Wakha, fhemt: '+brief+'. ' if brief else 'Wakha, fhemt. ')+'Developer dyal MedMH ghadi ytwasel m3ak hna.'
    return ('Got it: '+brief+'. ' if brief else 'Got it. ')+'A MedMH developer will message you here.'


def concise_message(text, max_chars=260):
    """Limit message length without slicing a word; prefer sentence boundary."""
    value=' '.join(str(text or '').split())
    if len(value)<=max_chars:return value
    prefix=value[:max_chars].rstrip()
    last=max(prefix.rfind('. '),prefix.rfind('? '),prefix.rfind('! '))
    if last>=70:return prefix[:last+1]
    return prefix.rsplit(' ',1)[0].rstrip(' ,;:')


# Verified MedMH information is supplied by the local Settings page, not
# inferred from the model's training data or from a customer's chat history.
def portfolio_url(settings):
    value = str(settings.get('portfolio') or '').strip()
    if not value:
        return ''
    if re.fullmatch(r'https?://[^\s<>"\']{3,200}', value, flags=re.I):
        return value
    return ''


def _portfolio_request(text):
    """A narrow backup check for explicit requests for previous work.

    Gemini is still responsible for interpreting each incoming message. This
    deterministic backstop makes a known business fact hard to hallucinate.
    It never generates a reply or sends a message on its own.
    """
    text = ' '.join(str(text or '').casefold().split())
    if not text:
        return False
    if re.search(r'\b(?:portfolio|portofolio|portfolyo|بورتفوليو|أعمالكم|اعمالكم|نماذج أعمال)\b', text):
        return True
    examples = r'(?:examples?|exemples?|amtila|namadij|نماذج|أمثلة|امثلة|exemple?s?)'
    sites = r'(?:sites?|websites?|web|projets?|projects?|sites? web|مواقع|مشاريع)'
    return bool(re.search(examples+r'.{0,100}'+sites+'|'+sites+r'.{0,100}'+examples, text))


def should_share_portfolio(messages, ai_result=''):
    """Check the latest request and optional model intent annotation.

    Also handle a short 'yes, send it' after asking about examples; looking
    only at the latest turn would miss the customer's earlier request.
    """
    if messages:
        last = next((m for m in reversed(messages) if m.get('direction') == 'in'), None)
        if last and _portfolio_request(last.get('body', '')):
            return True
        if last:
            short = ' '.join(str(last.get('body') or '').casefold().split())
            agrees = bool(re.fullmatch(r'(?:oui|yes|ah|iwa|wakha|ok|safi|3tini|siftli|sift|send|envoie|عطيني|صيفطلي)[\w\s,!.?]{0,35}', short))
            if agrees:
                previous = [m for m in messages if m.get('direction') == 'in' and m is not last]
                if any(_portfolio_request(m.get('body', '')) for m in previous[-3:]):
                    return True
    try:
        candidate = str(ai_result or '').strip()
        if candidate.startswith('```'):
            candidate = candidate.split('\n',1)[-1].rsplit('```',1)[0].strip()
        obj = json.loads(candidate)
        return isinstance(obj,dict) and obj.get('share_portfolio') is True
    except (ValueError, TypeError):
        return False


def apply_verified_portfolio_answer(action, answer, messages, raw_result, settings, lead):
    """Ground model answers to requests for work examples in saved facts.

    Never invent completed projects, future sends, or promises to follow up.
    """
    url = portfolio_url(settings)
    if action != 'reply' or not url or not should_share_portfolio(messages, raw_result):
        return action, answer
    if str(lead.get('country_code') or '').upper() == 'MA':
        return action, f'Ah, hadi portfolio dyal MedMH: {url}'
    return action, f'Here is the MedMH portfolio: {url}'



def customer_project_request(messages):
    """Recognize only an explicit project request in the CUSTOMER's words.

    Used as a conservative backstop when Gemini promises developer contact
    but incorrectly labels its output as an ordinary reply. It is not a
    substitute for Gemini understanding the rest of the conversation.
    """
    services = (
        ('landing page', r'\blanding[\s-]*page\b'),
        ('QR menu', r'\b(?:qr[\s-]*menu|menu[\s-]*qr|menu num[eé]rique|menu digital)\b'),
        ('website', r'\b(?:site[\s-]*web|site vitrine|website|web[\s-]*site|site internet)\b'),
        ('booking system', r'\b(?:r[eé]servation|reservation|booking)[\s-]*\b'),
        ('online store', r'\b(?:boutique en ligne|e[ -]?commerce|online (?:store|shop))\b'),
        ('automation', r'\b(?:automation|automatisation|automations)\b'),
        ('AI assistant', r'\b(?:assistant[\s-]*(?:ai|ia)|chatbot|agent[\s-]*ia)\b'),
    )
    intent = re.compile(r'\b(?:bghit|bghina|bghyt|baghi|kanbghi|n7taj|je veux|je voudrais|i want|i need|we need|i would like|i\s*\x27d like)\b', re.I)
    for msg in reversed(messages):
        if msg.get('direction') != 'in':
            continue
        raw = ' '.join(str(msg.get('body') or '').split())[:180]
        if not intent.search(raw):
            continue
        for service, pattern in services:
            if re.search(pattern, raw, flags=re.I):
                return {'service':service, 'requirements':raw[:140]}
    return {}


def promises_developer_contact(answer):
    """Detect a definite promise of developer contact, NOT a price caveat.

    Gemini sometimes writes a developer handoff in the reply text while
    returning action=reply. This guard makes that promise a real CRM handoff
    only when an explicit customer project request exists.
    """
    value=' '.join(str(answer or '').casefold().split())
    developer = r'(?:d[eé]veloppeur|developer|dev\b)'
    contact = r'(?:contact(?:era|er|ing|s|ed)?|(?:ghadi\s+)?(?:y|n)?t?wasel|nwaslok|ywasel|reach(?:\s+out)?|message(?:ra|s)?|text(?:\s+you)?)'
    # Phrases like 'the developer confirms the final quote' do NOT qualify.
    return bool(re.search(developer+r'.{0,85}'+contact+'|'+contact+r'.{0,85}'+developer, value, flags=re.I))



def gemini_with_fallback(db, worker, base_data, prompt, dialogue, *, purpose, lead_id=None, max_tokens=180):
    """One permitted attempt per compatible key, no retry loop on one broken key.

    Google limits are commonly shared by Cloud project; changing keys does not
    guarantee more quota. The daily local worker budget applies to the whole pool.
    """
    if not db.token_budget_available(worker['id'], reserve=300):
        raise RuntimeError('Daily worker token budget exhausted')
    model=worker.get('model_name')
    keys=[k for k in db.keys(worker['id'],reveal=True)
          if any(r.get('working') and r.get('model')==model for r in k['test_results'])]
    keys.sort(key=lambda k:k['id']!=worker.get('model_key_id'))
    if not keys:raise RuntimeError('No tested Gemini API key supports the selected model')
    last_exc=None
    for idx,key in enumerate(keys):
        try:
            response,usage=generate(decrypt_key(base_data,key['encrypted_key']),model,prompt,dialogue,max_tokens=max_tokens)
            db.log_usage(worker['id'],key['id'],model,purpose,
                usage.get('promptTokenCount',0),usage.get('candidatesTokenCount',0),lead_id=lead_id)
            if key['id']!=worker.get('model_key_id'):
                db.configure_worker(worker['id'],model_key_id=key['id'])
            return response,usage
        except Exception as exc:
            last_exc=exc
            db.log_usage(worker['id'],key['id'],model,purpose,error=str(exc),lead_id=lead_id)
            message=str(exc).lower()
            # A rejected API key may be replaced; invalid prompts or transport
            # outages are not fixed by cycling every credential.
            credential_failure=any(x in message for x in ('http 401','http 403','api key','invalid key','permission_denied'))
            rate_limited=('http 429' in message or 'resource_exhausted' in message)
            if not credential_failure and not rate_limited:
                break
            # A quota is often tied to a Google Cloud project, not to a key:
            # try at most one configured alternate, then wait for reset.
            if rate_limited and idx>=1:
                break
    raise RuntimeError('Gemini unavailable for this worker: '+str(last_exc)[:180])

def answer_one(db,thread,base_data):
    worker=db.worker(thread['worker_id'])
    if not worker or not worker['enabled']:
        return
    settings=db.account_settings()
    if db.daily_used(thread['worker_id'])>=settings['daily_ai_limit'] or not db.token_budget_available(thread['worker_id']):
        return
    if not worker['model_name'] or not db.working_models(worker['id']):return
    history,messages=db.context(thread['lead_id'])
    lead=db.lead_label(thread['lead_id'])
    # Summarize older material in rolling segments, preserving factual history
    # and preserving ALL raw messages in SQLite.
    if len(messages)>=12 and (messages[0]['id']-history.get('summarized_through',0))>10:
        prior=db.older_messages(thread['lead_id'],history.get('summarized_through',0),messages[0]['id'])
        if prior:
            try:
                prompt=('Compress verified conversation facts under 500 characters: requirements, budget, objections, promises and important context. No guesses.\n'
                        +'Earlier: '+history.get('summary','')[:600]+'\nMessages: '+json.dumps(prior,ensure_ascii=False)[:3500])
                memory,usage=gemini_with_fallback(db,worker,base_data,
                        'You maintain accurate sales conversation memory, not sales responses.',
                        [{'role':'user','parts':[{'text':prompt}]}],max_tokens=180,purpose='memory',lead_id=thread['lead_id'])
                db.write_memory(thread['lead_id'],memory,{},prior[-1]['id'])
                history['summary']=memory
            except Exception as exc:
                print('[memory]',str(exc)[:160])
                # Fall back to a longer tail if the summarizer failed.
                messages=db.messages(thread['lead_id'],limit=45)
    # A worker qualifies the opportunity; it does not design the full project
    # over WhatsApp. Short structured decisions save tokens and prevent loops.
    profile={k:lead.get(k) for k in ('name','niche','city') if lead.get(k)}
    ma = str(lead.get('country_code') or '').upper() == 'MA'
    try: prior_facts=json.loads(history.get('qualification_facts') or '{}')
    except (ValueError,TypeError): prior_facts={}
    questions=int(history.get('qualification_questions') or 0)
    instructions=(
      f"MedMH AI sales assistant for {settings['business_name']}. "
      "Natural human-sounding WhatsApp, but be honest that you are AI when asked. "
      "Reply in ONE short sentence, at most 170 characters. No repeated introductions or sales pitch. "
      + ("Morocco: authentic everyday Darija mixed with French; match user's Latin 3/7/9 or Arabic script. " if ma else "Match their language. ")
      + "Answer directly, never ignore their actual question. "
      "Your job: understand the requested service + at most 1-2 essential requirements, then hand to developer. "
      "Don't interview them about design, hosting, technology, or every feature. Budget and deadline optional. "
      "If customer clearly wants MedMH to BUILD a project and you know service and rough scope: action complete NOW. "
      "Do not confuse someone asking for a restaurant food menu with commissioning a QR-menu website. "
      "At most 2 brief clarification questions in the WHOLE sales conversation; after that complete with known facts if genuinely interested. "
      "CRITICAL: NEVER promise a developer will contact them using action=reply. "
      "If you mention developer follow-up, return action=complete and customer-stated qualification facts. "
      "If a human is requested: action handoff. If stop: action stop; acknowledge once. "
      "Never ignore a first direct greeting (hi, hello, salam) or a genuine question. Reply briefly to welcome a first-time contact. "
      "Ignore only later standalone acknowledgments (ok, thanks) when no new question was asked. Never promise an exact response time. "
      "Return ONLY JSON: {\"action\":\"reply|ignore|stop|handoff|complete\",\"reply\":\"short text\",\"qualification\":{\"service\":\"\",\"requirements\":\"\",\"budget\":\"\",\"deadline\":\"\"},\"share_portfolio\":false}. "
      "Qualification facts must be said by customer; never guess. "
      f"Questions already asked: {questions}/2. Known facts: {json.dumps(prior_facts,ensure_ascii=False)[:280]}. "
      f"Services: {settings['services'][:155]}. Approved price guide: {settings['pricing'][:500] or 'NONE; do not quote numbers'}. "
      f"Portfolio: {portfolio_url(settings) or 'not configured'}. Share on request, not unprompted. "
      f"Sales rules: {settings['instructions'][:190]}. Lead: {json.dumps(profile,ensure_ascii=False)[:120]}. "
      f"History: {history.get('summary','')[:260]}. "
      f"No proactive outreach: {bool(thread.get('do_not_contact') or thread.get('opted_out_at'))}."
    )

    if not messages:return
    try:
        result,usage=gemini_with_fallback(db,worker,base_data,
            instructions,human_history(messages),max_tokens=220,purpose='reply',lead_id=thread['lead_id'])
        action,answer=parse_ai_decision(result)
        facts=decision_facts(result)
        action,answer=apply_verified_portfolio_answer(action,answer,messages,result,settings,lead)
        # A model can say "developer will contact you" but return action=reply.
        # Convert a genuine promise to an actual CRM handoff, if the customer
        # explicitly requested a project. Without explicit intent, NEVER infer
        # a sale or invent customer requirements.
        customer_request=customer_project_request(messages)
        if action == 'reply' and customer_request and promises_developer_contact(answer):
            action='complete'
            facts={**customer_request,**{k:v for k,v in facts.items() if v}}
        elif action == 'complete' and not (facts.get('service') or prior_facts.get('service')) and customer_request:
            facts={**customer_request,**{k:v for k,v in facts.items() if v}}
        if action == 'complete' and not (facts.get('service') or prior_facts.get('service')):
            # Never mark a greeting or casual inquiry as a sales-qualified lead.
            action='reply' if answer else 'ignore'
        if action == 'reply' and questions >= 2 and ('?' in answer or '؟' in answer):
            # Hard backstop: don't keep interviewing people after two questions.
            # If the request is known, send the developer handoff instead.
            if facts.get('service') or prior_facts.get('service'):
                action='complete'
            else:
                answer=answer.split('?')[0].split('؟')[0].strip()
                if not answer:action='ignore'
        if action in ('handoff','complete'):
            # Finish the qualification in one DB transaction and queue one
            # actual confirmation before pausing AI for this lead.
            summary_facts={**prior_facts,**{k:v for k,v in facts.items() if v}}
            db.complete_qualification(thread,completion_message(lead,messages,summary_facts),summary_facts,
                                      reason='requested_human' if action=='handoff' else 'qualified')
            return
        if action=='stop':
            db.opt_out(thread['lead_id'])
            phone=db.contact_phone(thread['lead_id'])
            if phone:GlobalContactLedger(base_data).block(phone)
            # One acknowledgment is enough. Previously blocked means stay silent.
            if thread.get('opted_out_at') or thread.get('do_not_contact'):
                answer=''
            elif not answer:
                answer='Wakha, sm7 lia. Ma ghadich n3awd nsift lik.'
        if action=='ignore' or not answer:
            db.acknowledge_inbound(thread)
        else:
            db.commit_reply(thread,concise_message(answer,max_chars=180),facts)

    except Exception as exc:
        print('[reply]',str(exc)[:180])


def send_pending(db,ledger):
    # Serial sending, global deduplication and per-worker daily new-contact budget.
    attempted_workers=set()
    for m in db.pending_outbound():
        worker_id=m['worker_id']
        if m.get('kind')=='followup':
            db.status(m['id'],'superseded')
            continue
        if worker_id in attempted_workers:
            continue
        if m['kind'] in ('intro','followup','queue') and db.is_lead_paused(m['lead_id']):
            continue
        if m['status']=='queued_reply' and (not m['worker_enabled'] or db.is_lead_paused(m['lead_id'])):
            continue
        is_reply = m['status']=='queued_reply'
        if m['assigned_worker']!=worker_id:
            db.status(m['id'],'blocked');continue
        if m['kind']=='queue':
            current=db.lead_ai_state(m['lead_id'])
            if not m['worker_enabled'] or not current or current['paused'] or current['handoff']:
                db.status(m['id'],'blocked');continue
        # Explicit inbound replies are allowed even after an opt-out.
        # This DOES NOT restore permission for marketing or follow-ups.
        if is_reply:
            permitted=bool(m['last_inbound'] and time.time()-m['last_inbound']<=86400)
        else:
            destination=db.contact_phone(m['lead_id'])
            if (m['do_not_contact'] or m['opted_out_at'] or
                (destination and ledger.is_blocked(destination))):
                db.status(m['id'],'blocked');continue
            permitted=bool(m['opted_in'])
        if not permitted:
            db.status(m['id'],'blocked');continue
        is_first=False if is_reply else db.is_first_contact(m['id'],m['lead_id'])
        phone=db.contact_phone(m['lead_id']) if is_first else None
        if is_first and not phone:
            db.status(m['id'],'blocked');continue
        try:
            live=node_api('/status',{'workerId':worker_id},timeout=7)
            if live.get('state')!='ready':continue
        except Exception:
            continue
        if not db.acquire_send(worker_id,15):continue
        attempted_workers.add(worker_id)
        claimed=False
        budgeted=False
        if is_first:
            # Claim is atomic across all local workers.
            if not ledger.claim(phone,worker_id,m['lead_id']):
                db.status(m['id'],'blocked')
                continue
            claimed=True
            if not db.mark_first_contact_attempt(worker_id,m['lead_id'],m['id'],m['daily_new_contacts']):
                ledger.release(phone,worker_id,m['lead_id'])
                # Leave queued for next UTC day rather than blocking it forever.
                continue
            budgeted=True
        if not db.begin_sending(m['id']):
            if claimed: ledger.release(phone,worker_id,m['lead_id'])
            if budgeted: db.release_first_contact_attempt(m['id'])
            continue
        try:
            timing=db.worker(worker_id) or {}
            response=node_api('/send',{'workerId':worker_id,'chatId':m['chat_id'],'text':m['body'],
                'reply':bool(is_reply),
                'delayMin':int(timing.get('reply_delay_min') or 0),
                'delayMax':int(timing.get('reply_delay_max') or 0)},timeout=125)
            result='sent' if response.get('sent') else 'uncertain'
            db.status(m['id'],result)
            if claimed: ledger.settle(phone,worker_id,m['lead_id'],result)
        except RuntimeError as exc:
            if 'HTTP 409' in str(exc):
                db.requeue_sending(m['id'],m['status'])
                if claimed:ledger.release(phone,worker_id,m['lead_id'])
                if budgeted:db.release_first_contact_attempt(m['id'])
            elif 'HTTP 400' in str(exc):
                db.status(m['id'],'blocked')
                if claimed:ledger.release(phone,worker_id,m['lead_id'])
                if budgeted:db.release_first_contact_attempt(m['id'])
            else:
                db.status(m['id'],'uncertain')
                if claimed:ledger.settle(phone,worker_id,m['lead_id'],'uncertain')
        except Exception:
            db.status(m['id'],'uncertain')
            if claimed:ledger.settle(phone,worker_id,m['lead_id'],'uncertain')


def introduce_one(db,task,base_data):
    # Generate a brief first message only for leads with documented consent.
    worker=db.worker(task['worker_id'])
    if not worker or not worker['enabled'] or not worker['outreach_enabled']:
        return
    settings=db.account_settings()
    if db.daily_used(task['worker_id'])>=settings['daily_ai_limit'] or not db.token_budget_available(task['worker_id']):
        return
    keys={k['id']:k for k in db.keys(task['worker_id'],reveal=True)}
    key=keys.get(worker['model_key_id'])
    if not key or not any(r.get('working') and r.get('model')==worker['model_name'] for r in key['test_results']):
        return
    prompt=(
        f"Short MedMH AI introduction to a prospect who already opted into WhatsApp contact. "
        "Natural, respectful, no promotional paragraph. Keep under 210 characters, one sentence if possible. "
        "Use simple Darija mixed with common French if the business is Moroccan; otherwise match language. "
        "Say MedMH clearly once, briefly explain relevant service, ask at most one question. "
        "Don't invent friendship, previous discussions, prices or facts. "
        f"Services: {settings['services'][:200]}. "
        f"Lead: {json.dumps({'name':task['name'],'industry':task['niche'],'city':task['city']},ensure_ascii=False)[:230]}. "
        "Return only the WhatsApp text."
    )
    try:
        answer,usage=gemini_with_fallback(db,worker,base_data,
            prompt,[{'role':'user','parts':[{'text':'Write a concise opt-in introductory message.'}]}],
            max_tokens=105,purpose='intro',lead_id=task['lead_id'])
        if not answer.strip():raise ValueError('Gemini produced no introductory message')
        db.commit_intro(task,concise_message(answer.strip(),240))
    except Exception as exc:
        print('[intro]',str(exc)[:160])
        db.fail_intro(task['lead_id'])



def prepare_draft(db, worker_id, lead_id, base_data, cleaned_website=''):
    """Generate a reviewed draft, not an outbound WhatsApp message.

    This deliberately never queues / sends a first contact. An assigned lead
    needs no stored consent to be researched and receive a local draft.
    """
    worker=db.worker(worker_id)
    if not worker:raise ValueError('Worker not found')
    assigned=db.assigned_leads().get(int(lead_id))
    if assigned!=worker_id:raise ValueError('Assign this lead to this worker first')
    lead=db.lead_label(lead_id)
    if not lead:raise ValueError('Lead not found')
    if not db.can_message(lead_id,False):raise ValueError('This lead is archived or marked do not contact')
    if not worker['model_key_id'] or not worker['model_name']:
        raise ValueError('Select a tested Gemini model for this worker')
    settings=db.account_settings()
    if db.daily_used(worker_id)>=settings['daily_ai_limit']:
        raise ValueError('Worker AI request budget exhausted')
    if not db.working_models(worker_id):raise ValueError('No tested model available')
    prompt=(
        "Draft only, never send. MedMH AI assistant preparing one friendly WhatsApp introduction, "
        "max 210 characters. Natural, non-pushy, no fake familiarity or claims. "
        "Use the available business facts; for Moroccan leads prefer everyday Darija + a few French words. "
        f"Services: {settings['services'][:200]}. Lead: {json.dumps(lead,ensure_ascii=False)[:330]}. "
        f"Optional public details: {str(cleaned_website)[:350]}. "
        "One relevant low-pressure question maximum, no price promises, output only message."
    )
    answer,usage=gemini_with_fallback(db,worker,base_data,
        prompt,[{'role':'user','parts':[{'text':'Write the brief introduction draft only.'}]}],
        max_tokens=110,purpose='intro_draft',lead_id=lead_id)
    result=concise_message(answer.strip(),240)
    if not result:raise ValueError('Gemini returned an empty draft')
    db.save_draft(lead_id,worker_id,result)
    return result

executor=ThreadPoolExecutor(max_workers=6,thread_name_prefix='medmh-ai')


def process_answer(db,thread,data):
    try:
        answer_one(db,thread,data)
    except Exception as exc:
        print('[reply]',type(exc).__name__,str(exc)[:150],flush=True)
    finally:
        db.release(thread['lead_id'])
        db.release_generation(thread['worker_id'])


def process_intro(db,task,base_data):
    try:
        introduce_one(db,task,base_data)
    except Exception as exc:
        print('[intro]',type(exc).__name__,str(exc)[:150],flush=True)
        db.fail_intro(task['lead_id'])
    finally:
        db.release_generation(task['worker_id'])



def auto_assign_verified(db, ledger):
    """Low-rate WhatsApp availability check and least-loaded worker assignment.

    Registration is not consent. If offline/uncertain, retain unknown status.
    This only checks availability; it NEVER sends a message.
    """
    if not db.auto_assignment_enabled():return
    ready=[]
    for worker in db.workers():
        if not worker['enabled']:continue
        try:
            if node_api('/status',{'workerId':worker['id']},timeout=3).get('state')=='ready':ready.append(worker)
        except Exception:continue
    if not ready:return
    ready.sort(key=lambda w:(w['lead_count'],w['created_at']))
    candidates=db.auto_candidate(interval=60,max_daily=20)
    if not candidates:return
    import phonenumbers
    from phonenumbers import PhoneNumberType
    from .ai_outreach import normalize_phone
    for lead in candidates:
        raw=lead.get('phone') or lead.get('phone_e164') or ''
        phone=normalize_phone(raw,lead.get('country_code') or '')
        if not phone:
            db.record_whatsapp_check(lead['id'],'','unknown',detail='Invalid or ambiguous international phone number')
            continue
        if ledger.is_taken(phone):
            db.record_whatsapp_check(lead['id'],phone,'duplicate',detail='Previously contacted or blocked')
            continue
        try:
            parsed=phonenumbers.parse(phone,None)
            # Landlines (e.g. Morocco 05) are not assumed to have WhatsApp.
            if phonenumbers.number_type(parsed)==PhoneNumberType.FIXED_LINE:
                db.record_whatsapp_check(lead['id'],phone,'not_mobile',detail='Fixed-line phone; not verified on WhatsApp')
                continue
        except Exception:
            db.record_whatsapp_check(lead['id'],phone,'unknown',detail='Phone type could not be determined')
            continue
        worker=ready[0]
        try:
            result=node_api('/verify-number',{'workerId':worker['id'],'number':phone},timeout=13)
            if result.get('registered') is True:
                assigned=db.auto_assign_registered(lead['id'],worker['id'],phone)
                db.record_whatsapp_check(lead['id'],phone,'registered' if assigned else 'duplicate',worker['id'])
            elif result.get('registered') is False:
                db.record_whatsapp_check(lead['id'],phone,'not_registered',worker['id'])
            else:
                db.record_whatsapp_check(lead['id'],phone,'unknown',worker['id'], 'WhatsApp returned no definite answer')
        except Exception as exc:
            db.record_whatsapp_check(lead['id'],phone,'unknown',worker['id'],str(exc)[:130])
        # One remote lookup per cycle, no parallel WhatsApp enumeration.
        break

def process_registry(registry):
    import sqlite3
    with sqlite3.connect(registry.path) as c:
        rows=c.execute('SELECT worker_id FROM worker_registry').fetchall()
    seen=set()
    if not hasattr(process_registry,'ledger'):
        process_registry.ledger=GlobalContactLedger(registry.base)
        process_registry.backfilled=set()
    ledger=process_registry.ledger
    for (worker_id,) in rows:
        db=None
        try:
            db,account=registry.resolve(worker_id)
            if db.path in seen:
                db=None
                continue
            seen.add(db.path)
            from .message_queue import init_queue
            init_queue(db)
            if not hasattr(process_registry,'last_optout_sync'):
                process_registry.last_optout_sync={}
            if time.time()-process_registry.last_optout_sync.get(db.path,0)>=15:
                for phone in db.opted_out_phones():ledger.block(phone)
                process_registry.last_optout_sync[db.path]=time.time()
            if db.path not in process_registry.backfilled:
                for phone,wid,lid in db.historical_outbound():
                    if phone: ledger.observe_sent(phone,wid,lid)
                process_registry.backfilled.add(db.path)
            # Automatic WhatsApp verification/assignment is disabled; only verified incoming identity matching stays active.
            # Follow-up scheduling was removed from this simplified edition.
            send_pending(db,ledger)
            thread=db.due()
            if thread:
                executor.submit(process_answer,db,thread,registry.base)
            send_pending(db,ledger)
        except Exception as exc:
            print(f'[agent] {worker_id[:8]}: {type(exc).__name__}: {exc}',flush=True)
