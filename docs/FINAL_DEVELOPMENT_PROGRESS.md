# Jarvis : developpement final

Date de demarrage : 2026-10-08 (Africa/Lagos).

## Base et invariants

- Branche de travail : `feature/codex-jarvis-final-development`.
- Base verifiee : `5bdd542d3cab8b7d52ab7db9bc20167ca911e050` (9C).
- Reference preservee : `feature/semantic-planning-authoring-v9c`.
- Aucun reset, remplacement du runtime, merge stable ou compte personnel connecte.
- Les journaux et diagnostics locaux non suivis preexistants sont preserves.
- Cerebras/Groq, Chrome Bridge, UIA/CUA, memoire V5 et texte/voix restent les voies d'execution existantes.

## Audit initial

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
| 1-8 et 9A-9C | Base historique preservee | A mesurer sur cette branche | EN ATTENTE DE TEST REEL |
| 9D superviseur actif | Integre, opt-in ; limites documentees | 20 tests dedies locaux passes ; CI a observer | EN ATTENTE DE TEST REEL |
| 9E delegation controlee | A developper | A developper | EN ATTENTE DE TEST REEL |
| Centre MCP complet | Partiel (9A/9B) | A etendre | EN ATTENTE DE TEST REEL |
| 10 apprentissage et Skills | Partiel (operationnel) | A etendre | EN ATTENTE DE TEST REEL |
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
du SDK officiel MCP 2.3.0 verifie. La CI Windows/Linux reste a observer sur
le commit publie. Aucun de ces tests ne certifie une action Windows reelle.
Aucun essai reel Chrome/Windows/audio/MCP personnel effectue.

## Hermes

Reference actuelle inspectee : `d94b70f675205c2c046138997819428772cd2678`.
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

1. Publier le checkpoint 9D et observer la nouvelle CI cumulative Windows/Linux.
2. Completer 9E et ses responsabilites generiques, la coordination multi-etapes
   et la reprise controlee, dans le seul runtime.
3. Etendre MCP, apprentissage/Skills, scheduler et interface avec tests associes.
4. Publier les commits autorises, observer la CI et ouvrir une PR brouillon.
5. Finaliser l'architecture, la comparaison Hermes, les couts, la securite,
   l'installation/rollback et la procedure d'acceptation Windows.
