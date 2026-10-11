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


def _personal_identity_slot(query: str) -> tuple[str, ...]:
    """Resolve only a direct request about the user's own name.

    The normal stopword vocabulary deliberately drops 'nom', 'prenom' and
    'appelle', so without this typed slot "quel est mon nom ?" matches
    *nothing*. Do not treat a project/child/other person's name as self.
    """
    text = normalize(query)
    if not re.search(r"\b(?:mon|ma|je|moi|my|i)\b", text):
        return ()
    if re.search(r"\bprenoms?\b", text):
        return ("prenom",)
    if re.search(r"\bnom\b", text) or re.search(r"\bname\b", text):
        return ("nom", "prenom")
    if re.search(r"\bappell(?:e|es|ez|er|ait)\b", text):
        return ("prenom", "nom")
    return ()


def relevance(query: str, content: str, tags: str = "") -> float:
    wanted, actual = terms(query), terms(content + " " + tags)
    if not wanted:
        slots = _personal_identity_slot(query)
        if not slots:
            return 0.0
        # Only source-proven identity records (including original labels
        # preserved in SQLite), never an unrelated recent memory.
        raw = normalize(content + " " + tags)
        if any(re.search(r"\b" + re.escape(slot) + r"\b", raw) for slot in slots):
            return 1.0
        if "prenom" in slots and re.search(r"\b(?:je m'appelle|mon prenom|my name is)\b", raw):
            return 0.98
        return 0.0
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
