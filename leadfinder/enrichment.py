"""Small, optional website observations using a bounded public-web fetch.

Only the homepage and at most one same-domain contact page are inspected.  DNS
answers and every redirect are validated, connections are pinned to validated
public IPs, HTTPS verifies the original hostname, and environment proxy settings
are not consulted.  No forms are submitted and no messages are sent.
"""

from __future__ import annotations

import datetime as dt
import http.client
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

_MAX_BYTES = 2 * 1024 * 1024
_MAX_LINKS = 1000
_MAX_REDIRECTS = 4
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")
_EMAIL = re.compile(r"(?<![\w.+-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?)+)(?![\w.-])", re.I)
_CONTACT_WORDS = ("contact", "contactez", "contacter", "contacto", "kontakt", "اتصل", "تواصل")
_BOOKING_WORDS = ("book", "booking", "appointment", "reserve", "reservation", "rendez-vous", "rendez vous", "حجز")
_ENQUIRY_WORDS = ("enquiry", "inquiry", "quote", "estimate", "devis", "demande", "استفسار")


class _AuditError(Exception):
    pass


class _BlockedDestination(_AuditError):
    pass


@dataclass(frozen=True)
class _Target:
    url: str
    scheme: str
    hostname: str
    port: int
    request_path: str
    ips: tuple[str, ...]


@dataclass(frozen=True)
class _Response:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes = b""
    truncated: bool = False


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        mapped = getattr(address, "ipv4_mapped", None)
        if not address.is_global or any((address.is_loopback, address.is_link_local, address.is_multicast, address.is_reserved, address.is_unspecified)):
            return False
        if mapped is not None and not _is_public_ip(str(mapped)):
            return False
        if address.version == 6 and any(address in network for network in (
            ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"),
            ipaddress.ip_network("2002::/16"), ipaddress.ip_network("2001::/32"),
        )):
            return False
        return True
    except ValueError:
        return False


def _normalized_url(value: str) -> tuple[str, str, str, int, str]:
    raw = str(value or "").strip()
    if not raw or any(ord(c) < 32 for c in raw) or "\\" in raw:
        raise _BlockedDestination("Invalid HTTP(S) URL.")
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise _BlockedDestination("Only HTTP and HTTPS destinations can be inspected.")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise _BlockedDestination("A public hostname without embedded credentials is required.")
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not hostname or "%" in hostname or any(c.isspace() for c in hostname):
            raise _BlockedDestination("Invalid destination hostname.")
        if hostname in {"localhost", "localhost.localdomain", "instance-data"} or hostname.endswith(_LOCAL_SUFFIXES):
            raise _BlockedDestination("Local or internal destinations cannot be inspected.")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
            if not re.fullmatch(r"[a-z0-9.-]+", hostname) or len(hostname) > 253:
                raise _BlockedDestination("Invalid destination hostname.")
            if any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in hostname.split(".")):
                raise _BlockedDestination("Invalid destination hostname.")
        if literal is not None and not _is_public_ip(hostname):
            raise _BlockedDestination("Non-public IP destinations cannot be inspected.")
        port = parsed.port or (443 if scheme == "https" else 80)
        if port != (443 if scheme == "https" else 80):
            raise _BlockedDestination("Only standard HTTP(S) ports are supported.")
        netloc = "[" + hostname + "]" if ":" in hostname else hostname
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="%:@!$&'()*+,;=/?-._~")
        normalized = urlunsplit((scheme, netloc, path, query, ""))
        request_path = path + ("?" + query if query else "")
        return normalized, scheme, hostname, port, request_path
    except (ValueError, UnicodeError) as error:
        raise _BlockedDestination("Invalid destination URL.") from error


def _resolve_public_ips(hostname: str, port: int) -> tuple[str, ...]:
    """Reject mixed public/private answers rather than selecting only the public one."""
    try:
        answers = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except OSError as error:
        raise _AuditError("The website hostname could not be resolved.") from error
    ips: list[str] = []
    for family, _kind, _protocol, _canonical, address in answers:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        ip = address[0]
        if not _is_public_ip(ip):
            raise _BlockedDestination("DNS returned a non-public destination.")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise _AuditError("The website has no usable public address.")
    return tuple(ips)


