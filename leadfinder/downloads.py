"""Private inline exports, without registering a shared Streamlit media URL.

The file bytes are sent inside the authorized session's component document.
As with any displayed/exported data, a recipient can retain their own copy.
"""
from __future__ import annotations

import base64
from html import escape
from pathlib import PurePath

MAX_INLINE_BYTES = 40 * 1024 * 1024
ALLOWED_MIMES = {"text/csv", "application/json", "application/vnd.sqlite3", "application/octet-stream", "text/plain"}


def download_document(label: str, data: bytes | str, *, file_name: str, mime: str) -> str:
    if mime not in ALLOWED_MIMES:
        raise ValueError("Unsupported export type.")
    if not isinstance(file_name, str) or not file_name or len(file_name) > 160:
        raise ValueError("Invalid export filename.")
    if PurePath(file_name).name != file_name or any(c in file_name for c in ('/', '\\', '\r', '\n', '\x00')):
        raise ValueError("Use a filename without a directory.")
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        raise TypeError("Exports must contain text or bytes.")
    if len(data) > MAX_INLINE_BYTES:
        raise ValueError("This export is too large for an in-browser download. Narrow the lead export, or create a database backup in Settings.")
    payload = base64.b64encode(data).decode("ascii")
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;padding:2px;font-family:system-ui,sans-serif">'
        '<a style="display:inline-block;padding:9px 14px;border:1px solid #b8c9c5;border-radius:8px;color:#175e54;background:#fff;text-decoration:none;font-size:14px" '
        f'href="data:{mime};base64,{payload}" download="{escape(file_name, quote=True)}">{escape(str(label))}</a>'
        '</body></html>'
    )


def private_download(label: str, data: bytes | str, *, file_name: str, mime: str,
                     authorize, key: str | None = None) -> None:
    import streamlit as st
    import streamlit.components.v1 as components

    # Call immediately before emitting the bytes, even if a report was already
    # prepared or this is a fragment rerun. No caller-provided boolean suffices.
    authorize()
    try:
        document = download_document(label, data, file_name=file_name, mime=mime)
    except ValueError as exc:
        st.warning(str(exc))
        return
    authorize()
    components.html(document, height=54, scrolling=False)
