"""Read-only temporal evidence helpers for Memory V5 agent-tool mode.

These helpers check source text before trusting contradictory LLM projections.
They never edit the original durable note or assert an event occurred.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from dataclasses import replace

from .semantic_memory import MemoryProjection


def iso_day(value: str) -> str:
    """Accept canonical ISO dates and ISO datetimes, never arbitrary prefixes."""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?", text):
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def day_mentioned_in_raw(raw: str, day: str) -> bool:
    """Only confirm an explicit source date in ISO or unambiguous D/M/Y form."""
    parsed = iso_day(day)
    if not parsed:
        return False
    yyyy, mm, dd = parsed.split("-")
    source = str(raw or "")
    iso = re.escape(parsed)
    day_first = rf"0?{int(dd)}[/.-]0?{int(mm)}[/.-]{yyyy}"
    return bool(
        re.search(rf"(?<!\d)(?:{iso}|{day_first})(?!\d)", source)
    )


def event_day_and_warning(
    projection: MemoryProjection, raw: str
) -> tuple[str, str]:
    """Select source-grounded date; reject contradictory ungrounded metadata."""
    qualifiers = projection.qualifiers
    q_day = next(
        (
            iso_day(qualifiers.get(k, ""))
            for k in ("date", "datetime", "start_at")
            if iso_day(qualifiers.get(k, ""))
        ),
        "",
    )
    v_day = iso_day(projection.value)
    if q_day and v_day and q_day != v_day:
        if day_mentioned_in_raw(raw, v_day):
            return v_day, "conflicting_projection_dates"
        if day_mentioned_in_raw(raw, q_day):
            return q_day, "conflicting_projection_dates"
        return "", "unverified_conflicting_projection_dates"
    return q_day or v_day, ""


def validate_temporal_projection(
    projection: MemoryProjection, raw: str,
) -> tuple[MemoryProjection | None, str]:
    """Repair only conflicts where the existing source directly proves the date."""
    day, warning = event_day_and_warning(projection, raw)
    if not warning:
        return projection, ""
    if not day:
        return None, warning
    # A date-only value different from a source-grounded day is unsafe.
    value_day = iso_day(projection.value)
    if value_day and value_day != day:
        return None, "date_value_conflicts_with_source"
    fixed = dict(projection.qualifiers)
    fixed["date"] = day
    return replace(projection, qualifiers=fixed), warning


def events_in_range(
    store, start: str, end: str, *, limit: int = 30,
) -> dict:
    """Return bounded, fully read-only semantic facts for inclusive dates."""
    start_iso = iso_day(start)
    end_iso = iso_day(end)
    if not start_iso or start_iso != start or not end_iso or end_iso != end:
        raise ValueError("memory_date_requires_iso_yyyy_mm_dd")
    start_date = date.fromisoformat(start_iso)
    end_date = date.fromisoformat(end_iso)
    if not start_date <= end_date <= start_date + timedelta(days=31):
        raise ValueError("memory_date_range_requires_0_to_31_days")
    hits = []
    total = 0
    for fact in store.semantic_facts(status="active"):
        day, warning = event_day_and_warning(
            fact.projection, fact.raw_content,
        )
        if not day or not start_iso <= day <= end_iso:
            continue
        total += 1
        if len(hits) >= limit:
            continue
        projection = fact.projection
        hits.append({
            "memory_id": fact.memory_id,
            "subject": projection.subject,
            "relation": projection.relation,
            "value": projection.value,
            "event_date": day,
            "qualifiers": dict(projection.qualifiers),
            "created_at": fact.created_at,
            "raw": str(fact.raw_content or "")[:900],
            "warning": warning,
        })
    hits.sort(key=lambda row: (row["event_date"], row["memory_id"]))
    return {
        "status": "resolved" if hits else "missing",
        "start_date": start_iso,
        "end_date": end_iso,
        "hits": hits,
        "total_matching_facts": total,
        "truncated": total > len(hits),
        "read_only": True,
    }


def yearless_date_guard(
    store, user_text: str, selected_date: str,
    *, reference_date: date | None = None,
) -> dict[str, object]:
    """Flag unsupported date inference, without guessing or rewriting SQLite.

    An agent might select a year that the user never supplied (e.g. 2023
    instead of 2026). A missing result for that fabricated year must not be
    misrepresented as a memory-wide negative. Return grounded alternatives
    so the same brain can choose a new query or ask the year.
    """
    import unicodedata
    source = unicodedata.normalize("NFKD", str(user_text or "").casefold())
    source = "".join(c for c in source if not unicodedata.combining(c))
    months = {
        "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11,
        "decembre": 12,
    }
    match = re.search(
        r"\b(\d{1,2})\s+(" + "|".join(months) + r")\b", source,
    )
    if not match or re.search(r"\b(?:19|20)\d{2}\b", source):
        return {}
    day, month = int(match.group(1)), months[match.group(2)]
    chosen = iso_day(selected_date)
    if not chosen:
        return {}
    chosen_date = date.fromisoformat(chosen)
    if (chosen_date.day, chosen_date.month) != (day, month):
        return {
            "status": "date_argument_mismatch",
            "requested_day_month": f"{month:02d}-{day:02d}",
            "selected_date": chosen,
        }
    today = reference_date if reference_date is not None else date.today()
    if abs(chosen_date.year - today.year) <= 1:
        return {}
    # Search known indexed facts for the SAME requested month/day, not for
    # arbitrary other records. Candidate years are evidence, not assertions.
    candidates = set()
    for fact in store.semantic_facts(status="active"):
        value, _ = event_day_and_warning(fact.projection, fact.raw_content)
        if value and value[5:] == f"{month:02d}-{day:02d}":
            candidates.add(value)
    return {
        "status": "year_not_grounded",
        "selected_date": chosen,
        "candidate_dates": sorted(candidates)[:8],
        "note": "La demande ne spécifie pas l'année. Vérifie l'année avec "
                "le contexte ou demande une précision avant de conclure.",
    }
