from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone

from jarvis_agent.memory_semantic_interpreter import (
    ModelSemanticMemoryInterpreter,
)
from jarvis_agent.semantic_memory import semantic_key_similarity


CASES = [
    {
        "name": "preference_en",
        "memory": "My preferred code editor is Cursor.",
        "query": "Which code editor do I prefer?",
        "mode": "single",
    },
    {
        "name": "collection_fr",
        "memory": "Je veux regarder Inception et Gladiator.",
        "query": "Quels films est-ce que je veux regarder ?",
        "mode": "collection",
    },
    {
        "name": "date_fr",
        "memory": "J'ai rendez-vous avec le médecin le 09/10/2026.",
        "query": "Qu'est-ce que j'ai prévu le 09/10/2026 ?",
        "mode": "single",
        "exact": "09/10/2026",
    },
    {
        "name": "project_fr",
        "memory": "Pour le projet Atlas, la date limite est le 1 novembre 2026.",
        "query": "Quelle est la deadline du projet Atlas ?",
        "mode": "single",
    },
    {
        "name": "preference_ar",
        "memory": "لغتي المفضلة هي الفرنسية.",
        "query": "ما هي لغتي المفضلة؟",
        "mode": "single",
    },
    {
        "name": "entity_context_en",
        "memory": "Alice owns Project North while Bob owns Project South.",
        "query": "Who owns Project North?",
        "mode": "single",
        "entity": "Project North",
    },
    {
        "name": "relative_time_projection_en",
        "memory": "I have a doctor appointment tomorrow.",
        "created_at": "2026-10-08T12:00:00+00:00",
        "query": "What appointment do I have on 2026-10-09?",
        "mode": "single",
        "projection_date": "2026-10-09",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--model", default="")
    parser.add_argument("--threshold", type=float, default=0.48)
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Explicitly permit sending the synthetic acceptance corpus to a cloud provider.",
    )
    args = parser.parse_args()

    interpreter = ModelSemanticMemoryInterpreter(
        provider=args.provider,
        model=args.model,
        allow_cloud=args.allow_cloud,
    )

    start = time.perf_counter()
    projected = interpreter.project_batch(
        [
            (
                index + 1,
                case["memory"],
                case.get("created_at", ""),
            )
            for index, case in enumerate(CASES)
        ]
    )
    projection_seconds = time.perf_counter() - start

    failures = []
    results = []

    for index, case in enumerate(CASES):
        facts = projected.get(index + 1, ())
        t0 = time.perf_counter()
        turn = interpreter.interpret_turn(case["query"])
        query_seconds = time.perf_counter() - t0

        similarities = []
        if turn.query is not None:
            similarities = [
                semantic_key_similarity(
                    turn.query.relation,
                    fact.relation,
                )
                for fact in facts
            ]
        best = max(similarities or [0.0])

        row = {
            "name": case["name"],
            "projected_relations": [
                fact.relation for fact in facts
            ],
            "projected_values": [
                fact.value for fact in facts
            ],
            "operation": turn.operation,
            "query_relation": (
                turn.query.relation
                if turn.query is not None
                else ""
            ),
            "answer_mode": (
                turn.query.answer_mode
                if turn.query is not None
                else ""
            ),
            "query_entities": (
                list(turn.query.entities)
                if turn.query is not None
                else []
            ),
            "projected_entities": [
                entity
                for fact in facts
                for entity in fact.entities
            ],
            "relation_similarity": round(best, 4),
            "query_seconds": round(query_seconds, 3),
        }

        if turn.operation != "recall":
            failures.append(
                f"{case['name']}: expected recall, got {turn.operation}"
            )
        if not facts:
            failures.append(
                f"{case['name']}: no semantic facts projected"
            )
        if best < args.threshold:
            failures.append(
                f"{case['name']}: relation similarity {best:.3f} "
                f"< {args.threshold:.3f}"
            )
        if (
            turn.query is not None
            and turn.query.answer_mode != case["mode"]
        ):
            failures.append(
                f"{case['name']}: expected mode {case['mode']}, "
                f"got {turn.query.answer_mode}"
            )
        expected_projection_date = case.get("projection_date")
        if expected_projection_date:
            projection_dates = {
                fact.qualifiers.get("date")
                or fact.qualifiers.get("datetime")
                or fact.qualifiers.get("start_at")
                for fact in facts
            }
            if not any(
                str(value or "").startswith(expected_projection_date)
                for value in projection_dates
            ):
                failures.append(
                    f"{case['name']}: relative memory time was not "
                    f"resolved against created_at"
                )

        expected_entity = case.get("entity")
        if expected_entity and turn.query is not None:
            query_entities = {
                item.casefold()
                for item in turn.query.entities
            }
            projected_entities = {
                item.casefold()
                for fact in facts
                for item in fact.entities
            }
            if expected_entity.casefold() not in query_entities:
                failures.append(
                    f"{case['name']}: query entity not preserved"
                )
            if expected_entity.casefold() not in projected_entities:
                failures.append(
                    f"{case['name']}: projection entity not preserved"
                )

        exact = case.get("exact")
        if exact and turn.query is not None:
            combined = list(turn.query.exact_terms)
            combined.extend(turn.query.qualifiers.values())
            if not any(exact in str(value) for value in combined):
                failures.append(
                    f"{case['name']}: exact date not preserved"
                )

        results.append(row)

    pass_checks = [
        "What is the capital of Japan?",
        "Ouvre Chrome et va sur YouTube.",
    ]
    for text in pass_checks:
        t0 = time.perf_counter()
        turn = interpreter.interpret_turn(text)
        elapsed = time.perf_counter() - t0
        results.append(
            {
                "name": "pass_guard",
                "text": text,
                "operation": turn.operation,
                "session_facts": len(turn.session_facts),
                "query_seconds": round(elapsed, 3),
            }
        )
        if turn.operation != "pass":
            failures.append(
                f"pass_guard: {text!r} -> {turn.operation}"
            )

    session_statement = (
        "Je travaille actuellement sur un projet appelé Atlas Nova."
    )
    session_question = (
        "Comment s'appelle le projet sur lequel je travaille ?"
    )
    statement = interpreter.interpret_turn(session_statement)
    question = interpreter.interpret_turn(session_question)
    session_best = 0.0
    if question.query is not None:
        session_best = max(
            (
                semantic_key_similarity(
                    question.query.relation,
                    fact.relation,
                )
                for fact in statement.session_facts
            ),
            default=0.0,
        )
    results.append(
        {
            "name": "session_context",
            "statement_operation": statement.operation,
            "session_relations": [
                fact.relation
                for fact in statement.session_facts
            ],
            "question_operation": question.operation,
            "question_relation": (
                question.query.relation
                if question.query is not None
                else ""
            ),
            "relation_similarity": round(session_best, 4),
        }
    )
    if statement.operation != "pass" or not statement.session_facts:
        failures.append(
            "session_context: declarative statement not captured as session fact"
        )
    if question.operation != "recall":
        failures.append(
            "session_context: follow-up question not classified as recall"
        )
    if session_best < args.threshold:
        failures.append(
            "session_context: semantic relation mismatch"
        )

    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    relative_query = interpreter.interpret_turn(
        "What personal event do I have tomorrow?"
    )
    relative_qualifiers = (
        relative_query.query.qualifiers
        if relative_query.query is not None
        else {}
    )
    relative_values = {
        str(value)
        for value in relative_qualifiers.values()
    }
    results.append(
        {
            "name": "relative_query_time",
            "operation": relative_query.operation,
            "qualifiers": relative_qualifiers,
            "expected_date": tomorrow,
        }
    )
    if relative_query.operation != "recall":
        failures.append(
            "relative_query_time: expected recall"
        )
    if not any(value.startswith(tomorrow) for value in relative_values):
        failures.append(
            "relative_query_time: tomorrow was not resolved against "
            "reference_time_utc"
        )

    payload = {
        "ok": not failures,
        "provider": interpreter.provider,
        "model": interpreter.model,
        "projection_seconds": round(projection_seconds, 3),
        "results": results,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
