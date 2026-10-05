"""Read-only HWND-scoped UIA worker. Parent may kill a stuck provider safely."""
from __future__ import annotations

import json
import sys


def edit_state(wrapper):
    """A name/placeholder is not a value, and an Edit role is not write proof."""
    try:
        pattern = wrapper.iface_value
        return {"writable": not bool(pattern.CurrentIsReadOnly),
                "value": str(pattern.CurrentValue or "")}
    except Exception:
        return {"writable": False, "value": None}


def inspect(window_id):
    from . import windows_perception as win
    window = win._desktop().window(handle=int(window_id)).wrapper_object()
    wrappers, traversal = win._bounded_descendants(window, time_budget_s=1.0, max_nodes=300)
    controls = []
    for wrapper in wrappers:
        if not win._is_visible(wrapper) or not win._is_enabled(wrapper):
            continue
        runtime_id = list(wrapper.element_info.runtime_id or ())
        if not runtime_id:
            continue
        item = win._compact_control(json.dumps(runtime_id), wrapper)
        kind = item["type"]
        item["writable"] = False
        if kind in {"Edit", "ComboBox"}:
            item.update(edit_state(wrapper))
        item["actionable"] = kind in win._INTERACTIVE_TYPES
        item["focused"] = bool(wrapper.has_keyboard_focus())
        controls.append(item)
    useful = [c for c in controls if c["type"] not in {"TitleBar", "MenuBar", "Window", "Pane"}
              and (c.get("name") or c["writable"])]
    chrome_only = win._system_chrome_only(window,
        [w for w in wrappers if win._control_type(w) in win._INTERACTIVE_TYPES],
        [w for w in wrappers if win._control_type(w) == "Document"],
        [w for w in wrappers if win._control_type(w) == "Text"])
    return {"controls": controls, "snapshot": {"semantic_coverage": "usable" if useful and
            not chrome_only else "insufficient", "traversal": traversal,
            # A depth-bounded traversal cannot establish global absence.
            "tree_complete": False}}


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(sys.argv[1]), ensure_ascii=True))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
