"""Inspect Streamlit's installed browser bundle and its local HTTP responses.

The offline check reads the distribution's real RECORD manifest. It neither
imports Streamlit nor changes the installation. The HTTP check is deliberately
limited to the numeric loopback address and a small sample of installed scripts;
it never follows redirects or reads application data.
"""
from __future__ import annotations

import base64
import csv
import hashlib
from html.parser import HTMLParser
import http.client
from importlib import metadata
import io
import math
from pathlib import Path, PurePosixPath
import socket
import threading
import time
from urllib.parse import quote, unquote, urlsplit


_STATIC_PREFIX = "streamlit/static/"
_MAX_HTTP_ASSETS = 6
_MAX_HTTP_BYTES = 32 * 1024 * 1024
_JS_TYPES = {
    "application/javascript", "text/javascript", "application/x-javascript",
    "application/ecmascript", "text/ecmascript",
}


def _relative_path(value: str) -> str | None:
    """Return a safe relative file path without silently normalizing traversal."""
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ":" in parts[0]:
        return None
    return path.as_posix()


def _within_root(root: Path, relative: str) -> Path:
    safe = _relative_path(relative)
    if safe is None:
        raise ValueError("Unsafe relative asset path.")
    path = root.joinpath(*PurePosixPath(safe).parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Asset path leaves the installed static directory.")
    return path


def _file_digest(path: Path, algorithm: str) -> bytes:
    digest = hashlib.new(algorithm)
    # Variable-length hashes cannot be compared to an ordinary RECORD digest.
    if not digest.digest_size:
        raise ValueError("Variable-length hash algorithms are not supported.")
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


class _ScriptSources(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            source = dict(attrs).get("src")
            if source:
                self.sources.append(source)


def _entrypoints(index_path: Path) -> list[str]:
    parser = _ScriptSources()
    parser.feed(index_path.read_text(encoding="utf-8"))
    result: list[str] = []
    for source in parser.sources:
        parsed = urlsplit(source)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            continue
        relative = unquote(parsed.path)
        while relative.startswith("./"):
            relative = relative[2:]
        relative = relative.lstrip("/")
        safe = _relative_path(relative)
        if safe and safe.lower().endswith(".js") and safe not in result:
            result.append(safe)
    return result


def inspect_streamlit_assets(distribution=None) -> dict:
    """Verify every recorded Streamlit static file against its installed RECORD.

    ``distribution`` may be an importlib.metadata Distribution; omitting it reads
    the installed Streamlit distribution. Missing hashes or incomplete manifests
    are reported as unverified, never as a successful integrity check. Widget
    chunks are identified when their names are present, but separate chunk names
    are not required: some releases bundle widgets differently.
    """
    report = {
        "status": "unverified", "version": "", "static_root": "",
        "files_checked": 0, "missing": [], "mismatched": [],
        "unverifiable": [], "widget_files": {"DateInput": [], "TextArea": []},
        "entrypoint_files": [], "javascript_files": [], "errors": [],
    }
    if distribution is None:
        try:
            distribution = metadata.distribution("streamlit")
        except metadata.PackageNotFoundError:
            report["status"] = "missing_package"
            report["errors"].append("The Streamlit distribution is not installed in this Python environment.")
            return report
        except (OSError, ValueError) as exc:
            report["errors"].append(f"Cannot read Streamlit package metadata: {exc}")
            return report
    try:
        report["version"] = str(distribution.version or "")
        root = Path(distribution.locate_file("streamlit/static")).resolve()
        report["static_root"] = str(root)
        record_text = distribution.read_text("RECORD")
    except (AttributeError, TypeError, OSError, ValueError) as exc:
        report["errors"].append(f"Cannot read Streamlit package metadata: {exc}")
        return report
    if not record_text:
        report["errors"].append("Streamlit's RECORD manifest is missing or empty; asset integrity cannot be verified.")
        return report

    records: dict[str, tuple[str, str]] = {}
    manifest_problem = False
    try:
        for row_number, row in enumerate(csv.reader(io.StringIO(record_text), strict=True), 1):
            if not row or not row[0].startswith(_STATIC_PREFIX):
                continue
            if len(row) != 3:
                manifest_problem = True
                report["errors"].append(f"Malformed static asset entry on RECORD row {row_number}.")
                continue
            relative = _relative_path(row[0][len(_STATIC_PREFIX):])
            if relative is None:
                manifest_problem = True
                report["errors"].append(f"Unsafe static asset path on RECORD row {row_number}.")
                continue
            if relative in records:
                manifest_problem = True
                report["errors"].append(f"Duplicate static asset entry in RECORD: {relative}")
                continue
            records[relative] = (row[1], row[2])
    except (csv.Error, TypeError) as exc:
        manifest_problem = True
        report["errors"].append(f"Cannot parse Streamlit's RECORD manifest: {exc}")
    if not records:
        report["errors"].append("No recorded Streamlit static files were found; the browser bundle is unverified.")
        return report

    for relative, (hash_text, size_text) in sorted(records.items()):
        report["files_checked"] += 1
        if relative.lower().endswith(".js"):
            report["javascript_files"].append(relative)
            filename = PurePosixPath(relative).name.casefold()
            for widget in report["widget_files"]:
                if widget.casefold() in filename:
                    report["widget_files"][widget].append(relative)
        try:
            path = _within_root(root, relative)
            if not path.is_file():
                report["missing"].append(relative)
                continue
            if not size_text or not size_text.isdecimal():
                report["unverifiable"].append(relative)
                report["errors"].append(f"Missing or invalid RECORD size: {relative}")
            elif path.stat().st_size != int(size_text):
                report["mismatched"].append(relative)
                report["errors"].append(f"Installed file size differs from RECORD: {relative}")
            if "=" not in hash_text:
                if relative not in report["unverifiable"]:
                    report["unverifiable"].append(relative)
                report["errors"].append(f"Missing or invalid RECORD hash: {relative}")
                continue
            algorithm, encoded_hash = hash_text.split("=", 1)
            if not algorithm or not encoded_hash:
                raise ValueError("Empty RECORD hash algorithm or digest.")
            expected_digest = base64.b64decode(
                encoded_hash + "=" * (-len(encoded_hash) % 4), altchars=b"-_", validate=True,
            )
            actual_digest = _file_digest(path, algorithm)
            if len(expected_digest) != len(actual_digest):
                raise ValueError("The RECORD digest length does not match its hash algorithm.")
            if expected_digest != actual_digest:
                if relative not in report["mismatched"]:
                    report["mismatched"].append(relative)
                report["errors"].append(f"Installed file hash differs from RECORD: {relative}")
        except (OSError, ValueError, TypeError) as exc:
            if relative not in report["unverifiable"]:
                report["unverifiable"].append(relative)
            report["errors"].append(f"Cannot verify {relative}: {exc}")

    if "index.html" not in records:
        manifest_problem = True
        report["unverifiable"].append("index.html")
        report["errors"].append("The browser entry page index.html is not recorded in Streamlit's manifest.")
    elif "index.html" not in report["missing"]:
        try:
            report["entrypoint_files"] = _entrypoints(_within_root(root, "index.html"))
            if not report["entrypoint_files"]:
                manifest_problem = True
                report["errors"].append("No local JavaScript entrypoint could be identified in index.html.")
            for relative in report["entrypoint_files"]:
                if relative not in records:
                    manifest_problem = True
                    report["unverifiable"].append(relative)
                    report["errors"].append(f"JavaScript entrypoint is not recorded in the manifest: {relative}")
        except (OSError, UnicodeError, ValueError) as exc:
            manifest_problem = True
            report["errors"].append(f"Cannot inspect the browser entry page: {exc}")
    if not report["javascript_files"]:
        manifest_problem = True
        report["errors"].append("No JavaScript files are recorded for Streamlit's browser bundle.")

    if report["missing"] or report["mismatched"]:
        report["status"] = "failed"
    elif manifest_problem or report["unverifiable"]:
        report["status"] = "unverified"
    else:
        report["status"] = "ok"
    return report


def _loopback_origin(base_url: str) -> tuple[int, str]:
    if not isinstance(base_url, str) or "\\" in base_url or any(
        ord(character) < 33 or ord(character) == 127 for character in base_url
    ):
        raise ValueError("Use an HTTP URL at the numeric loopback address 127.0.0.1.")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("Only http://127.0.0.1[:port] local app URLs without credentials, query or fragment are allowed.")
    port = parsed.port if parsed.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError("The local HTTP port must be between 1 and 65535.")
    path = unquote(parsed.path).strip("/")
    if path and _relative_path(path) is None:
        raise ValueError("The local app base path is invalid.")
    # Parsing must not accept alternate spellings, an empty port, or authority
    # punctuation that was ignored by the URL parser.
    if parsed.netloc not in {"127.0.0.1", f"127.0.0.1:{port}"}:
        raise ValueError("Use the exact numeric loopback address 127.0.0.1 and an optional numeric port.")
    return port, ("/" + quote(path, safe="/-._~") if path else "")


def _sample_scripts(report: dict) -> list[str]:
    entries = list(report.get("entrypoint_files") or [])
    widgets = report.get("widget_files") or {}
    choices = entries[:1]
    # Reserve room for both failing widget families even when a release ships
    # more than one matching chunk for a particular widget.
    for family in ("DateInput", "TextArea"):
        choices.extend(list(widgets.get(family) or [])[:2])
    choices.extend(entries[1:])
    if not choices:
        choices.extend(list(report.get("javascript_files") or [])[:1])
    output: list[str] = []
    for relative in choices:
        safe = _relative_path(relative)
        if safe is None or not safe.lower().endswith(".js"):
            raise ValueError("An HTTP asset sample contains an invalid JavaScript path.")
        if safe not in output:
            output.append(safe)
        if len(output) == _MAX_HTTP_ASSETS:
            break
    return output


def _end_connection(sock: socket.socket) -> None:
    """Interrupt only the socket owned by this diagnostic request at its deadline."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        sock.close()


def verify_http_assets(base_url: str, report: dict, timeout: float = 5) -> dict:
    """Compare at most six local HTTP script responses to the installed bundle.

    A successful offline manifest report is required. Each HTTP request is
    bounded by ``timeout`` seconds and the size of its local file (at most 32 MiB).
    Redirects, non-JavaScript responses, HTML fallbacks, and byte differences are
    failures even when the server returns status 200.
    """
    result = {"status": "unverified", "checked": [], "errors": []}
    try:
        port, base_path = _loopback_origin(base_url)
        timeout = float(timeout)
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("HTTP asset timeout must be greater than zero and at most 30 seconds.")
    except (TypeError, ValueError) as exc:
        result["status"] = "failed"
        result["errors"].append(str(exc))
        return result
    if report.get("status") != "ok":
        result["errors"].append("Installed Streamlit assets have not passed offline verification; HTTP equality is unverified.")
        return result
    try:
        if not report.get("static_root"):
            raise ValueError("The installed Streamlit static directory was not supplied.")
        root = Path(report["static_root"]).resolve()
        scripts = _sample_scripts(report)
        if not scripts:
            result["errors"].append("No recorded JavaScript assets are available for a local HTTP check.")
            return result
        samples = []
        for relative in scripts:
            path = _within_root(root, relative)
            size = path.stat().st_size
            if not path.is_file() or not 0 <= size <= _MAX_HTTP_BYTES:
                raise ValueError(f"The installed script is missing or exceeds the HTTP check limit: {relative}")
            samples.append((relative, size, _file_digest(path, "sha256")))
    except (OSError, TypeError, ValueError) as exc:
        result["errors"].append(f"Cannot prepare the local HTTP asset comparison: {exc}")
        return result

    for relative, expected_size, expected_hash in samples:
        result["checked"].append(relative)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        deadline = time.monotonic() + timeout
        deadline_timer = None
        try:
            connection.connect()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Local asset connection exceeded its time limit.")
            request_socket = connection.sock
            request_socket.settimeout(remaining)
            # A whole-request deadline also bounds slowly streamed headers and
            # bodies. Socket timeouts alone can reset after each small read.
            deadline_timer = threading.Timer(remaining, _end_connection, args=(request_socket,))
            deadline_timer.daemon = True
            deadline_timer.start()
            request_path = base_path + "/" + quote(relative, safe="/-._~")
            connection.request("GET", request_path, headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}; expected 200.")
            mime = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if mime not in _JS_TYPES:
                raise ValueError(f"Non-JavaScript Content-Type: {mime or '(missing)'}. A browser module cannot load this response.")
            encoding = (response.getheader("Content-Encoding") or "identity").lower().strip()
            if encoding != "identity":
                raise ValueError(f"Unexpected Content-Encoding {encoding!r} despite requesting identity.")
            content_length = response.getheader("Content-Length")
            if content_length is not None and int(content_length) != expected_size:
                raise ValueError("HTTP body size differs from the installed JavaScript file.")
            digest, total, prefix = hashlib.sha256(), 0, b""
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Local asset response exceeded its time limit.")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, expected_size + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if len(prefix) < 256:
                    prefix += chunk[:256 - len(prefix)]
                if total > expected_size:
                    raise ValueError("HTTP body is larger than the installed JavaScript file.")
                digest.update(chunk)
            lowered = prefix.lstrip().lower()
            if lowered.startswith((b"<!doctype html", b"<html")):
                raise ValueError("The server returned an HTML page for a JavaScript asset.")
            if total != expected_size or digest.digest() != expected_hash:
                raise ValueError("HTTP JavaScript differs from the installed file; the server or browser bundle may be stale.")
        except (OSError, ValueError, http.client.HTTPException) as exc:
            result["errors"].append(f"{relative}: {exc}")
        finally:
            if deadline_timer is not None:
                deadline_timer.cancel()
            connection.close()
    result["status"] = "failed" if result["errors"] else "ok"
    return result
