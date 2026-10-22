"""Incremental, interruptible Google Maps collection.

The browser is only opened by ``GoogleMapsScraper.__enter__``. Importing this
module never starts a browser, contacts a service, or imports Streamlit.
"""

from __future__ import annotations

import datetime as dt
import re
import time
import unicodedata
from urllib.parse import parse_qs, quote, unquote, urlsplit

class ScraperError(RuntimeError):
    """Collection could not establish a trustworthy observation."""


class TransientScrapeError(ScraperError):
    """A bounded retry may recover a navigation or extraction failure."""


class DetailIdentityError(TransientScrapeError):
    """The opened detail panel could not be matched to its candidate."""


class AccessBlockedError(ScraperError):
    """An access challenge requires collection to stop, without bypassing it."""


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def build_search_query(query):
    """Keep country in the actual search, not just in the saved record."""
    city = str(query.get("city") or "").strip()
    niche = str(query.get("niche") or "").strip()
    country = str(query.get("country") or "").strip()
    if not country and query.get("country_code"):
        from .quality import country_info
        country = country_info(query["country_code"]).get("country") or ""
    if not city or not niche or not country:
        raise ValueError("Each search requires a location, niche, and country.")
    return f"{niche} in {city}, {country}"


def source_place_id(url):
    """Read the source identity already present in a public Maps URL."""
    parsed = urlsplit(str(url or ""))
    params = parse_qs(parsed.query)
    for key in ("query_place_id", "place_id", "ftid"):
        if params.get(key):
            return params[key][0]
    decoded = unquote(str(url or ""))
    match = re.search(r"!1s([^!/?&#]+)", decoded)
    return match.group(1) if match else ""


def candidate_from_link(url, preview_name=""):
    from .quality import canonical_source_key
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {"google.com", "www.google.com", "maps.google.com"}:
        return None
    if not parsed.path.startswith("/maps"):
        return None
    key = canonical_source_key(url=url, place_id=source_place_id(url))
    if "/place/" not in parsed.path and not key.startswith("google:"):
        return None
    return {
        "url": url,
        "preview_name": str(preview_name or "").strip(),
        "source_key": key,
    }


def _normalized_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", text.casefold()))


def detail_identity_matches(candidate, current_url, name):
    from .quality import canonical_source_key
    if not str(name or "").strip():
        return False
    expected_id = canonical_source_key(url=candidate.get("url", ""))
    actual_id = canonical_source_key(url=current_url)
    comparable_ids = expected_id.startswith("google:") and actual_id.startswith("google:") and expected_id.rsplit(":", 1)[0] == actual_id.rsplit(":", 1)[0]
    if comparable_ids and expected_id != actual_id:
        return False
    preview = _normalized_name(candidate.get("preview_name"))
    if preview:
        return preview == _normalized_name(name)
    if comparable_ids:
        return expected_id == actual_id
    expected = canonical_source_key(url=candidate.get("url", ""))
    return bool(expected) and expected == canonical_source_key(url=current_url)


def parse_rating(text):
    match = re.search(r"(?<!\d)([0-5](?:[.,]\d+)?)(?!\d)", str(text or ""))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if 0 <= value <= 5 else None


def observation_from_snapshot(snapshot, candidate, query):
    """Convert a verified panel snapshot without equating errors with absence."""
    from .quality import canonical_source_key, classify_website, country_info
    info = country_info(query.get("country_code") or query.get("country") or "")
    website_url = snapshot.get("website_url") or None
    extraction_ok = bool(snapshot.get("panel_complete")) and not snapshot.get("website_error")
    website = classify_website(website_url, extraction_ok=extraction_ok)
    if snapshot.get("website_error"):
        website.update(website_status="error", website_type="Unknown")
    elif not extraction_ok:
        website.update(website_status="unknown", website_type="Unknown")
    elif website_url:
        # Invalid/unsupported links remain extraction errors after classification.
        website["website_status"] = website.get("website_status", "error")
    else:
        website.update(website_status="confirmed_absent", website_type="None")
    url = candidate.get("url") or snapshot.get("url") or ""
    observation = {
        "name": str(snapshot.get("name") or "").strip(),
        "city": str(query.get("city") or "").strip(),
        "country": info.get("country") or str(query.get("country") or ""),
        "country_code": info.get("country_code") or str(query.get("country_code") or ""),
        "niche": str(query.get("niche") or "").strip(),
        "address": str(snapshot.get("address") or "").strip(),
        "phone_raw": str(snapshot.get("phone_raw") or "").strip(),
        "rating": parse_rating(snapshot.get("rating_text")),
        "category": str(snapshot.get("category") or "").strip(),
        "business_status": str(snapshot.get("business_status") or "").strip(),
        "maps_link": url,
        "source_url": url,
        "place_id": source_place_id(url),
        "source_status": "observed",
        "observation_status": "verified_detail",
        "observed_at": utc_now(),
        "website_checked": extraction_ok,
        "website_extraction_ok": extraction_ok,
        "fb_link": snapshot.get("fb_link") or None,
        "instagram_link": snapshot.get("instagram_link") or None,
        "has_facebook": bool(snapshot.get("fb_link")),
        "has_instagram": bool(snapshot.get("instagram_link")),
        **website,
    }
    observation["source_key"] = canonical_source_key(
        url=url, place_id=observation["place_id"], name=observation["name"],
        address=observation["address"], country_code=observation["country_code"],
    )
    return observation