@lru_cache(maxsize=1)
def _domain_parser():
    # Deferred, genuine dependency: transport/security checks themselves use
    # only the standard library. There is no substitute or metadata fallback.
    import tldextract
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)


def _site_domain(hostname: str) -> str:
    parts = _domain_parser()(hostname)
    return (parts.domain + "." + parts.suffix) if parts.suffix else hostname


def _validated_target(url: str, allowed_domain: str | None = None) -> _Target:
    normalized, scheme, hostname, port, path = _normalized_url(url)
    if allowed_domain and _site_domain(hostname) != allowed_domain:
        raise _BlockedDestination("Contact-page navigation left the website's domain.")
    ips = _resolve_public_ips(hostname, port)
    return _Target(normalized, scheme, hostname, port, path, ips)


def _check_peer(sock: socket.socket, expected_ip: str) -> None:
    try:
        peer = sock.getpeername()[0]
        if not _is_public_ip(peer) or ipaddress.ip_address(peer) != ipaddress.ip_address(expected_ip):
            raise _BlockedDestination("The connection did not reach the validated public address.")
    except (ValueError, OSError) as error:
        raise _BlockedDestination("The remote connection address could not be verified.") from error


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, ip: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout)
        self._validated_ip = ip

    def connect(self) -> None:
        # Using the numeric address prevents a second hostname lookup/rebinding.
        sock = socket.create_connection((self._validated_ip, self.port), self.timeout)
        try:
            _check_peer(sock, self._validated_ip)
        except Exception:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._validated_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._validated_ip, self.port), self.timeout)
        try:
            _check_peer(sock, self._validated_ip)
            # Pin transport to the IP while verifying certificate/SNI against
            # the original host. Never disable certificate verification.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _request_once(target: _Target, timeout: float) -> _Response:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    for ip in target.ips[:2]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        connection_class = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
        connection = connection_class(target.hostname, ip, target.port, remaining)
        try:
            connection.request("GET", target.request_path, headers={
                "User-Agent": "LeadFinderWebsiteCheck/2.0",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "identity", "Connection": "close",
            })
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status in _REDIRECT_CODES:
                return _Response(target.url, response.status, headers)
            encoding = headers.get("content-encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise _AuditError("The website returned an unsupported compressed response.")
            # read1 returns after one buffered/socket read so a trickling body
            # cannot keep resetting an inactivity timeout indefinitely.
            chunks: list[bytes] = []
            total = 0
            while total <= _MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _AuditError("The website check reached its time limit while reading the response.")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, _MAX_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            body = b"".join(chunks)
            return _Response(target.url, response.status, headers, body[:_MAX_BYTES], len(body) > _MAX_BYTES)
        except _BlockedDestination:
            raise
        except (OSError, http.client.HTTPException, _AuditError) as error:
            last_error = error
        finally:
            connection.close()
    if isinstance(last_error, _AuditError):
        raise last_error
    if isinstance(last_error, ssl.SSLCertVerificationError):
        raise _AuditError("HTTPS certificate verification failed.") from last_error
    raise _AuditError("The website could not be fetched within the request budget.") from last_error


def _fetch_url(url: str, deadline: float, allowed_domain: str | None = None) -> _Response:
    current = url
    visited: set[str] = set()
    for _hop in range(_MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _AuditError("The website check reached its time limit.")
        target = _validated_target(current, allowed_domain)
        if target.url in visited:
            raise _AuditError("The website entered a redirect loop.")
        visited.add(target.url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _AuditError("The website check reached its time limit during hostname resolution.")
        response = _request_once(target, remaining)
        if response.status not in _REDIRECT_CODES:
            return response
        location = response.headers.get("location", "").strip()
        if not location:
            raise _AuditError("The website returned a redirect without a destination.")
        current = urljoin(target.url, location)
    raise _AuditError("The website exceeded the redirect limit.")


class _PageParser(HTMLParser):
    """Collect visible static text and ordinary links, never scripts/config JSON."""

    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _HIDDEN_TAGS = {"script", "style", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []
        self._in_title = False
        self._anchor: dict[str, Any] | None = None
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        inherited_hidden = bool(self._stack and self._stack[-1][1])
        style = (attributes.get("style") or "").lower().replace(" ", "")
        hidden = inherited_hidden or tag in self._HIDDEN_TAGS or "hidden" in attributes or (
            (attributes.get("aria-hidden") or "").lower() == "true"
        ) or "display:none" in style or "visibility:hidden" in style
        if tag not in self._VOID_TAGS:
            self._stack.append((tag, hidden))
        if tag == "title" and not hidden:
            self._in_title = True
        if tag == "a" and not hidden:
            if self._anchor is not None:
                self._finish_anchor()
            self._anchor = {"href": attributes.get("href") or "", "text_parts": [], "label": attributes.get("aria-label") or ""}

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a":
            self._finish_anchor()
        if tag == "title":
            self._in_title = False
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1][1]:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.text_parts.append(data)
        if self._anchor is not None:
            self._anchor["text_parts"].append(data)

    def _finish_anchor(self) -> None:
        if self._anchor is not None:
            label = " ".join(" ".join(self._anchor["text_parts"]).split()) or self._anchor["label"]
            if len(self.links) < _MAX_LINKS:
                self.links.append({"href": self._anchor["href"], "text": label[:300]})
            self._anchor = None

    def close(self) -> None:
        super().close()
        self._finish_anchor()


def _html_observations(response: _Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    is_html = "text/html" in content_type or "application/xhtml+xml" in content_type or (
        not content_type and bool(re.search(br"<(?:!doctype\s+html|html|head|body|title|p|div|a)(?:\s|>)", response.body[:2048], re.I))
    )
    if not is_html:
        return {"is_html": False, "title": "", "emails": [], "email_sources": [], "links": []}
    encoding_match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type)
    encoding = encoding_match.group(1) if encoding_match else "utf-8"
    try:
        html = response.body.decode(encoding, errors="replace")
    except LookupError:
        html = response.body.decode("utf-8", errors="replace")
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    email_sources: list[dict[str, str]] = []
    emails: dict[str, str] = {}

    def add_emails(text: str, kind: str) -> None:
        for match in _EMAIL.finditer(text):
            if len(emails) >= 100:
                break
            email = match.group(1).rstrip(".")
            if len(email) > 254:
                continue
            local, domain = email.rsplit("@", 1)
            if len(local) > 64 or "." not in domain:
                continue
            # Resource filenames often resemble email addresses; require an
            # ordinary letter-based TLD and skip common image/font suffixes.
            suffix = domain.rsplit(".", 1)[-1].lower()
            if not re.fullmatch(r"[a-z]{2,63}", suffix) or suffix in {"png", "jpg", "jpeg", "gif", "webp", "svg", "woff", "woff2"}:
                continue
            key = email.casefold()
            emails.setdefault(key, email)
            item = {"email": emails[key], "source_url": response.url, "kind": kind}
            if item not in email_sources:
                email_sources.append(item)

    add_emails(" ".join(parser.text_parts), "displayed_text")
    links: list[dict[str, str]] = []
    for item in parser.links:
        href = item["href"].strip()
        if href.lower().startswith("mailto:"):
            # Do not mine addresses out of cc, bcc, body or script parameters.
            add_emails(unquote(href[7:].split("?", 1)[0]), "public_mailto_link")
            continue
        if not href or href.startswith("#"):
            continue
        try:
            normalized, _scheme, _host, _port, _path = _normalized_url(urljoin(response.url, href))
        except _BlockedDestination:
            continue
        record = {"url": normalized, "text": item["text"], "source_url": response.url}
        if record not in links:
            links.append(record)
    return {
        "is_html": True, "title": " ".join(" ".join(parser.title_parts).split())[:300],
        "emails": list(emails.values()), "email_sources": email_sources, "links": links,
    }


def _link_mentions(link: dict[str, str], words: tuple[str, ...]) -> bool:
    text = (link.get("text", "") + " " + unquote(urlsplit(link["url"]).path)).casefold()
    return any(word in text for word in words)


def _contact_candidate(links: list[dict[str, str]], homepage: str) -> str | None:
    if not links:
        return None
    home_host = urlsplit(homepage).hostname or ""
    domain = _site_domain(home_host)
    candidates = []
    for index, link in enumerate(links):
        host = urlsplit(link["url"]).hostname or ""
        if _site_domain(host) != domain or link["url"] == homepage or not _link_mentions(link, _CONTACT_WORDS):
            continue
        path = urlsplit(link["url"]).path.casefold().rstrip("/")
        priority = 0 if re.search(r"/(?:contact|contact-us|contactez-nous|kontakt|contacto)(?:\.[a-z]+)?$", path) else 1
        candidates.append((priority, index, link["url"]))
    return min(candidates)[2] if candidates else None


def audit_website(url: str, timeout: int | float = 15) -> dict[str, Any]:
    """Return concrete observations, or a structured blocked/error outcome.

    Timeout is the shared socket/request budget for the homepage, redirects,
    and optional contact page. System DNS resolution is also bounded by the
    resolver's own timeout. A partial result keeps successfully observed data.
    """
    result: dict[str, Any] = {
        "status": "error", "url": str(url or ""), "final_url": None,
        "http_status": None, "content_type": None, "https": None, "title": "",
        "contact_page": None, "emails": [], "email_sources": [],
        "booking_links": [], "enquiry_links": [], "source_urls": [],
        "observations": [], "errors": [], "truncated": False,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    try:
        budget = float(timeout)
        if not 0 < budget <= 120:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        result["errors"].append("Timeout must be a positive number no greater than 120 seconds.")
        return result
    deadline = time.monotonic() + budget
    try:
        homepage = _fetch_url(url, deadline)
    except _BlockedDestination as error:
        result["status"] = "blocked"
        result["errors"].append(str(error))
        return result
    except (OSError, _AuditError, http.client.HTTPException) as error:
        result["errors"].append(str(error))
        return result
    result.update({
        "final_url": homepage.url, "http_status": homepage.status,
        "content_type": homepage.headers.get("content-type", ""),
        "https": urlsplit(homepage.url).scheme == "https",
        "source_urls": [homepage.url], "truncated": homepage.truncated,
    })
    result["observations"].append("Homepage returned HTTP " + str(homepage.status) + ".")
    if not 200 <= homepage.status < 300:
        result["status"] = "http_error"
        return result
    try:
        observations = _html_observations(homepage)
    except (ValueError, UnicodeError) as error:
        result["errors"].append("The HTML response could not be parsed: " + str(error))
        return result
    if not observations["is_html"]:
        result["status"] = "unsupported"
        result["observations"].append("The response is not an HTML page; contact details were not extracted.")
        return result
    result["status"] = "partial" if homepage.truncated else "ok"
    result["title"] = observations["title"]

    def merge(page_observations: dict[str, Any]) -> None:
        for email in page_observations["emails"]:
            if email.casefold() not in {old.casefold() for old in result["emails"]}:
                result["emails"].append(email)
        for evidence in page_observations["email_sources"]:
            if evidence not in result["email_sources"]:
                result["email_sources"].append(evidence)
        for link in page_observations["links"]:
            if _link_mentions(link, _BOOKING_WORDS) and link not in result["booking_links"]:
                result["booking_links"].append(link)
            if (_link_mentions(link, _ENQUIRY_WORDS) or _link_mentions(link, _CONTACT_WORDS)) and link not in result["enquiry_links"]:
                result["enquiry_links"].append(link)

    merge(observations)
    contact = _contact_candidate(observations["links"], homepage.url)
    if contact:
        result["contact_page"] = contact
        try:
            domain = _site_domain(urlsplit(homepage.url).hostname or "")
            contact_response = _fetch_url(contact, deadline, allowed_domain=domain)
            result["contact_page"] = contact_response.url
            result["source_urls"].append(contact_response.url)
            result["observations"].append("Contact page returned HTTP " + str(contact_response.status) + ".")
            if 200 <= contact_response.status < 300:
                contact_data = _html_observations(contact_response)
                if contact_data["is_html"]:
                    merge(contact_data)
                else:
                    result["status"] = "partial"
                    result["observations"].append("The contact response was not an HTML page.")
            else:
                result["status"] = "partial"
            result["truncated"] = result["truncated"] or contact_response.truncated
        except (OSError, _AuditError, http.client.HTTPException, ValueError, UnicodeError) as error:
            result["status"] = "partial"
            result["errors"].append("Contact page: " + str(error))
    if result["truncated"]:
        result["status"] = "partial"
        result["observations"].append("An HTML response exceeded the size limit; observations are incomplete.")
    result["observations"].append("Email addresses and action links are observations from public page content; availability and deliverability are unverified.")
    return result
