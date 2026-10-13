"""Pure pre-dispatch validation of LLM-supplied MCP tool arguments.

The tool name, service and schema are NOT permission. Detect only obvious
unresolved documentation markers and reject before quota reservation, network
connection or remote side effects. Never persist argument values in receipts.
"""
from __future__ import annotations

import re
from typing import Any

# Whole-token documentation placeholders, not arbitrary user prose.
_TEMPLATE_TOKEN = re.compile(
    r"(?i)(?:^|[\\s/])(?:"
    r"(?:your|insert|replace|enter|fill)[_-]"
    r"(?:user(?:name)?|owner|org(?:anization)?|repo(?:sitory)?|"
    r"account|project|id|token|api[-_]?key|value|name)"
    r"|(?:owner|username|repository|repo|account|project)[_-]placeholder"
    r")(?:$|[\\s/])"
)
_DELIMITED = re.compile(
    r"(?:<([a-z][a-z0-9_-]{0,60})>|"
    r"\\{\\{([a-z][a-z0-9_-]{0,60})\\}\\}|"
    r"(?<!\\{)\\{([a-z][a-z0-9_-]{0,60})\\}(?!\\})|"
    r"\\$\\{([a-z][a-z0-9_-]{0,60})\\}|"
    r"\\[([a-z][a-z0-9_-]{0,60})\\])",
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
            if (word.lower() in _EXACT or
                    _DELIMITED.search(word) is not None or
                    _TEMPLATE_TOKEN.search(word) is not None):
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
