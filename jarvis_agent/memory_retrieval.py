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
where memorisee memorises memorisees memoriser""".split())
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
            score = relevance(query, str(row[1]), str(row[2]))
            if score:
                scored.append((score, int(row[0]), MemoryItem(*row)))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in scored[:max(1, min(int(limit), 20))]]
