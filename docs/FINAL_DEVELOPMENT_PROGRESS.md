# Jarvis : developpement final

Date de demarrage : 2026-10-08 (Africa/Lagos).

## Reprise du 2026-10-09 : checkpoint 9F

### Reconciliation 9G Et Consentement Operationnel

Dernier brief utilisateur et commentaires des PR #16/#17 relus le 2026-10-09.
Les observations reelles signalees dans ces commentaires sont des donnees de
recette humaine, pas de nouvelles executions effectuees par Codex.
Les travaux Skills ont ete preserves dans `8b9864b`, puis la branche 9G
`b78266f` a ete reconciliee sans ecrasement dans `aa4740c` sur la seule branche
de developpement. La CI 9G a ete inspectee Windows/Linux SUCCESS :
[run 37928313324](https://github.com/hammouda93/jarvis-personal/actions/runs/37928313324).
La regression combinee avant changement de politique etait verte : 847 tests.

Travail ajoute apres reconciliation :

- Deux interrupteurs independants et persistants dans le centre de controle.
  Utiliser les Skills : OFF par defaut. Apprentissage operationnel : OFF par defaut.
- Le nouveau consentement operateur remplace le drapeau combine historique
  d'environnement; un fichier manquant/invalide laisse les deux modes OFF.
- ON/OFF reutilise les connaissances sans apprendre; OFF/ON peut apprendre
  mais n'injecte/reutilise aucune connaissance operationnelle. OFF/OFF n'emet
  aucun checkpoint LLM d'apprentissage ni ecriture automatique de profils/lecons.
- Filtrage des schemas Ollama/OpenAI/Cerebras, gardes de dispatch natives,
  controle des lectures/ecritures du backend kernel, application par le worker.
  Les connaissances existantes ne sont pas effacees. V5/missions/preuves restent
  independantes de cette politique.
- Les 402, clarifications versionnees, questions en entier, prevention 413 et
  garde de fausse affirmation d'envoi 9G sont conserves. Correction supplementaire
  du cooldown : un refus local sans requete ne prolonge plus le delai initial.

Verification locale actuelle : 856 tests Python Windows passes, 0 echec/erreur/skip;
45/45 replays Browser Bridge. Les deux interrupteurs, quatre modes et redemarrage
de configuration sont testes automatiquement. CI du code combine a observer.
Le centre MCP complet, l'autonomie naturelle bout-en-bout, le compactage de
contexte, la lecture fiable de lignes/cartes, V5/Ollama reel et le scheduler
durable restent ouverts. Aucun agent de production ni compte personnel utilise.
Les erreurs de nombres MS Football, refs Instagram et routage VLC sont des
problemes rapportes encore a traiter, pas des corrections declarees ici.

Etat de depart inspecte : `210e9d38653de23b44106eadb6ff31795efd8e54`, synchronise
avec la branche distante. Ses deux jobs Windows/Linux sont SUCCESS :
[CI 210e9d3](https://github.com/hammouda93/jarvis-personal/actions/runs/37858807575).
Les commits `420179b` (MCP) et `968cc4a`/`210e9d3` (contrat begin_turn V5)
sont conserves, sans retour arriere.

Travail 9F realise :

- Contrat `mission_checkpoint` dans une execution supervisee : continuer,
  attendre la verification, ou signaler un obstacle. Aucune declaration de
  completion ni autorisation ne peut etre fournie par cet outil.
- Une etape longue peut poursuivre plusieurs tours dans le meme runtime,
  sous les budgets cumulatifs existants. Une limite de tour n'est plus une
  preuve d'execution definitive de toute l'etape. Pas d'augmentation des 8 tours.
- La conversation d'une mission au plan approuve coordonne les etapes jusqu'a
  une pause justifiee. L'objectif original accompagne chaque delegation.
- Les mutations identiques deja reussies ne sont pas redispatchees lors d'une
  continuation. Les effets inconnus restent bloques avant une nouvelle action.
- Les rapports intermediaires passent par des signaux Qt, sans TTS ni attente
  d'un nouveau message. L'arret hors bande reste disponible pendant la mission.
- Les annonces finales supervisees proviennent de l'etat des preuves, pas
  d'une phrase du modele. Un retour de reponse ne remet plus une mission
  non verifiee dans l'etat visuel TERMINE.
- V5 implemente explicitement `run_with_context` : les ecritures/rappels dans
  une mission ne contournent plus la memoire. Les continuations operationnelles
  supervisees ne sont pas reclassifiees a chaque tour par Ollama.
- Les sorties Ollama vides/tronquees sont refusees, le thinking inutile du
  classificateur est desactive. Le timeout et l'opt-in cloud ne sont pas changes.
- Interface Qt : tous les controles dans le defilement, panneaux horizontaux
  et conversation redimensionnables, poignee de fenetre, dimensions initiales
  bornees a l'ecran logique, controles compacts et contrastes lisibles.

Verification locale : 815 tests Python Windows passes, 0 erreur/echec/skip;
45 replays Browser Bridge passes. Captures Qt offscreen inspectees en
660x500, 1024x640 et 1500x900; aucun debordement horizontal de la console avec
Segoe UI. Tests de geometrie egalement passes avec la police de secours.
Ce sont des tests automatiques, pas une nouvelle installation Windows reelle.
CI de `38aa8dd` verifiee SUCCESS sur Windows/Linux :
[run 37903921448](https://github.com/hammouda93/jarvis-personal/actions/runs/37903921448).
Windows : 815 passes; Linux : 810 passes et 5 skips Windows/DPAPI explicites.
45 replays Browser Bridge et contrat du SDK MCP officiel passes sur chaque OS.

Travaux Skills preserves avant reconciliation 9G : snapshots transactionnels,
edition/restauration operateur, desactivation non revoquee par l'apprentissage,
historique legacy sans versions inventees. 15 nouveaux tests, regression Windows
locale 830 verte. L'utilisation et l'apprentissage doivent devenir OFF par defaut
avec deux reglages persistants conformement au nouveau brief du 2026-10-09;
ce changement de politique n'est pas encore integre dans ce checkpoint intermediaire.

Limites importantes : la continuation automatique 9F exige un plan supervise
approuve. Le parcours conversationnel sans plan conserve son fonctionnement
historique, mais l'installation complete en langage naturel n'est PAS encore
demontree. Les confirmations de mutations du superviseur restent conservatrices;
une permission precise pour des transitions UI ordinaires reste a concevoir.
Les predicates finaux d'installation/raccourci doivent etre configurables et
valides en conditions reelles. Aucune boucle ne transforme un clic en preuve
de l'objectif complet.

## Tests Windows signales par l'utilisateur

Source : brief de continuation fourni le 2026-10-09. Ce sont des observations
humaines rapportees, pas des executions realisees par Codex pendant cette reprise.

| Parcours | Observation humaine | Limite restant ouverte |
| --- | --- | --- |
| Conversation francaise texte/voix | Conversation, changement de mode, micro coupe, fallback TTS Windows | Refaire avec 9F |
| Chrome habituel / YouTube | Recherche complete, premier resultat, retour, fermeture ciblee | Refaire avec 9F et onglets temoins |
| WhatsApp Web generique | Recherche/contact, saisie Unicode, confirmation et soumission | Livraison au destinataire non prouvee; lecture variee a tester |
| MS Football | Consultation/comptage, preview et approbation de mutation | Mutation definitive non validee |
| Installateur Windows | Fichier ouvert, licence/Next/options observes, dialogue de fermeture traite | Installation achevee, raccourci et lancement non verifies |
| Memoire V5 | Rappel en session rapporte; timeout local et rappel apres redemarrage en echec | Validation Ollama et stockage persistant reel requise |

Lecture locale sans inference : Ollama `127.0.0.1:11434/api/tags` repond et
liste `qwen3:4b-instruct`, `qwen3:4b` et `gemma3:latest`. Cela prouve la
disponibilite du serveur et la presence des modeles, pas la latence de V5,
le chargement effectif du modele ni la fiabilite du rappel apres redemarrage.

Procedure : `docs/FINAL_WINDOWS_ACCEPTANCE.md`. Details du contrat :
`docs/MISSION_CONTINUATION_V9F.md`. MCP : `docs/MCP_SECURITY_AND_OAUTH.md`.

## Base et invariants

- Branche de travail : `feature/codex-jarvis-final-development`.
- Base verifiee : `5bdd542d3cab8b7d52ab7db9bc20167ca911e050` (9C).
- Reference preservee : `feature/semantic-planning-authoring-v9c`.
- Aucun reset, remplacement du runtime, merge stable ou compte personnel connecte.
- Les journaux et diagnostics locaux non suivis preexistants sont preserves.
- Cerebras/Groq, Chrome Bridge, UIA/CUA, memoire V5 et texte/voix restent les voies d'execution existantes.

## Audit initial (historique 9C)

`build_agent_runtime` assemble un seul registre d'outils et le fournisseur choisi,
puis des wrappers opt-in : foundations, memoire, traces, continuite de missions,
fiabilite Hermes. `LiveMissionContinuityRuntime` conserve les tours, le graphe,
les preuves et la reprise avec controle de proprietaire. Il bloque une action
interrompue et ne deduit pas le succes du but d'un retour d'outil.

`semantic_goal_supervisor` est actuellement une revue deterministe sans dispatch.
`CapabilityAgentRouter` et `capability_planner` proposent des responsabilites ;
ils ne deleguent pas encore les etapes. `llm_mission_planner` produit un plan
borne via le fournisseur existant, avec une requete explicite et sans outils.

Le MCP Hub possede deja un registre, une permission par outil et une empreinte
de schema qui revoque une autorisation modifiee. Le transport ouvre une session
temporaire et ne retente pas un effet incertain. Il ne supporte actuellement
que le profil stdio `hermes mcp serve` et HTTP avec bearer d'environnement.
OAuth, stockage securise, stdio generique et suivi d'utilisation restent ouverts.

L'apprentissage operationnel et les Skills existent dans `AgentKnowledgeStore` ;
il faut etendre leur validation, versionnement, desactivation et retour arriere.
`MissionScheduler` est une file kernel en memoire, pas un scheduler personnel
durable. L'interface PySide6 possede deja conversation, visualisation, supervision,
MCP et roadmap. Ces composants seront etendus.

## Etat des jalons

| Jalon | Code integre | Validation automatique nouvelle branche | Test reel |
| --- | --- | --- | --- |
| 1-8 et 9A-9C | Base historique preservee | Regression cumulative locale et CI 9D verte | EN ATTENTE DE TEST REEL |
| 9D superviseur actif | Integre, opt-in ; limites documentees | 20 tests dedies locaux ; CI Windows/Linux verte | EN ATTENTE DE TEST REEL |
| 9E delegation controlee | Integree dans le runtime existant ; roles sans outils bloques | 38 tests dedies ; CI Windows/Linux verte | EN ATTENTE DE TEST REEL COMPLET |
| 9F continuite / V5 / Qt | Integre pour les plans supervises ; autonomie conversationnelle complete encore ouverte | 29 nouveaux tests; CI 38aa8dd Windows/Linux verte | EN ATTENTE DE TEST REEL |
| Centre MCP complet | HTTP/stdio, OAuth SDK, coffre OS, quotas/journal integres dans 420179b | CI 210e9d3 Windows/Linux verte | Aucun compte personnel connecte |
| 10 apprentissage et Skills | Historique, edition, restauration, desactivation et deux opt-in independants | 24 nouveaux tests locaux; regression 856 verte; CI combinee a observer | EN ATTENTE DE TEST REEL |
| 11 automatisations durables | A developper | A developper | EN ATTENTE DE TEST REEL |
| Interface finale et acceptation | Partiel | A etendre | EN ATTENTE DE TEST REEL |

## Tests et problemes

Les chiffres historiques fournis (480 Windows, 661 Linux, 45 Browser Bridge)
concernent la CI 9C et ne prouvent pas les futurs commits.

Le `.venv` local reference un Python 3.9 absent. Un environnement separe
`.cache/t` est cree avec Python 3.12.14 pour les tests ;
le `.venv` et les donnees de l'utilisateur ne sont pas modifies.

Le premier environnement de test long a echoue a installer ElevenLabs (limite
de chemins Windows) ; le chemin raccourci corrige ce probleme sans changer
les dependances ni la configuration Windows.

La suite complete a revele des connexions SQLite conservees apres les blocs
`with`, ce qui empêche le nettoyage des fichiers sur Windows. Les stores
utilisent maintenant des connexions qui conservent commit/rollback puis ferment
le handle. `JARVIS_DATA_DIR` permet d'isoler les stores initialises a l'import.
La lecture de telemetrie inaccessible est traitee comme indisponible.

Resultats locaux du checkpoint 9D : 685 tests Python Windows passes, dont 20
tests dedies de supervision/Qt ; 45 replays Browser Bridge passes ; contrat
du SDK officiel MCP 2.3.0 verifie. CI du commit `539e1fdc23efd08f87fe2a96117cb4ca08d62f72` :
[run 37837187767](https://github.com/hammouda93/jarvis-personal/actions/runs/37837187767),
Windows et Linux SUCCESS. Windows : 685 tests passes ; Linux : 681 passes et
4 skips Windows explicites ; 45 replays Browser Bridge passes sur chaque OS.
Aucun de ces tests ne certifie une action Windows reelle.
Au moment de ce checkpoint 9D, aucun essai reel effectue par Codex.
Les observations humaines ulterieures sont listees en tete de ce rapport.

Checkpoint 9E en preparation : 723 tests Python Windows passes, dont 38 dedies
a la delegation ; 45 replays Browser Bridge passes. Les tests incluent l'assemblage
reel des wrappers avec fournisseur simule, permissions exactes, reprise sans
rejeu, arrets et budgets de memoire V5. Deux echecs de fixtures (plan immuable,
projection incompletement attendue) ont ete analyses/corriges avant cette passe.
La CI 9E a ensuite ete verifiee SUCCESS sur Windows/Linux :
[run 37854436125](https://github.com/hammouda93/jarvis-personal/actions/runs/37854436125).

PR brouillon ouverte et rattachee :
[PR 16](https://github.com/hammouda93/jarvis-personal/pull/16), base reference 9C,
head branche de developpement. Aucune fusion effectuee.

## Hermes

Reference initiale inspectee : `d94b70f675205c2c046138997819428772cd2678`.
Main revalide et sources delegation relues : `9d05e7ff92d3edd9abdf20fe3d04551905cb995e`.
Comparaison, incompatibilites et licence : `docs/HERMES_ADAPTATION.md`.
Sources : `tools/mcp_tool_discovery.py`, `tools/mcp_tool_transport.py`,
`hermes_cli/mcp_security.py`, `hermes_cli/subcommands/mcp.py`, `mcp_serve.py`,
`agent/transports/hermes_tools_mcp_server.py`.

La decouverte actuelle distingue configuration, session active, lazy et erreur,
borne la concurrence et applique un cooldown aux connexions echouees.
Le transport applique des controles d'environnement, de redirection et de
negociation. Les mecanismes utiles seront adaptes sans installer Hermes.
`hermes mcp serve` expose les conversations/canaux ; le serveur d'outils publie
une selection et depend de son runtime. Aucun des deux ne prouve qu'un service
personnel est connecte a Jarvis. Aucun code Hermes copie a ce stade.

## Prochain travail

1. Publier 9F, observer sa CI Windows/Linux et corriger toute regression.
2. Etendre l'autonomie depuis les demandes naturelles avec permissions de
   transition UI precises et predicates finaux generiques, sans scripts par app.
3. Tester V5/Ollama et missions longues en conditions reelles controlees.
4. Completer le catalogue MCP officiel et les connecteurs personnels, puis
   Skills versionnes/desactivables et scheduler durable avec tests associes.
5. Publier les commits autorises, observer la CI et maintenir la PR brouillon.
6. Finaliser l'architecture, la comparaison Hermes, les couts, la securite,
   l'installation/rollback et la procedure d'acceptation Windows.