class GoogleMapsScraper:
    """A search page discovers URLs; a separate detail page reads each place.

    ``pulse`` is called during waits and between browser operations so the
    worker can enforce pause/cancel, lease ownership, and time budgets.
    """

    def __init__(self, settings=None, pulse=None, log=None):
        self.settings = dict(settings or {})
        self.pulse = pulse or (lambda: None)
        self.log = log or (lambda level, message: None)
        self.timeout = max(2.0, float(self.settings.get("timeout_seconds", 30)))
        self.delay = max(0.0, float(self.settings.get("delay_seconds", 2)))
        self._playwright = self.browser = self.context = None
        self.search_page = self.detail_page = None
        self.last_discovery = {}

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
            self.pulse()
            self._playwright = sync_playwright().start()
            self.browser = self._playwright.chromium.launch(
                headless=bool(self.settings.get("headless", True)),
            )
            self.context = self.browser.new_context(locale="en-US")
            self.context.set_default_timeout(self.timeout * 1000)
            self.search_page = self.context.new_page()
            self.detail_page = self.context.new_page()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def close(self):
        for attribute in ("context", "browser", "_playwright"):
            resource = getattr(self, attribute, None)
            if resource is not None:
                try:
                    resource.stop() if attribute == "_playwright" else resource.close()
                except Exception:
                    pass
                setattr(self, attribute, None)

    def _wait(self, seconds):
        deadline = time.monotonic() + max(0, seconds)
        while True:
            self.pulse()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    def _check_access(self, page):
        self.pulse()
        try:
            url = str(page.url or "")
            text = page.locator("body").inner_text(timeout=min(2000, self.timeout * 1000))[:25000].casefold()
        except Exception as exc:
            raise TransientScrapeError(f"Could not read page state: {exc}") from exc
        blocked = (
            "/sorry/" in url
            or "our systems have detected unusual traffic" in text
            or "verify you are human" in text
            or "please complete the captcha" in text
            or "automated queries" in text
        )
        if blocked:
            raise AccessBlockedError("Google returned an access challenge. Collection stopped; no bypass was attempted.")
        return text

    def _dismiss_consent(self, page):
        for name in ("Reject all", "Accept all", "Tout refuser", "Tout accepter", "I agree"):
            self.pulse()
            button = page.get_by_role("button", name=name, exact=True)
            if button.count() and button.first.is_visible():
                try:
                    button.first.click(timeout=min(5000, self.timeout * 1000))
                    self._wait(0.4)
                except Exception as exc:
                    raise TransientScrapeError(f"Consent dialog could not be dismissed: {exc}") from exc
                return

    def _goto(self, page, url):
        self.pulse()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        except Exception as exc:
            raise TransientScrapeError(f"Navigation failed: {exc}") from exc
        self.pulse()
        if response is not None and response.status in (401, 403, 429):
            raise AccessBlockedError(f"Google returned HTTP {response.status}; collection stopped.")
        if response is not None and response.status >= 500:
            raise TransientScrapeError(f"Google returned HTTP {response.status}.")
        self._dismiss_consent(page)
        self._check_access(page)

    def _open_search(self, query):
        search = build_search_query(query)
        self.log("info", f"Search: {search}")
        self._goto(self.search_page, f"https://www.google.com/maps/search/{quote(search, safe='')}/?hl=en")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            text = self._check_access(self.search_page)
            if self.search_page.locator('[role="feed"]').count():
                return
            if self.search_page.locator("h1.DUwDvf").count():
                return  # Maps sometimes opens a single matching business.
            if "no results found" in text or "couldn't find" in text or "can't find" in text:
                return
            self._wait(0.25)
        raise TransientScrapeError("Search did not load a results feed, a place, or an explicit no-results state.")

    def _read_candidate_links(self):
        self._check_access(self.search_page)
        feed = self.search_page.locator('[role="feed"]').first
        if feed.count():
            try:
                return feed.locator('a[href*="/maps/place/"]').evaluate_all(
                    "nodes => nodes.map(a => ({url: a.href, preview_name: a.getAttribute('aria-label') || a.textContent || ''}))"
                )
            except Exception as exc:
                raise TransientScrapeError(f"Could not read result links: {exc}") from exc
        heading = self.search_page.locator("h1.DUwDvf").first
        if heading.count():
            return [{"url": self.search_page.url, "preview_name": heading.inner_text()}]
        return []

    def _feed_at_end(self):
        text = self._check_access(self.search_page)
        return (
            "you've reached the end of the list" in text
            or "you have reached the end of the list" in text
            or "no results found" in text
            or not self.search_page.locator('[role="feed"]').count()
        )

    def _scroll_result_feed(self):
        feed = self.search_page.locator('[role="feed"]').first
        if not feed.count():
            return
        self.pulse()
        try:
            feed.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        except Exception as exc:
            raise TransientScrapeError(f"Could not scroll the results feed: {exc}") from exc
        self._wait(max(0.8, self.delay))

    def iter_candidates(self, query, seen_keys=None):
        """Discover incrementally, including beyond previously excluded places."""
        self._open_search(query)
        known = set(seen_keys or ())
        seen_in_feed = set()
        stalled = 0
        rounds = 0
        while True:
            self.pulse()
            links = self._read_candidate_links()
            fresh_in_feed = 0
            for link in links:
                self.pulse()
                candidate = candidate_from_link(link.get("url"), link.get("preview_name", ""))
                if not candidate:
                    continue
                key = candidate["source_key"]
                if key not in seen_in_feed:
                    seen_in_feed.add(key)
                    fresh_in_feed += 1
                if key not in known:
                    known.add(key)
                    yield candidate
            rounds += 1
            stalled = 0 if fresh_in_feed else stalled + 1
            if self._feed_at_end():
                reason = "feed_end"
                break
            if stalled >= 4:
                reason = "feed_stalled"
                break
            self._scroll_result_feed()
        self.last_discovery = {"reason": reason, "rounds": rounds, "visible_unique": len(seen_in_feed)}
        self.log("info", f"Search discovery stopped: {reason}; {len(seen_in_feed)} distinct result links observed.")

    def _read_detail_snapshot(self):
        try:
            return self.detail_page.evaluate(_DETAIL_SNAPSHOT_JS)
        except Exception as exc:
            raise TransientScrapeError(f"Detail extraction failed: {exc}") from exc

    def _verified_snapshot(self, candidate):
        deadline = time.monotonic() + self.timeout
        previous = None
        stable_since = None
        latest = None
        matched = False
        while time.monotonic() < deadline:
            self._check_access(self.detail_page)
            latest = self._read_detail_snapshot()
            matched = detail_identity_matches(candidate, self.detail_page.url, latest.get("name"))
            if matched and latest.get("panel_ready") and not latest.get("busy"):
                signature = tuple(latest.get(key) for key in ("name", "address", "phone_raw", "website_url", "category", "rating_text", "reviews_text"))
                if signature != previous:
                    previous, stable_since = signature, time.monotonic()
                elif time.monotonic() - stable_since >= max(0.8, self.delay):
                    latest["panel_complete"] = True
                    return latest
            else:
                previous = stable_since = None
            self._wait(0.25)
        if not matched:
            raise DetailIdentityError("The detail name/source identity did not match the selected business.")
        # A correctly identified but incomplete panel can never prove absence.
        latest = latest or {}
        latest["panel_complete"] = False
        return latest

    def collect(self, candidate, query):
        self._goto(self.detail_page, candidate["url"])
        snapshot = self._verified_snapshot(candidate)
        self.pulse()
        observation = observation_from_snapshot(snapshot, candidate, query)
        return observation


