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
favourite like liked love have has that it to in please know said saved test
prochain prochaine prochaines prochains next prevu prevue scheduled quand when
where memorisee memorises memorisees memoriser
demande demandes demandee demandees demander demandais demandait asked requesting""".split())
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
    words = [_CONCEPTS.get(t, t) for t in re.findall(r"\w+", text)
             if t not in _STOP and (len(t) > 1 or t.isdigit())]
    # Preserve complete opaque identifiers even when their components are
    # stopwords or single letters. They must not become fuzzy entity matches.
    clitics = {"je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "toi", "moi"}
    anchors = [token for token in re.findall(r"\w+(?:[-.]\w+)+", text)
               if not (token.rsplit("-", 1)[-1] in clitics
                       and token.split("-", 1)[0] in _STOP)]
    return tuple(dict.fromkeys([*words, *anchors]))


# Function words only. When the historic high-precision query stoplist
# removes every token, recover the *actual topic* from the user's wording,
# whatever that topic is. No person/field/application-specific exceptions.
_QUESTION_SYNTAX = frozenset("""
quel quelle quels quelles que quoi qui comment pourquoi est sont etait etais
ce cet cette ces de du des le la les un une mon ma mes ton ta tes son sa ses
notre nos votre vos leur leurs je j tu il elle nous vous ils elles me te se
moi toi pour dans sur avec et ou au aux en as ai a ne pas dire dis donne
donner peux peut pourrais sais savez tu vous rappelle rappelles rappeler
souviens souviens-toi qu est ce c est know tell remember recall which what
who how why the a an of for from your my you i it is are was do does did
can could please me us our mine yours about
""".split())


def _topic_terms(value: str) -> tuple[str, ...]:
    """Literal topic fallback for degenerate zero-term queries.

    This is *not* semantic inference. A topic is selected from the
    user's actual wording and must occur in the source text/tags. We never
    turn a generic question into permission to return recent unrelated rows.
    """
    value = normalize(value).replace("rendez-vous", "rendezvous")
    tokens = re.findall(r"\w+", value)
    result = [
        token for token in tokens
        if token not in _QUESTION_SYNTAX and len(token) >= 3
    ]
    return tuple(dict.fromkeys(result))


def relevance(query: str, content: str, tags: str = "") -> float:
    wanted = terms(query)
    # The normal search uses high-precision content terms. If all were
    # treated as common query language, fall back to the literal subject of
    # the question; never infer synonyms or return unrelated recent facts.
    topic_fallback = not wanted
    if topic_fallback:
        wanted = _topic_terms(query)
    if not wanted:
        return 0.0
    actual = set(terms(content + " " + tags))
    if topic_fallback:
        actual.update(_topic_terms(content + " " + tags))
    if not actual:
        return 0.0
    scores = []
    for term in wanted:
        if term in actual:
            scores.append(1.0)
            continue
        if "-" in term or "." in term:
            return 0.0
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
            score = relevance(query, str(row[1]), str(row[2]))
            if score:
                scored.append((score, int(row[0]), MemoryItem(*row)))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in scored[:max(1, min(int(limit), 20))]]
