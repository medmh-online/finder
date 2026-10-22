"""On-demand cleaned public website lookup through r.jina.ai, with per-account caching.

No background website scraping. Only invoked by a user's explicit UI action.
Returns a short text extract to keep Gemini's context compact.
"""
from __future__ import annotations
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def allowed_public_url(raw):
    text=str(raw or '').strip()
    if not text or len(text)>1400:
        raise ValueError('No valid public business website URL on this lead')
    if '://' not in text:text='https://'+text
    url=urlsplit(text)
    hostname=(url.hostname or '').lower().rstrip('.')
    if url.scheme not in ('http','https') or not hostname or url.username or url.password:
        raise ValueError('Website must be an HTTP(S) public URL')
    if url.port not in (None,80,443):
        raise ValueError('Nonstandard website ports are not supported')
    if hostname=='localhost' or hostname.endswith(('.localhost','.local','.internal','.test')):
        raise ValueError('Local/private websites are not supported')
    try:
        ip=ipaddress.ip_address(hostname)
        if not ip.is_global:
            raise ValueError('Private network addresses are not supported')
    except ValueError as exc:
        if 'not supported' in str(exc):raise
        if '.' not in hostname or not re.fullmatch(r'[a-z0-9.-]+',hostname):
            raise ValueError('Website hostname is not a valid public domain')
    return urlunsplit((url.scheme,url.netloc,url.path or '/',url.query,''))


def cleaned_public_page(db,raw_url):
    url=allowed_public_url(raw_url)
    cache=db.cached_research(url)
    if cache is not None:
        return cache,True
    request=Request('https://r.jina.ai/'+url,headers={
        'User-Agent':'MedMH-Sales-Research/1.0',
        'Accept':'text/plain',
        'X-Return-Format':'text',
    })
    with urlopen(request,timeout=14) as response:
        # Bound network and prompt size: text only, no PDFs/assets.
        text=response.read(24_000).decode('utf-8',errors='replace')
    clean=re.sub(r'\n{4,}', '\n\n',text).strip()[:4500]
    if not clean:raise ValueError('The website extractor returned no readable content')
    db.save_research(url,clean)
    return clean,False
