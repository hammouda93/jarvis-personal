from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _knowledge_root() -> Path:
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        root = Path(local) / "JarvisPersonal"
    else:
        root = Path.home() / ".jarvis_personal"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _default_db_path() -> Path:
    return _knowledge_root() / "agent_knowledge.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _normalize(value: str) -> str:
    text = (value or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9à-ÿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> set[str]:
    stop = {
        "de", "du", "des", "la", "le", "les", "un", "une", "et", "ou",
        "dans", "sur", "avec", "pour", "par", "au", "aux", "en", "à", "a",
        "the", "a", "an", "of", "to", "in", "on", "with", "for", "and",
        "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
    }
    return {
        token
        for token in _normalize(value).split()
        if len(token) >= 3 and token not in stop
    }


def _redact(value: str) -> str:
    """Remove common secrets/identifiers from reusable operational knowledge."""
    text = str(value or "")
    text = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "<email>",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:api[_ -]?key|password|mot de passe|token|secret)"
        r"\s*[:=]\s*[^\s,;]+",
        "<secret>",
        text,
    )
    text = re.sub(
        r"(?i)C:\\Users\\[^\\\s]+",
        r"%USERPROFILE%",
        text,
    )
    text = re.sub(r"(?<!\d)\+?\d[\d .()-]{7,}\d(?!\d)", "<number>", text)
    return text.strip()


def _clean_list(values: list[Any] | tuple[Any, ...] | None, *, limit: int = 24) -> list[str]:
    result: list[str] = []
    for raw in list(values or [])[:limit]:
        text = _redact(str(raw)).strip()
        if text and text not in result:
            result.append(text[:700])
    return result


@dataclass(frozen=True)
class Skill:
    id: int
    name: str
    goal: str
    app_scope: str
    procedure: tuple[str, ...]
    success_checks: tuple[str, ...]
    failure_patterns: tuple[str, ...]
    confidence: float
    success_count: int
    failure_count: int
    version: int
    source: str
    created_at: str
    updated_at: str
    last_used_at: str


@dataclass(frozen=True)
class Lesson:
    id: int
    scope: str
    pattern: str
    rule: str
    confidence: float
    evidence_count: int
    source: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AppProfile:
    id: int
    app_key: str
    display_name: str
    aliases: tuple[str, ...]
    launch_hint: str
    window_title_patterns: tuple[str, ...]
    observed_capabilities: tuple[str, ...]
    confidence: float
    success_count: int
    failure_count: int
    created_at: str
    updated_at: str
    last_seen_at: str


@dataclass(frozen=True)
class SkillRun:
    id: int
    skill_name: str
    goal: str
    status: str
    actions: tuple[str, ...]
    proof: dict[str, Any]
    error: str
    created_at: str
    finished_at: str


