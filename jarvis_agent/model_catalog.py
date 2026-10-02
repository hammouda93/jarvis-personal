from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .model_router import ModelCandidate


@dataclass(frozen=True)
class CatalogEntry:
    candidate: ModelCandidate
    configured: bool
    purpose: str


class CurrentModelCatalog:
    """Describe Jarvis' current model options without changing live routing."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def entries(self) -> list[CatalogEntry]:
        s = self.settings
        entries = [
            CatalogEntry(
                candidate=ModelCandidate(
                    provider="cerebras",
                    model=s.cerebras_agent_model,
                    task_tags=(
                        "general",
                        "reasoning",
                        "windows",
                        "browser",
                        "tools",
                    ),
                    local=False,
                ),
                configured=bool(s.cerebras_api_key),
                purpose="Primary GPT-OSS tool/reasoning provider.",
            ),
            CatalogEntry(
                candidate=ModelCandidate(
                    provider="groq",
                    model=s.groq_agent_model,
                    task_tags=(
                        "general",
                        "reasoning",
                        "windows",
                        "browser",
                        "tools",
                    ),
                    local=False,
                ),
                configured=bool(s.groq_api_key),
                purpose="GPT-OSS fallback/provider for tool reasoning.",
            ),
            CatalogEntry(
                candidate=ModelCandidate(
                    provider="ollama",
                    model=s.ollama_agent_model,
                    task_tags=(
                        "general",
                        "local",
                        "lightweight",
                    ),
                    local=True,
                    max_context_tokens=s.ollama_agent_num_ctx,
                ),
                configured=True,
                purpose="Local agent model option.",
            ),
            CatalogEntry(
                candidate=ModelCandidate(
                    provider="ollama",
                    model=s.vision_model,
                    task_tags=(
                        "vision",
                        "computer_vision",
                        "local",
                    ),
                    local=True,
                ),
                configured=bool(s.vision_enabled),
                purpose="Local screen-vision sensor model.",
            ),
            CatalogEntry(
                candidate=ModelCandidate(
                    provider="openai",
                    model=s.openai_agent_model,
                    task_tags=(
                        "general",
                        "reasoning",
                        "code",
                        "research",
                    ),
                    local=False,
                ),
                configured=bool(s.openai_api_key),
                purpose="Optional future provider; not selected by current live path.",
            ),
        ]
        return entries

    def configured_candidates(
        self,
        *,
        include_vision: bool = True,
    ) -> list[ModelCandidate]:
        result: list[ModelCandidate] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for entry in self.entries():
            if not entry.configured:
                continue
            if (
                not include_vision
                and "vision" in set(entry.candidate.task_tags)
            ):
                continue
            key = (
                entry.candidate.provider,
                entry.candidate.model,
                tuple(entry.candidate.task_tags),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(entry.candidate)
        return result
