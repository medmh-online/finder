"""Country-aware business observations and explainable lead qualification.

This module makes no network requests.  In particular, the Public Suffix List
comes from tldextract's packaged snapshot, never a runtime download.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import math
import re
import unicodedata
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import phonenumbers
import pycountry
import tldextract


_DOMAIN_PARSER = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True,
)

_COUNTRY_ALIASES = {
    "uk": "GB", "u.k.": "GB", "united states of america": "US",
    "usa": "US", "u.s.a.": "US", "south korea": "KR", "north korea": "KP",
    "russia": "RU", "vietnam": "VN", "bolivia": "BO", "venezuela": "VE",
    "iran": "IR", "syria": "SY", "laos": "LA", "tanzania": "TZ",
    "moldova": "MD", "taiwan": "TW", "brunei": "BN", "palestine": "PS",
    "turkey": "TR", "czech republic": "CZ", "ivory coast": "CI",
    "maroc": "MA", "المغرب": "MA", "المملكة المغربية": "MA",
    "algerie": "DZ", "الجزائر": "DZ", "tunisie": "TN", "تونس": "TN",
    "egypte": "EG", "مصر": "EG", "arabie saoudite": "SA",
    "السعودية": "SA", "المملكة العربية السعودية": "SA",
    "emirats arabes unis": "AE", "الإمارات العربية المتحدة": "AE",
    "france": "FR", "فرنسا": "FR", "espagne": "ES", "اسبانيا": "ES",
    "إسبانيا": "ES", "allemagne": "DE", "المانيا": "DE", "ألمانيا": "DE",
    "royaume uni": "GB", "royaume-uni": "GB", "المملكة المتحدة": "GB",
    "etats unis": "US", "etats-unis": "US", "الولايات المتحدة": "US",
}

_HOSTED_DOMAINS = {
    "business.site", "negocio.site", "entreprise.site", "wixsite.com",
    "wordpress.com", "blogspot.com", "weebly.com", "jimdosite.com",
    "canva.site", "myshopify.com", "godaddysites.com", "webnode.com",
    "webnode.page", "site123.me", "notion.site", "sites.google.com",
    "carrd.co", "square.site", "webflow.io", "framer.website",
}
_SOCIAL_DOMAINS = {
    "facebook.com", "fb.com", "fb.me", "instagram.com", "wa.me",
    "whatsapp.com", "youtube.com", "youtu.be", "tiktok.com", "linkedin.com",
    "twitter.com", "x.com", "pinterest.com", "g.page", "maps.app.goo.gl",
}
_TRACKING_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref_src",
}
_GOOGLE_VIEW_KEYS = {"hl", "authuser", "entry", "g_ep", "ved", "source", "sa"}
_WEBSITE_TYPES = {"None", "Free", "Subdomain", "Real", "Social", "Unknown"}
_WEBSITE_STATUSES = {"found", "confirmed_absent", "unknown", "error"}
_DEFAULT_SCORING = {
    "no_website": 45, "social_only": 35, "hosted_website": 15,
    "valid_phone": 20, "public_email": 10, "social_presence": 5,
    "has_reviews": 5, "verified_new_business": 15,
    "hot_threshold": 70, "warm_threshold": 40,
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _truth(value: Any) -> bool:
    """Do not treat the strings 'false' and '0' as positive observations."""
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "verified"}
    return value is True or value == 1


def _country_token(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", _text(value)).casefold()
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


@lru_cache(maxsize=1)
def _country_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for item in pycountry.countries:
        for field in ("alpha_2", "alpha_3", "numeric", "name", "official_name", "common_name"):
            value = getattr(item, field, "")
            if value:
                index[_country_token(value)] = item.alpha_2
    index.update({_country_token(k): v for k, v in _COUNTRY_ALIASES.items()})
    return index


def country_options() -> dict[str, str]:
    """ISO alpha-2 codes mapped to country names, sorted for a country selector."""
    items = [(c.alpha_2, getattr(c, "common_name", c.name)) for c in pycountry.countries]
    return dict(sorted(items, key=lambda item: item[1].casefold()))


def country_info(value: Any) -> dict[str, str]:
    """Resolve exact country names/codes; never guess a country from a city."""
    code = _country_index().get(_country_token(value), "")
    item = pycountry.countries.get(alpha_2=code) if code else None
    if item is None:
        return {"country": "", "country_code": ""}
    return {"country": getattr(item, "common_name", item.name), "country_code": item.alpha_2}


def normalize_phone(raw: Any, country_code: str = "") -> dict[str, Any]:
    """Normalize a valid number; a generated link never verifies WhatsApp.

    National numbers require a recognized region.  Invalid input stays available
    as raw/display text, but cannot acquire an international contact link.
    """
    phone_raw = _text(raw)
    result = {
        "phone_raw": phone_raw, "phone": phone_raw, "phone_e164": "",
        "phone_type": "UNKNOWN", "phone_valid": False, "whatsapp_link": None,
    }
    if not phone_raw or len(phone_raw) > 500:
        return result
    region = country_info(country_code)["country_code"] or None
    try:
        number = phonenumbers.parse(phone_raw, region)
        if not phonenumbers.is_valid_number(number):
            return result
        # Extensions are useful display data, but are not part of E.164/wa.me.
        canonical = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        kind = phonenumbers.number_type(number)
        type_map = {
            phonenumbers.PhoneNumberType.MOBILE: "MOBILE",
            phonenumbers.PhoneNumberType.FIXED_LINE: "FIXE",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "FIXED_OR_MOBILE",
        }
        result.update({
            "phone": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "phone_e164": canonical, "phone_type": type_map.get(kind, "UNKNOWN"),
            "phone_valid": True, "whatsapp_link": "https://wa.me/" + canonical.lstrip("+"),
        })
    except (phonenumbers.NumberParseException, ValueError, TypeError):
        pass
    return result


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _unwrap_google_url(url: str) -> str:
    current = url
    for _ in range(4):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower()
        google = _DOMAIN_PARSER(host).domain == "google" if host else False
        if parsed.path != "/url" or (host and not google):
            break
        params = parse_qs(parsed.query)
        replacement = (params.get("q") or params.get("url") or [""])[0]
        if not replacement or replacement == current:
            break
        current = replacement.strip()
    return current


def _clean_http_url(value: Any) -> str:
    raw = _text(value)
    if not raw or any(ord(c) < 32 for c in raw) or "\\" in raw:
        return ""
    try:
        raw = _unwrap_google_url(raw)
        if raw.startswith("//"):
            raw = "https:" + raw
        elif not re.match(r"^[a-zA-Z][a-zA-Z\d+.-]*:", raw):
            # Accept a human-entered hostname, not arbitrary relative text.
            first = raw.split("/", 1)[0]
            if "." not in first or any(c.isspace() for c in first):
                return ""
            raw = "https://" + raw
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if any(c.isspace() for c in host) or "%" in host:
            return ""
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global or (getattr(ip, "ipv4_mapped", None) and not ip.ipv4_mapped.is_global):
                return ""
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                return ""
            if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split(".")):
                return ""
        port = parsed.port
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        netloc = "[" + host + "]" if ":" in host else host
        if port and port != default_port:
            netloc += ":" + str(port)
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError):
        return ""


def _configured_domains(config: dict[str, Any] | None, key: str, defaults: set[str]) -> set[str]:
    if not isinstance(config, dict) or key not in config:
        return defaults
    supplied = config[key]
    if isinstance(supplied, str):
        supplied = [supplied]
    if not isinstance(supplied, (list, tuple, set)):
        return defaults
    domains: set[str] = set()
    legacy_platform_names = {"webnode": {"webnode.com", "webnode.page"}, "site123": {"site123.me", "site123.com"}}
    for value in supplied:
        raw = _text(value).lower().lstrip("*.")
        if raw in legacy_platform_names and key == "free_site_indicators":
            domains.update(legacy_platform_names[raw])
            continue
        try:
            parsed = urlsplit(raw if "://" in raw else "https://" + raw)
            host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
            if "." in host and re.fullmatch(r"[a-z0-9.-]+", host) and all(
                label and not label.startswith("-") and not label.endswith("-") for label in host.split(".")
            ):
                domains.add(host)
        except (ValueError, UnicodeError):
            continue
    return domains


def classify_website(url: Any, extraction_ok: bool = True, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify an observed URL independently from its price or design quality.

    ``extraction_ok=True`` with an empty URL means the caller successfully
    checked for a website and confirmed absence.  Callers without that evidence
    should use ``prepare_lead``, which defaults missing observations to unknown.
    ``Free`` is retained as the existing schema label for a recognizable hosted
    platform; it does not assert that the site's subscription is free.
    Configured ``fake_domains`` and ``free_site_indicators`` match hostnames
    exactly or at a subdomain boundary; URL text/path/query substrings never do.
    """
    raw = _text(url)
    cleaned = _clean_http_url(raw) if raw else ""
    if not extraction_ok:
        return {"website_url": cleaned or raw or None, "website_type": "Unknown", "website_status": "error"}
    if not raw:
        return {"website_url": None, "website_type": "None", "website_status": "confirmed_absent"}
    if not cleaned:
        return {"website_url": raw, "website_type": "Unknown", "website_status": "error"}
    host = urlsplit(cleaned).hostname or ""
    hosted = [rule for rule in _configured_domains(config, "free_site_indicators", _HOSTED_DOMAINS) if _host_matches(host, rule)]
    social = [rule for rule in _configured_domains(config, "fake_domains", _SOCIAL_DOMAINS) if _host_matches(host, rule)]
    if hosted and max(map(len, hosted)) > max(map(len, social), default=0):
        kind = "Free"
    elif social:
        kind = "Social"
    elif _DOMAIN_PARSER(host).domain == "google" and (
        host.startswith("maps.") or urlsplit(cleaned).path.startswith("/maps")
    ):
        kind = "Social"
    else:
        extracted = _DOMAIN_PARSER(host)
        subdomain = extracted.subdomain
        if subdomain == "www":
            subdomain = ""
        elif subdomain.startswith("www."):
            subdomain = subdomain[4:]
        kind = "Subdomain" if subdomain else "Real"
    return {"website_url": cleaned, "website_type": kind, "website_status": "found"}


