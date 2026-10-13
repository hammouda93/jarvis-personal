"""Conservative clause boundaries for local routing, never a permission grant.

The semantic planner still owns the full objective and constraints. These
helpers keep keyword-based repairs from treating prohibitions as commands.
"""
import re
import unicodedata


def _normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(c)).replace("\u2019", "'")


def positive_instruction_clauses(text):
    unquoted = re.sub(r'"[^"\n]*"|\u00ab[^\u00bb]*\u00bb|\u201c[^\u201d]*\u201d', " ", str(text or ""))
    clauses = re.split(r"[.;!?\n]+|\b(?:puis|ensuite|then)\b|,?\s+et\s+(?=(?:ne\b|n'|ouvre\b|ferme\b|ecris\b|lis\b|cherche\b|recherche\b))",
                       _normalized(unquoted))
    result = []
    for clause in clauses:
        clause = clause.strip(" ,:")
        if not clause:
            continue
        if re.search(r"\b(?:ne\s+|n')\w+.{0,80}\b(?:pas|aucun|aucune|jamais|plus|rien)\b|\b(?:never|do not|don't|interdit|interdiction)\b", clause):
            continue
        if re.match(r"^(?:si\b|if\b|unless\b|au besoin\b|eventuellement\b|facultativement\b)", clause):
            continue
        # A positive action followed by a limiting phrase remains positive.
        clause = re.split(r"\b(?:sans|without)\b", clause, maxsplit=1)[0].strip()
        result.append(clause)
    return result


def has_windows_objective(text):
    return any(not re.search(r"https?://|\b(?:chrome|browser|navigateur|onglet|tab|site|web)\b", clause)
               and re.search(r"\b(?:fichier|file|document|dossier|folder|bureau|desktop|fenetre|window|application|installateur|installer|exe)\b", clause)
               for clause in positive_instruction_clauses(text))


def has_instruction_constraints(text):
    raw = _normalized(str(text or ""))
    return bool(re.search(r"\b(?:ne\s+|n'|never|do not|don't|sans|without|si|if)\b", raw))


def requires_agent_routing(text):
    raw = _normalized(str(text or ""))
    # A direct primitive cannot implement multiple goals or retain constraints.
    if has_instruction_constraints(text):
        return True
    clauses = positive_instruction_clauses(raw)
    action = r"\b(?:ouvre|ouvrir|lance|affiche|ferme|ecris|saisis|lis|verifie|cherche|recherche|clique|reviens|open|close|write|read|search)\b"
    return sum(bool(re.search(action, clause)) for clause in clauses) > 1


def requires_empty_target(text):
    return any(re.search(r"\b(?:document|fichier|champ|editeur|file|field|editor)\s+(?:vide|empty|blank)\b|\b(?:empty|blank)\s+(?:document|file|field|editor)\b", clause)
               for clause in positive_instruction_clauses(text))
