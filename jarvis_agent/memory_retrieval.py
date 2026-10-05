"""Deterministic retrieval over the existing SQLite store; no schema migration.

All informative query terms must match. Fuzzy matching only repairs long-word
typos; it never relaxes an unknown entity into an unrelated personal fact.
"""
from __future__ import annotations

import re
import unicodedata
from contextlib import closing
from difflib import SequenceMatcher


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c)).replace("’", "'")


_STOP = set("""quel quelle quels quelles que quoi qui est sont etait etais ce cet
cette ces de du des le la les un une mon ma mes ton ta tes son sa ses notre nos
votre vos leur leurs je j tu il elle nous vous ils elles sur dans avec pour et ou
au aux me te se moi toi ai as a en ne pas nom prenom appelle appele s appelle
comment rappelle rappelles rappeler souviens souvenir souviens-toi tu sais dis
dit dire peux peut pourrais besoin information informations donne avais deja
memorise retenu aime aimes aimer prefere preferee preferees preferes favori
favorite favoris favorites bien encore maintenant what which the of about my
your i you is are was name called tell do does did remember recall favorite
favourite like liked love have has that it to in please know said saved
prochain prochaine prochaines prochains next prevu prevue scheduled quand when
where memorisee memorises memorisees memoriser dont parle parler parlee parlees
demande demandes demander demandee demandees asked ask mentionne mentionner
mentionnee mentionnees evoque evoquer evoquee evoquees quesque questce qu
faire fais dois doit veux veut voudrais voudrait souhaite souhaites souhaitent
want wants would""".split())
_STOP.update({"connais", "connait", "sait", "pourrais", "can", "remind", "donne", "reminds"})
_CONCEPTS = {
    "movie": "film", "movies": "film", "films": "film",
    "meeting": "reunion", "meetings": "reunion", "reunions": "reunion",
    "rendezvous": "reunion", "rdv": "reunion", "friday": "vendredi",
    "adresse": "address", "courriel": "email", "mail": "email",
    "travail": "work", "job": "work", "societe": "entreprise",
}


def terms(value: str) -> tuple[str, ...]:
    text = normalize(value).replace("rendez-vous", "rendezvous")
    return tuple(dict.fromkeys(_CONCEPTS.get(t, t) for t in re.findall(r"\w+", text)
                              if t not in _STOP and (len(t) > 1 or t.isdigit())))


def content_key(value: str) -> str:
    """Canonical text identity for duplicate suppression, not semantic merging."""
    return " ".join(re.findall(r"\w+", normalize(value)))


def _segments(content: str) -> tuple[str, ...]:
    """Semantic-ish clauses for legacy free-text memories.

    Query terms must co-occur in one clause. This prevents an old compound note
    such as "Films à regarder: ... ; Tests: ..." from falsely satisfying
    "film test" just because the two words exist in unrelated clauses.
    """
    pieces = [
        part.strip()
        for part in re.split(r"[;\n\r|]+", str(content or ""))
        if part.strip()
    ]
    return tuple(pieces) or (str(content or ""),)


def segmented_relevance(
    query: str,
    content: str,
    tags: str = "",
) -> float:
    return max(
        (
            relevance(query, segment, tags)
            for segment in _segments(content)
        ),
        default=0.0,
    )


def relevance(query: str, content: str, tags: str = "") -> float:
    wanted, actual = terms(query), terms(content + " " + tags)
    if not wanted or not actual:
        return 0.0
    scores = []
    for term in wanted:
        if term in actual:
            scores.append(1.0)
            continue
        # Never fuzzy-match numbers or short names. One extra plural s is safe.
        match = max((SequenceMatcher(None, term, token).ratio()
                     for token in actual if len(token) >= 5 and len(term) >= 5
                     and not token.isdigit() and not term.isdigit()), default=0.0)
        if match < 0.85:
            return 0.0
        scores.append(match)
    return sum(scores) / len(scores)


def search(memory, query: str, *, limit: int = 5):
    from .memory import MemoryItem

    scored = []
    with closing(memory._connect()) as connection:
        for row in connection.execute("SELECT id, content, tags, created_at FROM memories"):
            score = segmented_relevance(
                query,
                str(row[1]),
                str(row[2]),
            )
            if score:
                scored.append((score, int(row[0]), MemoryItem(*row)))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)

    # Keep the newest instance of an identical remembered fact, but preserve
    # distinct facts in the same broad topic (e.g. "film test = Arrival" and
    # "film to watch = Inception"). Repeated explicit writes must not manufacture
    # artificial ambiguity.
    unique = []
    seen = set()
    for score, memory_id, item in scored:
        key = content_key(item.content)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= max(1, min(int(limit), 20)):
            break
    return unique


def list_recent(memory, *, limit: int = 20):
    """Return newest distinct persistent facts without pretending a search query.

    This is used only for an explicit user request to inspect their local memory.
    It deliberately does not expose hidden model/session state.
    """
    from .memory import MemoryItem

    rows = []
    with closing(memory._connect()) as connection:
        rows = connection.execute(
            "SELECT id, content, tags, created_at "
            "FROM memories ORDER BY id DESC LIMIT 200"
        ).fetchall()

    result = []
    seen = set()
    for row in rows:
        item = MemoryItem(*row)
        key = normalize(item.content).strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= max(1, min(int(limit), 50)):
            break
    return result
