"""Provider-independent memory routing, before any model invocation.

Only user-authored declarative session facts are eligible as session evidence.
Connector resolvers are explicit, read-only dependency injections, never a web
search inferred from a personal question. UI/page text is never session memory.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .memory_retrieval import normalize, relevance, terms


_WRITE = re.compile(
    r"^(?:(?:ok|oui|d'accord|daccord|okay)[, ]*)?"
    r"(?:(?:je (?:veux|voudrais|souhaite) que tu|i (?:want|would like) you to)\s+)?"
    r"(?:s'il te plait[, ]*|please[, ]*)?"
    r"(?:memorise(?:z)?|retiens|retenez|souviens-toi|souvenez-vous|"
    r"(?:garde[z]?|conserve[z]?) (?:en (?:memoire|tete)|a l'esprit)|"
    r"remember|memorize|memorise|(?:save|keep) (?:in )?(?:memory|mind))"
    r"\s*(?:que\s+|that\s+|:\s*)?(.+)$", re.DOTALL)

_MEMORY_LIST = re.compile(
    r"^(?:(?:qu[' ]?est[- ]?ce que|quest[- ]?ce que|quesque|quoi|what)|"
    r"(?:montre|liste|affiche|show|list))"
    r".*\b(?:memoire|memory)\b"
)

_RECALL_CLARIFICATION_BLOCK = re.compile(
    r"^(?:ouvre|ouvrir|open|ferme|fermer|close|ecris|ecrire|write|"
    r"envoie|envoyer|send|recherche|chercher|search|lance|lancer|run)\b"
)


def _is_collection_query(value: str) -> bool:
    text = normalize(value)
    return bool(
        re.search(
            r"\b(?:quels|quelles|lesquels|lesquelles|mes|tous|toutes|"
            r"which|all)\b",
            text,
        )
        and re.search(
            r"\b(?:films?|projets?|reunions?|rendezvous|adresses?|"
            r"emails?|preferences?|souvenirs?|memoires?|"
            r"movies?|projects?|meetings?|addresses?|memories?)\b",
            text,
        )
    )


def _looks_like_recall_clarification(value: str) -> bool:
    text = normalize(value.strip())
    if not text or _RECALL_CLARIFICATION_BLOCK.match(text):
        return False
    informative = terms(value)
    return 0 < len(informative) <= 6


@dataclass(frozen=True)
class MemoryDecision:
    kind: str = "pass"
    content: str = ""
    reason: str = ""


class MemoryRouter:
    def decide(self, user_text: str) -> MemoryDecision:
        text = normalize(user_text.strip())
        match = _WRITE.match(text)
        if match:
            # Extract using the original text so spelling/case/accents persist.
            original = user_text.strip()
            offset = 0
            for index, char in enumerate(original):
                if offset >= match.start(1):
                    break
                offset += len(normalize(char))
            else:
                index = len(original)
            content = original[index:].lstrip(": ")
            if not content or normalize(content) in {"que", "that"} or text.endswith("?") or re.search(r"\b(?:puis|ensuite|then)\b", text):
                return MemoryDecision("clarify", reason="ambiguous_memory_write")
            return MemoryDecision("write", content, "explicit_user_write")
        if _MEMORY_LIST.match(text):
            return MemoryDecision("list", reason="explicit_memory_inspection")
        question = "?" in text or re.match(
            r"^(?:quel|quelle|quels|quelles|qui|quand|ou|what|which|who|when|where|"
            r"c'est quoi|qu'est[- ]?ce que|quest[- ]?ce que|quesque|"
            r"tu te (?:rappelles|souviens)|tu (?:sais|connais|peux me rappeler)|"
            r"(?:peux|pourrais)[ -]tu me (?:rappeler|dire)|dis-moi|donne-moi (?:le|la|les)|"
            r"rappelle-moi|do you remember|can you (?:remind|tell) me|te souviens)", text)
        personal = re.search(r"\b(?:mon|ma|mes|moi|je|j'|my|mine|i|me)\b", text)
        procedural = re.search(r"\b(?:comment|how|pourquoi|why)\b", text)
        reminder = re.match(r"^rappelle-moi\s+(?:de\s+\w+er\b|d'\w+er\b)", text)
        if question and personal and not procedural and not reminder:
            return MemoryDecision("recall", user_text, "personal_question")
        return MemoryDecision()


class MemoryRoutingRuntime:
    """Transparent runtime adapter. Enable only for the foundation test suite."""

    def __init__(self, delegate, tools, *, connector_resolver=None):
        self.delegate, self.tools = delegate, tools
        self.router = MemoryRouter()
        self.connector_resolver = connector_resolver
        self._session: list[str] = []
        self._pending_recall_query = ""

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def reset(self):
        self._session.clear()
        self._pending_recall_query = ""
        self.delegate.reset()

    def warm_up(self, *, log=None):
        self.delegate.warm_up(log=log)

    def _record(self, text):
        normalized = normalize(text)
        if (self.router.decide(text).kind == "pass" and "?" not in text
                and re.match(r"^(?:mon |ma |mes |je |j'|my |i )", normalized)
                and re.search(r"\b(?:est|s'appelle|ai|aime|prefere|is|have|like)\b", normalized)):
            self._session.append(text)
            self._session = self._session[-32:]

    def record_external_turn(self, user_text, assistant_text, **kwargs):
        self._record(user_text)
        method = getattr(self.delegate, "record_external_turn", None)
        if method:
            method(user_text, assistant_text, **kwargs)

    def run(self, user_text, *, log=None, phase=None):
        from .agent_runtime import AgentTurnResult

        begin = getattr(self.tools, "begin_turn", None)
        if begin:
            begin(user_text)
        decision = self.router.decide(user_text)
        if (
            decision.kind == "pass"
            and self._pending_recall_query
            and _looks_like_recall_clarification(user_text)
        ):
            decision = MemoryDecision(
                "recall",
                f"{self._pending_recall_query} {user_text}",
                "recall_clarification",
            )
        if log:
            log(f"[MEMORY_ROUTER] route={decision.kind} reason={decision.reason}")
        if decision.kind == "pass":
            self._pending_recall_query = ""
            result = self.delegate.run(user_text, log=log, phase=phase)
            self._record(user_text)
            return result
        actions = []
        if phase:
            phase("acting")
        if decision.kind == "clarify":
            reply = "Quelle information exacte souhaitez-vous mémoriser ?"
        elif decision.kind == "list":
            result = self.tools.execute(
                "list_memory_information",
                {"limit": 20},
            )
            actions.append(result)
            try:
                data = json.loads(result.detail) if result.success else []
                candidates = [
                    str(row["content"])
                    for row in data
                    if isinstance(row, dict) and row.get("content")
                ]
            except (ValueError, TypeError, KeyError):
                candidates = []
            if candidates:
                reply = (
                    "Dans ma mémoire locale, j'ai notamment : "
                    + " ; ".join(candidates)
                )
            else:
                reply = "Je n'ai aucune information dans la mémoire locale."
            self._pending_recall_query = ""
        elif decision.kind == "write":
            result = self.tools.execute("remember_information", {"content": decision.content})
            actions.append(result)
            reply = result.message if result.success else "La mémorisation a échoué : " + result.message
            if result.success:
                self._session.append(decision.content)
                self._session = self._session[-32:]
            self._pending_recall_query = ""
        else:
            recall_query = decision.content or user_text
            candidates = [
                fact
                for fact in reversed(self._session)
                if relevance(recall_query, fact) >= 0.85
            ]
            source = "session"
            if not candidates:
                source = "persistent"
                result = self.tools.execute(
                    "recall_information",
                    {"query": recall_query},
                )
                actions.append(result)
                try:
                    data = json.loads(result.detail) if result.success else []
                    candidates = [str(row["content"]) for row in data if isinstance(row, dict)]
                except (ValueError, TypeError, KeyError):
                    candidates = []
                if not result.success:
                    reply = "La mémoire persistante est indisponible : " + result.message
                    self.record_external_turn(user_text, reply)
                    return AgentTurnResult(text=reply, actions=tuple(actions))
            if not candidates and self.connector_resolver:
                source = "connector"
                try:
                    candidates = [
                        str(fact)
                        for fact in self.connector_resolver(recall_query)
                        if relevance(recall_query, str(fact)) >= 0.85
                    ]
                except Exception:
                    source = "connector_unavailable"
            candidates = list(dict.fromkeys(candidates))
            if not terms(recall_query) or not candidates:
                reply = "Je n'ai pas cette information. Pouvez-vous me la préciser ?"
                self._pending_recall_query = recall_query
            elif len(candidates) > 1 and _is_collection_query(recall_query):
                reply = (
                    "Informations correspondantes : "
                    + " ; ".join(candidates[:8])
                )
                self._pending_recall_query = ""
            elif len(candidates) > 1:
                reply = (
                    "Plusieurs informations correspondent : "
                    + " ; ".join(candidates[:3])
                    + ". Laquelle voulez-vous préciser ?"
                )
                self._pending_recall_query = recall_query
            else:
                reply = candidates[0]
                self._pending_recall_query = ""
            if log:
                log(f"[MEMORY_ROUTER] source={source} hits={len(candidates)}")
        self.record_external_turn(user_text, reply)
        return AgentTurnResult(text=reply, actions=tuple(actions))