_DETAIL_SNAPSHOT_JS = r"""() => {
    const name = document.querySelector('h1.DUwDvf') || document.querySelector('[role="main"] h1');
    if (!name) return {name: '', panel_ready: false};
    const main = name.closest('[role="main"]');
    if (!main) return {name: name.textContent.trim(), panel_ready: false};
    const text = element => element ? (element.textContent || '').trim() : '';
    const label = element => element ? (element.getAttribute('aria-label') || text(element)) : '';
    const address = main.querySelector('button[data-item-id="address"]');
    const phone = main.querySelector('button[data-item-id^="phone:"], a[href^="tel:"]');
    const category = main.querySelector('button.DkEaL, button[jsaction*="category"]');
    const ratingBox = main.querySelector('.F7nice');
    const rating = ratingBox && (ratingBox.querySelector('span[aria-hidden="true"]') || ratingBox.querySelector('[role="img"]'));
    let website = null, websiteError = false;
    try { website = main.querySelector('a[data-item-id="authority"]'); } catch (_) { websiteError = true; }
    const anchors = Array.from(main.querySelectorAll('a[href]'));
    const social = domain => { const a = anchors.find(el => { try { const h = new URL(el.href).hostname; return h === domain || h.endsWith('.' + domain); } catch (_) { return false; } }); return a ? a.href : null; };
    const body = main.innerText || '';
    const status = /permanently closed/i.test(body) ? 'permanently_closed' : (/temporarily closed/i.test(body) ? 'temporarily_closed' : '');
    return {
        name: text(name), url: location.href,
        panel_ready: Boolean(address || phone || website || category || status),
        busy: Boolean(main.querySelector('[aria-busy="true"], [role="progressbar"]')),
        address: label(address).replace(/^Address:\s*/i, ''),
        phone_raw: phone && phone.href && phone.href.startsWith('tel:') ? phone.href.slice(4) : label(phone).replace(/^Phone:\s*/i, ''),
        website_url: website ? website.href : null, website_error: websiteError,
        rating_text: label(rating),
        category: text(category), business_status: status,
        fb_link: social('facebook.com'), instagram_link: social('instagram.com')
    };
}"""
