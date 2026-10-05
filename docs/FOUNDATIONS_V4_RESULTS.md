# Résultat de reconstruction — 5 octobre 2026

Branche locale : `codex/personal-agent-foundations-v4`, créée directement depuis
`500453228d9e00ae7a820cf60d0a1d73344c61ca`. Le checkpoint utilisateur reste intact.
Les expériences `fix/runtime-foundation-gates-v3` ne sont pas importées.

## Livrables

- [Audit architectural et sources primaires](FOUNDATIONS_V4_AUDIT.md) :
  diagnostic du routage mémoire, comparaison Chrome A/B, étude UFO²/Agent-S/
  OpenInterpreter/UIA/OCR et limites de chaque approche.
- [Inventaire de la base](FOUNDATIONS_V4_INVENTORY.json) : les 142 fichiers
  suivis du checkpoint, empreintes, symboles et dépendances Python internes.
- Memory Router exécuté avant tous les providers, writes explicites, session →
  SQLite → readers connecteurs enregistrés → clarification ; recherche fuzzy
  conservative et conflits explicites. Aucun modèle requis pour ces routes.
- Browser Core : les 14 primitives génériques, extension MV3 dans le vrai profil,
  Native Messaging, RPC locale authentifiée, références tab/document, CDP Input
  ciblé pour touches/clics principaux. Aucun clavier OS dans cet adapter.
- Computer Grounding : worker UIA borné → OCR Windows natif → adapter de modèle
  local facultatif ; objets texte/bbox/type/confiance/ref, identité HWND/PID,
  fraîcheur, occlusion, vérification après action et garde de contexte/header.
- Console live enregistrant JSONL/captures et [guide Windows exact](FOUNDATIONS_V4_WINDOWS_ACCEPTANCE.md).
  Les 10 gates sont déclarés dans `tests/acceptance_foundations_v4.json`.

Les trois flags `JARVIS_MEMORY_CORE_ENABLED`, `JARVIS_BROWSER_CORE_ENABLED` et
`JARVIS_COMPUTER_CORE_ENABLED` restent à `0` par défaut. Les adapters n'imposent
pas une migration de la mémoire ni un remplacement de l'installer historique.

## Vérifications effectuées

| Vérification | Résultat | Portée de la preuve |
| --- | --- | --- |
| Suite Python complète | 404 tests, OK | 370 tests historiques + 34 tests fondations ; une correction du harness STT frozen, aucune modification de production STT |
| Mémoire restart | OK | Vrais sous-processus, vraie SQLite, outils remember/recall tracés, cinq formulations après restart ; aucune invocation LLM |
| Native host | OK | Vrai sous-processus host, TCP loopback et stdio framed ; peer extension simulé, Chrome réel non testé |
| Replays JavaScript | 14 tests, OK | API Chrome/DOM synthétiques ; onglets, refs, touches/clics ciblés, résultat partiel inconnu, download et Unicode |
| Grounding adversarial | OK | Backend desktop synthétique ; mauvais header/focus, OCR non éditable, refs périmées, résultat inconnu et preuve d'absence incomplète |
| OCR Windows natif | 5 appels, 0,262–0,321 s | OCR réel sur PNG fixture, sous-processus inclus ; aucune validation de composer/header desktop |
| Modèle local Gemma déjà présent | Dépasse le délai de 2 s | Fixture simple ; modèle non qualifié pour le grounding custom rapide |
| Scripts PowerShell | Parse OK | Installation du host non exécutée ; runner de validation exécuté avec succès |
| Modules protégés | Aucun diff depuis le checkpoint | windows_perception, tools, screen_vision, mission, native_tools, browser_adapter et memory |
| Checkpoint | SHA inchangé | Ref origin/checkpoint/runtime-foundation-v2-tested vérifiée |

Commande du dernier run : `scripts/run_foundations_v4_validation.ps1` avec les
runtimes Python 3.12 / Node 24 fournis sur ce poste et les dépendances isolées
dans `.cache`. Sortie détaillée locale : `.cache/foundation-all-tests.log`.
[Mesure OCR détaillée](FOUNDATIONS_V4_OCR_BENCHMARK.json).

## Gates non validés et travail restant

Chrome normal n'a pas encore reçu l'extension et l'enregistrement Native Messaging.
Les parcours réels YouTube, Google, Wikipedia, plusieurs onglets et changement de
focus Windows restent à exécuter, y compris depuis la conversation Jarvis V3.
Les writes DOM et les clics synthétiques de sous-frames doivent être qualifiés
sur leurs interfaces réelles. AutoConnect a été étudié, pas implémenté comme
second backend. Les shadow roots fermés ne sont pas couverts.

Le parcours WhatsApp complet avec exact header, composer, envoi et message sortant
reste non validé. Un label OCR ne constitue pas un champ éditable ; aucun modèle
local n'est encore qualifié pour les contrôles entièrement opaques et la région
header. OmniParser/UI-TARS ne sont pas intégrés. L'adapter échoue explicitement
quand ces preuves manquent. C'est une limite fonctionnelle restante, pas un test
desktop réussi grâce à un mock.

Le parcours réel de l'EXE Cursor reste à rejouer avec flags désactivés puis
activés. L'absence de diff dans le moteur protège son implémentation ; elle ne
remplace pas cette validation réelle du focus et du routing. Aucun nom de contact,
coordonnée ni branche de workflow propre à ces applications n'a été ajouté dans
les nouveaux modules runtime.

Cette branche est une reconstruction candidate testée automatiquement. Elle
ne constitue pas un nouveau checkpoint desktop validé et ne doit pas être
promue avant réussite des gates live du guide.
