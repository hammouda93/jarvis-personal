"""Maintenance CLI for the rebuildable Semantic Memory V5 sidecar.

This module never edits or deletes rows from the historical memories table.
Only the semantic sidecar can be reset/rebuilt.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .memory import LOCAL_MEMORY
from .memory_core_store import MemoryCoreStore
from .memory_semantic_interpreter import (
    ModelSemanticMemoryInterpreter,
    SemanticMemoryInterpreter,
)
from .semantic_memory_runtime import SemanticMemoryEngine


def raw_snapshot(store: MemoryCoreStore) -> list[dict[str, Any]]:
    """Stable raw-memory snapshot proving maintenance is non-destructive."""
    return [
        {
            "id": item.id,
            "content": item.content,
            "tags": item.tags,
            "created_at": item.created_at,
        }
        for item in store.all_memories()
    ]


def semantic_status(store: MemoryCoreStore) -> dict[str, Any]:
    payload = dict(store.semantic_summary())
    payload["db_path"] = str(store.db_path)
    return payload


def inspect_semantic(
    store: MemoryCoreStore,
    *,
    limit: int = 50,
    include_history: bool = True,
) -> dict[str, Any]:
    facts = store.semantic_facts(
        status=None if include_history else "active"
    )
    facts.sort(
        key=lambda item: (
            item.memory_id,
            item.ordinal,
            item.fact_id,
        ),
        reverse=True,
    )
    rows = []
    for fact in facts[: max(1, min(int(limit), 500))]:
        rows.append(
            {
                "fact_id": fact.fact_id,
                "memory_id": fact.memory_id,
                "subject": fact.projection.subject,
                "relation": fact.projection.relation,
                "value": fact.projection.value,
                "kind": fact.projection.kind,
                "qualifiers": fact.projection.qualifiers,
                "scope": fact.projection.scope,
                "cardinality": fact.projection.cardinality,
                "confidence": fact.projection.confidence,
                "status": fact.status,
                "provenance": fact.provenance,
                "parser_version": fact.parser_version,
                "created_at": fact.created_at,
            }
        )
    return {
        **semantic_status(store),
        "facts": rows,
    }


def reindex_semantic(
    store: MemoryCoreStore,
    interpreter: SemanticMemoryInterpreter,
    *,
    force: bool = False,
    retry_errors: bool = False,
    max_items: int = 100000,
    log=None,
) -> dict[str, Any]:
    """Rebuild/continue the sidecar while proving raw evidence is unchanged."""
    before_raw = raw_snapshot(store)
    before = semantic_status(store)

    if force:
        store.reset_semantic_index()
    elif retry_errors:
        store.reset_semantic_index(errors_only=True)

    engine = SemanticMemoryEngine(
        store,
        interpreter,
    )
    totals = {
        "indexed": 0,
        "unprojected": 0,
        "errors": 0,
    }
    remaining = max(1, min(int(max_items), 1_000_000))

    while remaining > 0:
        pending = store.pending_projection_items(
            engine.parser_version,
            limit=min(engine.batch_size, remaining),
        )
        if not pending:
            break

        outcome = engine.ensure_indexed(
            log=log,
            max_items=min(engine.batch_size, remaining),
        )
        for key in totals:
            totals[key] += int(outcome.get(key, 0))
        consumed = len(pending)
        remaining -= consumed

        # Failed batches are marked in sidecar state and must not spin.
        if (
            outcome.get("errors")
            and not outcome.get("indexed")
            and not outcome.get("unprojected")
        ):
            break

    after_raw = raw_snapshot(store)
    if before_raw != after_raw:
        raise RuntimeError("raw_memory_changed_during_semantic_reindex")

    return {
        "ok": True,
        "parser_version": engine.parser_version,
        "force": bool(force),
        "retry_errors": bool(retry_errors),
        "raw_memory_unchanged": True,
        "processed": totals,
        "before": before,
        "after": semantic_status(store),
    }


def _store(path: str) -> MemoryCoreStore:
    return MemoryCoreStore(
        Path(path).expanduser().resolve()
        if path
        else LOCAL_MEMORY.db_path
    )


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Semantic Memory V5 sidecar maintenance"
    )
    parser.add_argument(
        "--db",
        default="",
        help="Optional memory.sqlite3 path; defaults to Jarvis local memory.",
    )
    parser.add_argument(
        "--provider",
        default="auto",
        help="Semantic provider for reindex; auto is local-first.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional semantic model override for the primary provider.",
    )
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Explicitly permit raw memory projection through a cloud provider.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--limit", type=int, default=50)
    inspect.add_argument("--active-only", action="store_true")

    reindex = sub.add_parser("reindex")
    reindex.add_argument("--force", action="store_true")
    reindex.add_argument("--retry-errors", action="store_true")
    reindex.add_argument("--max-items", type=int, default=100000)

    args = parser.parse_args(argv)
    store = _store(args.db)

    if args.command == "status":
        _print(semantic_status(store))
        return 0

    if args.command == "inspect":
        _print(
            inspect_semantic(
                store,
                limit=args.limit,
                include_history=not args.active_only,
            )
        )
        return 0

    try:
        interpreter = ModelSemanticMemoryInterpreter(
            provider=args.provider,
            model=args.model,
            allow_cloud=args.allow_cloud,
        )
        payload = reindex_semantic(
            store,
            interpreter,
            force=args.force,
            retry_errors=args.retry_errors,
            max_items=args.max_items,
            log=lambda line: print(line),
        )
    except Exception as exc:
        _print(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "db_path": str(store.db_path),
            }
        )
        return 2
    _print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
