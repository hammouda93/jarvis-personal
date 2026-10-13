"""Reject unresolved *template* URL placeholders before browser dispatch.

Pure and intentionally conservative: we cannot prove an arbitrary URL refers
to the requested resource. This guard handles explicit placeholders only;
source discovery, permissions and final verification remain runtime duties.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


_PLACEHOLDER_PART = re.compile(
    r"(?i)(?:^|[/])"
    r"(?:your|insert|replace|example)[-_]"
    r"(?:username|user|org|organization|owner|repository|repo|account|"
    r"project|project[-_]id|token|api[-_]?key)"
    r"(?=$|[/;?#])"
)
_TEMPLATE_FIELD = re.compile(
    r"(?:\{[a-z][a-z0-9_-]{0,55}\}|"
    r"<[a-z][a-z0-9_-]{0,55}>|"
    r"\[[a-z][a-z0-9_-]{0,55}\])",
    re.IGNORECASE,
)


def unresolved_url_template(url: str) -> bool:
    """True when a URL obviously contains an unfilled documentation template."""
    if not isinstance(url, str) or len(url) > 8192:
        return True
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False  # scheme validation remains the original tool's duty
        if not parsed.hostname:
            return False
        content = unquote(parsed.netloc + parsed.path)
        return bool(_TEMPLATE_FIELD.search(content) or _PLACEHOLDER_PART.search(content))
    except (ValueError, UnicodeError):
        return True
