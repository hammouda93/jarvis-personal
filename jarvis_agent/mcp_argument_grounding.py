"""Pure pre-dispatch validation of LLM-supplied MCP tool arguments.

The tool name, service and schema are NOT permission. Detect only obvious
unresolved documentation markers and reject before quota reservation, network
connection or remote side effects. Never persist argument values in receipts.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

# Whole-token documentation placeholders, not arbitrary user prose.
_TEMPLATE_TOKEN = re.compile(
    r"(?i)(?:^|[\s/])(?:"
    r"(?:your|insert|replace|enter|fill)[_-]"
    r"(?:user(?:name)?|owner|org(?:anization)?|repo(?:sitory)?|"
    r"account|project|id|token|api[-_]?key|value|name)"
    r"|(?:owner|username|repository|repo|account|project)[_-]placeholder"
    r")(?:$|[\s/])"
)
_DELIMITED = re.compile(
    r"(?:<([a-z][a-z0-9_-]{0,60})>|"
    r"\{\{([a-z][a-z0-9_-]{0,60})\}\}|"
    r"(?<!\{)\{([a-z][a-z0-9_-]{0,60})\}(?!\})|"
    r"\$\{([a-z][a-z0-9_-]{0,60})\}|"
    r"\[([a-z][a-z0-9_-]{0,60})\])",
    re.IGNORECASE,
)
_EXACT = frozenset({
    "todo", "tbd", "placeholder", "your-username", "your_username",
    "your-owner", "your_owner", "insert-owner", "insert_owner",
    "replace-me", "replace_me", "fill-me", "fill_me", "???",
})


def unresolved_argument_paths(arguments: Any) -> tuple[str, ...]:
    """Return only key paths, NEVER sensitive values.

    Intentionally narrow detection: a literal word 'example', a real URI or
    strings describing a template are not automatically an invalid field.
    """
    unresolved: list[str] = []

    def check(value: Any, path: str, depth: int) -> None:
        if depth > 9 or len(unresolved) >= 15:
            return
        if isinstance(value, str):
            word = value.strip()
            # URL template fields may be percent-encoded. Decode for local
            # analysis only; never mutate the actual tool argument.
            candidate = unquote(word) if "%" in word else word
            matches = list(_DELIMITED.finditer(candidate))
            def is_field(match):
                opening_html_tag = (match.group(1) or "").lower()
                if opening_html_tag in {
                    "html", "body", "head", "title", "div", "span",
                    "p", "a", "em", "strong", "b", "i", "h1", "h2", "h3",
                    "h4", "h5", "ul", "ol", "li", "pre", "code",
                } and re.search(r"</" + opening_html_tag + r"\s*>", candidate, re.I):
                    return False  # actual markup, not an ID placeholder
                return True
            if (candidate.lower() in _EXACT or
                    _TEMPLATE_TOKEN.search(candidate) is not None or
                    any(is_field(match) for match in matches)):
                unresolved.append(path)
        elif isinstance(value, dict):
            for key, item in list(value.items())[:120]:
                if not isinstance(key, str):
                    continue
                check(item, (path + "." if path else "") + key[:75], depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value[:120]):
                check(item, f"{path}[{index}]", depth + 1)

    if isinstance(arguments, dict):
        check(arguments, "", 0)
    return tuple(unresolved)



def missing_required_arguments(arguments: Any, schema: Any) -> tuple[str, ...]:
    """Validate only *declared* top-level required keys, without guessing values.

    Advanced server JSON schemas still belong to the actual MCP server.
    A clear local omission should not spend an API call or become an uncertain
    remote mutation. No defaults and no new authority are inferred.
    """
    if not isinstance(schema, dict) or not isinstance(arguments, dict):
        return ()
    required = schema.get("required")
    if not isinstance(required, list):
        return ()
    missing = []
    for key in required[:120]:
        if not isinstance(key, str):
            continue
        if (key not in arguments or arguments[key] is None or
                isinstance(arguments[key], str) and not arguments[key].strip()):
            missing.append(key[:75])
        if len(missing) >= 15:
            break
    return tuple(missing)