def _identity_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def _hash_identity(prefix: str, *parts: str) -> str:
    content = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _google_identifier(value: str) -> str:
    value = unquote(value).strip()
    if re.fullmatch(r"0x[\da-fA-F]+:0x[\da-fA-F]+", value):
        return "google:cid:" + str(int(value.split(":")[1], 16))
    if value.startswith("ChI") and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return "google:place:" + value
    return ""


def canonical_source_key(
    url: str = "", place_id: str = "", name: str = "", address: str = "",
    country_code: str = "", phone_e164: str = "",
) -> str:
    """Create a conservative identity without search niche or mutable tracking.

    Empty means there is insufficient evidence for automatic identity matching.
    Separate branches must not be merged just because they share a phone/name.
    """
    if _text(place_id):
        identifier = _google_identifier(_text(place_id))
        return identifier or "google:place:" + _text(place_id)
    cleaned = _clean_http_url(url)
    if cleaned:
        parsed = urlsplit(cleaned)
        host = parsed.hostname or ""
        is_google = _DOMAIN_PARSER(host).domain == "google"
        params = parse_qs(parsed.query)
        if is_google:
            for key in ("query_place_id", "place_id"):
                if params.get(key):
                    return canonical_source_key(place_id=params[key][0])
            for key in ("ftid", "cid"):
                for value in params.get(key, []):
                    identifier = _google_identifier(value)
                    if identifier:
                        return identifier
                    if key == "cid" and value.isdigit():
                        return "google:cid:" + str(int(value))
            embedded = re.search(r"!1s([^!/?&]+)", unquote(parsed.path))
            if embedded:
                identifier = _google_identifier(embedded.group(1))
                if identifier:
                    return identifier
            q_value = (params.get("q") or [""])[0]
            if q_value.startswith("place_id:"):
                return canonical_source_key(place_id=q_value[len("place_id:"):])
            # A Maps search/feed URL is a query, not a business identity.
            if parsed.path.startswith("/maps/search") or (parsed.path.rstrip("/") == "/maps" and not params.get("cid")):
                cleaned = ""
        if cleaned:
            query = [
                (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
                and not (is_google and key.lower() in _GOOGLE_VIEW_KEYS)
            ]
            stable_path = parsed.path
            if is_google:
                stable_path = re.sub(r"/@[^/]+", "", stable_path)
            stable = urlunsplit((parsed.scheme, parsed.netloc, stable_path or "/", urlencode(sorted(query)), ""))
            return _hash_identity("url:", stable)
    normalized_name = _identity_text(name)
    normalized_address = _identity_text(address)
    region = country_info(country_code)["country_code"]
    if normalized_name and normalized_address and region:
        return _hash_identity("fallback:", normalized_name, normalized_address, region)
    if normalized_name and not normalized_address and region and re.fullmatch(r"\+\d{7,15}", _text(phone_e164)):
        return _hash_identity("phone:", normalized_name, _text(phone_e164), region)
    return ""


def _is_legacy(lead: dict[str, Any]) -> bool:
    return _truth(lead.get("legacy_unverified")) or any(
        _text(lead.get(field)).casefold() == "legacy_unverified"
        for field in ("observation_status", "source_status")
    )


def _scoring_config(config: dict[str, Any] | None) -> dict[str, float]:
    supplied = config.get("scoring", {}) if isinstance(config, dict) else {}
    result: dict[str, float] = {}
    for key, default in _DEFAULT_SCORING.items():
        value = supplied.get(key, default) if isinstance(supplied, dict) else default
        try:
            number = float(value)
            result[key] = max(0.0, min(100.0, number)) if math.isfinite(number) else float(default)
        except (ValueError, TypeError):
            result[key] = float(default)
    result["hot_threshold"] = max(result["hot_threshold"], result["warm_threshold"])
    return result


def _public_emails(lead: dict[str, Any]) -> list[str]:
    audit = lead.get("website_audit") or {}
    if isinstance(audit, str):
        try:
            audit = json.loads(audit)
        except (ValueError, TypeError):
            audit = {}
    values = lead.get("public_emails") or []
    if isinstance(values, str):
        values = [values]
    if _text(lead.get("email")) and (_text(lead.get("email_source_url")) or _text(lead.get("email_checked_at"))):
        values = list(values) if isinstance(values, (list, tuple)) else []
        values.append(_text(lead.get("email")))
    if isinstance(audit, dict) and audit.get("status") in {"ok", "partial"}:
        values = list(values) if isinstance(values, (list, tuple)) else [values]
        values += audit.get("emails") if isinstance(audit.get("emails"), list) else []
    if isinstance(values, str):
        values = [values]
    return [s for s in values if isinstance(s, str) and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", s)] if isinstance(values, (list, tuple)) else []


def prepare_lead(lead: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return normalized observations while preserving all supplied CRM fields.

    Legacy rows retain raw inputs and observation markers.  Empty legacy website
    fields and extraction failures never become confirmed no-website evidence.
    Storage clears stale ``phone_raw`` before passing a newly edited phone.
    """
    result = dict(lead)
    # Reviews and review-age sampling are retired; do not persist new review data.
    for obsolete in ('reviews_count','oldest_review_age_years','review_age_is_sample','review_sample_count'):
        result.pop(obsolete,None)
    original_country = _text(lead.get("country_raw") or lead.get("country"))
    country = country_info(lead.get("country_code"))
    if not country["country_code"]:
        country = country_info(lead.get("country"))
    result.update(country)
    if original_country and (not country["country_code"] or "country_raw" in lead):
        result["country_raw"] = original_country
    phone_raw = lead.get("phone_raw")
    if not _text(phone_raw):
        phone_raw = lead.get("phone") or lead.get("phone_e164")
    result.update(normalize_phone(phone_raw, country["country_code"]))
    raw_url = lead.get("website_url") or lead.get("website") or ""
    status = _text(lead.get("website_status")).lower()
    if status not in _WEBSITE_STATUSES:
        status = "unknown"
    legacy = _is_legacy(lead)
    independent_website = _truth(lead.get("website_verified"))
    extraction_failed = "website_extraction_ok" in lead and not _truth(lead.get("website_extraction_ok"))
    if legacy and not independent_website:
        website = {"website_url": _clean_http_url(raw_url) or _text(raw_url) or None, "website_type": "Unknown", "website_status": "unknown"}
    elif status == "error" or extraction_failed:
        website = classify_website(raw_url, extraction_ok=False, config=config)
    elif _text(raw_url):
        website = classify_website(raw_url, config=config)
        if status == "unknown" and "website_status" in lead:
            website["website_status"] = "unknown"
    elif status == "confirmed_absent" or _truth(lead.get("website_checked")):
        website = classify_website("", extraction_ok=True, config=config)
    else:
        website = {"website_url": None, "website_type": "Unknown", "website_status": "unknown"}
    result.update(website)
    # Keep an imported/raw observation for auditability; do not rewrite notes,
    # CRM status, user-entered follow-up dates, activity or manual priority.
    if _text(raw_url) and not _clean_http_url(raw_url):
        result.setdefault("website_url_raw", _text(raw_url))
    if not _text(result.get("source_key")):
        result["source_key"] = canonical_source_key(
            url=_text(result.get("maps_link") or result.get("source_url")),
            place_id=_text(result.get("place_id")), name=_text(result.get("name")),
            address=_text(result.get("address")), country_code=country["country_code"],
            phone_e164=result["phone_e164"],
        )
    # Deprecated sales-fit/confidence/priority ranking is no longer calculated.
    # Keep legacy columns at their schema defaults for database compatibility.
    result.update(lead_score=0,data_confidence=0,lead_priority='Cold',
                  manual_priority='',score_breakdown={})
    return result