class AgentKnowledgeStore:
    """Local persistent operational knowledge for Jarvis.

    This store is intentionally separate from personal memory. It holds reusable
    procedures, behavioral lessons, machine-local application profiles and
    execution proofs. GPT-OSS can be replaced without losing this knowledge.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    goal TEXT NOT NULL,
                    app_scope TEXT NOT NULL DEFAULT '',
                    procedure_json TEXT NOT NULL DEFAULT '[]',
                    success_checks_json TEXT NOT NULL DEFAULT '[]',
                    failure_patterns_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'learned',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL DEFAULT 'global',
                    pattern TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    evidence_count INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'feedback',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope, pattern, rule)
                );

                CREATE TABLE IF NOT EXISTS app_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    launch_hint TEXT NOT NULL DEFAULT '',
                    window_title_patterns_json TEXT NOT NULL DEFAULT '[]',
                    observed_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.6,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS skill_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL DEFAULT '',
                    goal TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    proof_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_skills_active
                    ON skills(active, updated_at);
                CREATE INDEX IF NOT EXISTS idx_lessons_active
                    ON lessons(active, updated_at);
                CREATE INDEX IF NOT EXISTS idx_profiles_seen
                    ON app_profiles(last_seen_at);
                CREATE INDEX IF NOT EXISTS idx_runs_finished
                    ON skill_runs(finished_at);
                """
            )

    @staticmethod
    def _clamp_confidence(value: float) -> float:
        return max(0.0, min(float(value), 1.0))

    def upsert_skill(
        self,
        *,
        name: str,
        goal: str,
        procedure: list[str],
        success_checks: list[str] | None = None,
        failure_patterns: list[str] | None = None,
        app_scope: str = "",
        confidence: float = 0.65,
        source: str = "learned",
    ) -> Skill:
        clean_name = _normalize(name).replace(" ", "_")[:100]
        clean_goal = _redact(goal)[:500]
        if len(clean_name) < 3 or len(clean_goal) < 4:
            raise ValueError("skill name/goal is too vague")

        steps = _clean_list(procedure)
        if not steps:
            raise ValueError("skill procedure is empty")
        checks = _clean_list(success_checks)
        failures = _clean_list(failure_patterns)
        scope = _redact(app_scope)[:120]
        now = _now()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE name = ?",
                (clean_name,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO skills(
                        name, goal, app_scope, procedure_json,
                        success_checks_json, failure_patterns_json,
                        confidence, source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_name,
                        clean_goal,
                        scope,
                        _json(steps),
                        _json(checks),
                        _json(failures),
                        self._clamp_confidence(confidence),
                        source[:40],
                        now,
                        now,
                    ),
                )
                skill_id = int(cursor.lastrowid)
            else:
                skill_id = int(row["id"])
                version = int(row["version"]) + 1
                merged_steps = _clean_list([
                    *_load_json(str(row["procedure_json"]), []),
                    *steps,
                ])
                merged_checks = _clean_list([
                    *_load_json(str(row["success_checks_json"]), []),
                    *checks,
                ])
                merged_failures = _clean_list([
                    *_load_json(str(row["failure_patterns_json"]), []),
                    *failures,
                ])
                new_confidence = max(
                    float(row["confidence"]),
                    self._clamp_confidence(confidence),
                )
                conn.execute(
                    """
                    UPDATE skills
                    SET goal = ?, app_scope = ?, procedure_json = ?,
                        success_checks_json = ?, failure_patterns_json = ?,
                        confidence = ?, version = ?, source = ?,
                        updated_at = ?, active = 1
                    WHERE id = ?
                    """,
                    (
                        clean_goal,
                        scope or str(row["app_scope"]),
                        _json(merged_steps),
                        _json(merged_checks),
                        _json(merged_failures),
                        new_confidence,
                        version,
                        source[:40],
                        now,
                        skill_id,
                    ),
                )

        return self.get_skill(clean_name)

    def get_skill(self, name: str) -> Skill:
        key = _normalize(name).replace(" ", "_")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE name = ? AND active = 1",
                (key,),
            ).fetchone()
        if row is None:
            raise KeyError(name)
        return self._skill_from_row(row)

    def record_lesson(
        self,
        *,
        pattern: str,
        rule: str,
        scope: str = "global",
        confidence: float = 0.8,
        source: str = "feedback",
    ) -> Lesson:
        clean_scope = _normalize(scope)[:100] or "global"
        clean_pattern = _redact(pattern)[:500]
        clean_rule = _redact(rule)[:700]
        if len(clean_pattern) < 4 or len(clean_rule) < 8:
            raise ValueError("lesson is too vague")
        now = _now()

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM lessons
                WHERE scope = ? AND pattern = ? AND rule = ?
                """,
                (clean_scope, clean_pattern, clean_rule),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO lessons(
                        scope, pattern, rule, confidence, evidence_count,
                        source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        clean_scope,
                        clean_pattern,
                        clean_rule,
                        self._clamp_confidence(confidence),
                        source[:40],
                        now,
                        now,
                    ),
                )
                lesson_id = int(cursor.lastrowid)
            else:
                lesson_id = int(row["id"])
                evidence = int(row["evidence_count"]) + 1
                confidence_value = min(
                    0.99,
                    max(
                        float(row["confidence"]),
                        self._clamp_confidence(confidence),
                    ) + min(0.03, evidence * 0.002),
                )
                conn.execute(
                    """
                    UPDATE lessons
                    SET evidence_count = ?, confidence = ?, source = ?,
                        updated_at = ?, active = 1
                    WHERE id = ?
                    """,
                    (
                        evidence,
                        confidence_value,
                        source[:40],
                        now,
                        lesson_id,
                    ),
                )

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lessons WHERE id = ?",
                (lesson_id,),
            ).fetchone()
        assert row is not None
        return self._lesson_from_row(row)

    def upsert_app_profile(
        self,
        *,
        display_name: str,
        aliases: list[str] | None = None,
        launch_hint: str = "",
        window_title_patterns: list[str] | None = None,
        observed_capabilities: list[str] | None = None,
        success: bool | None = None,
        confidence: float = 0.65,
    ) -> AppProfile:
        display = _redact(display_name)[:160].strip()
        app_key = _normalize(display).replace(" ", "_")[:120]
        if len(app_key) < 2:
            raise ValueError("application name is too vague")
        now = _now()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_profiles WHERE app_key = ?",
                (app_key,),
            ).fetchone()

            old_aliases = [] if row is None else _load_json(str(row["aliases_json"]), [])
            old_titles = [] if row is None else _load_json(
                str(row["window_title_patterns_json"]), []
            )
            old_caps = [] if row is None else _load_json(
                str(row["observed_capabilities_json"]), []
            )
            merged_aliases = _clean_list([*old_aliases, *(aliases or []), display])
            merged_titles = _clean_list([*old_titles, *(window_title_patterns or [])])
            merged_caps = _clean_list([*old_caps, *(observed_capabilities or [])])

            if row is None:
                successes = 1 if success is True else 0
                failures = 1 if success is False else 0
                cursor = conn.execute(
                    """
                    INSERT INTO app_profiles(
                        app_key, display_name, aliases_json, launch_hint,
                        window_title_patterns_json, observed_capabilities_json,
                        confidence, success_count, failure_count,
                        created_at, updated_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        app_key,
                        display,
                        _json(merged_aliases),
                        _redact(launch_hint)[:500],
                        _json(merged_titles),
                        _json(merged_caps),
                        self._clamp_confidence(confidence),
                        successes,
                        failures,
                        now,
                        now,
                        now,
                    ),
                )
                profile_id = int(cursor.lastrowid)
            else:
                profile_id = int(row["id"])
                successes = int(row["success_count"]) + (1 if success is True else 0)
                failures = int(row["failure_count"]) + (1 if success is False else 0)
                launch = _redact(launch_hint)[:500] or str(row["launch_hint"])
                conn.execute(
                    """
                    UPDATE app_profiles
                    SET display_name = ?, aliases_json = ?, launch_hint = ?,
                        window_title_patterns_json = ?,
                        observed_capabilities_json = ?, confidence = ?,
                        success_count = ?, failure_count = ?,
                        updated_at = ?, last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        display,
                        _json(merged_aliases),
                        launch,
                        _json(merged_titles),
                        _json(merged_caps),
                        max(
                            float(row["confidence"]),
                            self._clamp_confidence(confidence),
                        ),
                        successes,
                        failures,
                        now,
                        now,
                        profile_id,
                    ),
                )

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        assert row is not None
        return self._profile_from_row(row)

    def record_run(
        self,
        *,
        status: str,
        actions: list[str],
        proof: dict[str, Any] | None = None,
        skill_name: str = "",
        goal: str = "",
        error: str = "",
    ) -> SkillRun:
        normalized_status = _normalize(status)
        if normalized_status not in {"verified", "failed", "partial", "cancelled"}:
            normalized_status = "partial"
        now = _now()
        safe_actions = _clean_list(actions, limit=50)
        safe_goal = _redact(goal)[:500]
        safe_error = _redact(error)[:800]
        safe_proof = self._sanitize_json(proof or {})

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO skill_runs(
                    skill_name, goal, status, actions_json, proof_json,
                    error, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _normalize(skill_name).replace(" ", "_")[:100],
                    safe_goal,
                    normalized_status,
                    _json(safe_actions),
                    _json(safe_proof),
                    safe_error,
                    now,
                    now,
                ),
            )
            run_id = int(cursor.lastrowid)

            if skill_name:
                key = _normalize(skill_name).replace(" ", "_")
                column = "success_count" if normalized_status == "verified" else "failure_count"
                conn.execute(
                    f"""
                    UPDATE skills
                    SET {column} = {column} + 1,
                        last_used_at = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (now, now, key),
                )

        return SkillRun(
            id=run_id,
            skill_name=_normalize(skill_name).replace(" ", "_")[:100],
            goal=safe_goal,
            status=normalized_status,
            actions=tuple(safe_actions),
            proof=safe_proof,
            error=safe_error,
            created_at=now,
            finished_at=now,
        )

    def relevant_context(self, query: str, *, limit: int = 4) -> dict[str, Any]:
        wanted = _tokens(query)
        if not wanted:
            return {"skills": [], "lessons": [], "app_profiles": []}

        with self._connect() as conn:
            skill_rows = conn.execute(
                "SELECT * FROM skills WHERE active = 1 ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
            lesson_rows = conn.execute(
                "SELECT * FROM lessons WHERE active = 1 ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
            profile_rows = conn.execute(
                "SELECT * FROM app_profiles ORDER BY last_seen_at DESC LIMIT 100"
            ).fetchall()

        def score(text: str, confidence: float) -> float:
            values = _tokens(text)
            if not values:
                return 0.0
            overlap = len(wanted & values)
            if not overlap:
                return 0.0
            coverage = overlap / max(1, len(wanted))
            specificity = overlap / max(1, len(values))
            return coverage * 0.65 + specificity * 0.2 + confidence * 0.15

        skills: list[tuple[float, Skill]] = []
        for row in skill_rows:
            item = self._skill_from_row(row)
            haystack = " ".join(
                [
                    item.name,
                    item.goal,
                    item.app_scope,
                    *item.procedure,
                    *item.failure_patterns,
                ]
            )
            value = score(haystack, item.confidence)
            if value > 0:
                skills.append((value, item))

        lessons: list[tuple[float, Lesson]] = []
        for row in lesson_rows:
            item = self._lesson_from_row(row)
            value = score(
                f"{item.scope} {item.pattern} {item.rule}",
                item.confidence,
            )
            if value > 0:
                lessons.append((value, item))

        profiles: list[tuple[float, AppProfile]] = []
        for row in profile_rows:
            item = self._profile_from_row(row)
            haystack = " ".join(
                [
                    item.display_name,
                    *item.aliases,
                    *item.window_title_patterns,
                    *item.observed_capabilities,
                ]
            )
            value = score(haystack, item.confidence)
            if value > 0:
                profiles.append((value, item))

        skills.sort(key=lambda pair: -pair[0])
        lessons.sort(key=lambda pair: -pair[0])
        profiles.sort(key=lambda pair: -pair[0])
        capped = max(1, min(int(limit), 8))
        return {
            "skills": [self.skill_to_dict(item) for _, item in skills[:capped]],
            "lessons": [self.lesson_to_dict(item) for _, item in lessons[:capped]],
            "app_profiles": [
                self.profile_to_dict(item) for _, item in profiles[:capped]
            ],
        }

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                "skills": int(conn.execute(
                    "SELECT COUNT(*) FROM skills WHERE active = 1"
                ).fetchone()[0]),
                "lessons": int(conn.execute(
                    "SELECT COUNT(*) FROM lessons WHERE active = 1"
                ).fetchone()[0]),
                "app_profiles": int(conn.execute(
                    "SELECT COUNT(*) FROM app_profiles"
                ).fetchone()[0]),
                "skill_runs": int(conn.execute(
                    "SELECT COUNT(*) FROM skill_runs"
                ).fetchone()[0]),
            }

    def export_snapshot(self, path: Path, *, anonymize: bool = True) -> Path:
        with self._connect() as conn:
            skills = [
                self.skill_to_dict(self._skill_from_row(row))
                for row in conn.execute(
                    "SELECT * FROM skills WHERE active = 1 ORDER BY id"
                ).fetchall()
            ]
            lessons = [
                self.lesson_to_dict(self._lesson_from_row(row))
                for row in conn.execute(
                    "SELECT * FROM lessons WHERE active = 1 ORDER BY id"
                ).fetchall()
            ]
            profiles = [
                self.profile_to_dict(self._profile_from_row(row))
                for row in conn.execute(
                    "SELECT * FROM app_profiles ORDER BY id"
                ).fetchall()
            ]
            runs = [
                self.run_to_dict(self._run_from_row(row))
                for row in conn.execute(
                    "SELECT * FROM skill_runs ORDER BY id DESC LIMIT 250"
                ).fetchall()
            ]

        payload: dict[str, Any] = {
            "schema": 1,
            "exported_at": _now(),
            "stats": self.stats(),
            "skills": skills,
            "lessons": lessons,
            "app_profiles": profiles,
            "recent_runs": runs,
        }
        if anonymize:
            payload = self._sanitize_json(payload)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @classmethod
    def _sanitize_json(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_json(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._sanitize_json(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize_json(item) for item in value]
        if isinstance(value, str):
            return _redact(value)
        return value

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> Skill:
        return Skill(
            id=int(row["id"]),
            name=str(row["name"]),
            goal=str(row["goal"]),
            app_scope=str(row["app_scope"]),
            procedure=tuple(_load_json(str(row["procedure_json"]), [])),
            success_checks=tuple(
                _load_json(str(row["success_checks_json"]), [])
            ),
            failure_patterns=tuple(
                _load_json(str(row["failure_patterns_json"]), [])
            ),
            confidence=float(row["confidence"]),
            success_count=int(row["success_count"]),
            failure_count=int(row["failure_count"]),
            version=int(row["version"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_used_at=str(row["last_used_at"]),
        )

    @staticmethod
    def _lesson_from_row(row: sqlite3.Row) -> Lesson:
        return Lesson(
            id=int(row["id"]),
            scope=str(row["scope"]),
            pattern=str(row["pattern"]),
            rule=str(row["rule"]),
            confidence=float(row["confidence"]),
            evidence_count=int(row["evidence_count"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> AppProfile:
        return AppProfile(
            id=int(row["id"]),
            app_key=str(row["app_key"]),
            display_name=str(row["display_name"]),
            aliases=tuple(_load_json(str(row["aliases_json"]), [])),
            launch_hint=str(row["launch_hint"]),
            window_title_patterns=tuple(
                _load_json(str(row["window_title_patterns_json"]), [])
            ),
            observed_capabilities=tuple(
                _load_json(str(row["observed_capabilities_json"]), [])
            ),
            confidence=float(row["confidence"]),
            success_count=int(row["success_count"]),
            failure_count=int(row["failure_count"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_seen_at=str(row["last_seen_at"]),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> SkillRun:
        return SkillRun(
            id=int(row["id"]),
            skill_name=str(row["skill_name"]),
            goal=str(row["goal"]),
            status=str(row["status"]),
            actions=tuple(_load_json(str(row["actions_json"]), [])),
            proof=dict(_load_json(str(row["proof_json"]), {})),
            error=str(row["error"]),
            created_at=str(row["created_at"]),
            finished_at=str(row["finished_at"]),
        )

    @staticmethod
    def skill_to_dict(item: Skill) -> dict[str, Any]:
        value = asdict(item)
        value["procedure"] = list(item.procedure)
        value["success_checks"] = list(item.success_checks)
        value["failure_patterns"] = list(item.failure_patterns)
        return value

    @staticmethod
    def lesson_to_dict(item: Lesson) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def profile_to_dict(item: AppProfile) -> dict[str, Any]:
        value = asdict(item)
        value["aliases"] = list(item.aliases)
        value["window_title_patterns"] = list(item.window_title_patterns)
        value["observed_capabilities"] = list(item.observed_capabilities)
        return value

    @staticmethod
    def run_to_dict(item: SkillRun) -> dict[str, Any]:
        value = asdict(item)
        value["actions"] = list(item.actions)
        return value


AGENT_KNOWLEDGE = AgentKnowledgeStore()
