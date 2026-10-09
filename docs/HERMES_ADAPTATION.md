# Comparaison Et Provenance Hermes

Jarvis conserve son architecture et ses pilotes. Hermes n'est ni installe comme
runtime, ni substitue a Cerebras, Chrome Bridge, UIA/CUA ou memoire V5.

## Sources Inspectees

- Audit initial 9D : commit `d94b70f675205c2c046138997819428772cd2678`.
- Relecture 9E du main courant le 2026-10-08 : commit
  `9d05e7ff92d3edd9abdf20fe3d04551905cb995e`.
- [Dispatch de delegation](https://github.com/NousResearch/hermes-agent/blob/9d05e7ff92d3edd9abdf20fe3d04551905cb995e/tools/delegate_tool_dispatch.py).
- [Contrat de sortie](https://github.com/NousResearch/hermes-agent/blob/9d05e7ff92d3edd9abdf20fe3d04551905cb995e/tools/delegation_output_schema.py).
- Sources MCP initiales : `tools/mcp_tool_discovery.py`, `tools/mcp_tool_transport.py`,
  `hermes_cli/mcp_security.py`, `hermes_cli/subcommands/mcp.py`, `mcp_serve.py`,
  `agent/transports/hermes_tools_mcp_server.py`.

## Choix Compatibles

Relecture de continuite le 2026-10-09 : main
`14ec243c1797412d93e6b52c41f29f41e75b0cef`.
Sources inspectees :
[run_agent](https://github.com/NousResearch/hermes-agent/blob/14ec243c1797412d93e6b52c41f29f41e75b0cef/run_agent.py),
[conversation_loop](https://github.com/NousResearch/hermes-agent/blob/14ec243c1797412d93e6b52c41f29f41e75b0cef/agent/conversation_loop.py),
[turn_final_response](https://github.com/NousResearch/hermes-agent/blob/14ec243c1797412d93e6b52c41f29f41e75b0cef/agent/turn_final_response.py).
Hermes distingue continuations/interruptions et reponse finale, avec des
gardes bornes sur les annonces d'action sans appel et une gate de verification.
Jarvis reutilise le principe de disposition explicite avant cloture, en
conservant ses predicates locaux independants et budgets de mission durables.
La limite Hermes par defaut non bornee n'est pas reprise; les reparations
textuelles Hermes ne constituent pas une preuve d'installation Windows.

Sources officielles confrontees pour 9F :
[Qt QScrollArea](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QScrollArea.html),
[Qt QSplitter](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSplitter.html),
[Ollama contrat API](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml).
La geometrie utilise les contraintes et splitters Qt; le classificateur
local demande une sortie JSON sans thinking. Aucun moteur tiers ne remplace
les chemins de conversation et d'action existants.

Hermes porte l'identite/autorite d'origine, borne les enfants, suit leur arret,
persiste les resultats et agrege les rapports. Jarvis reprend ces principes via
son contexte proprietaire, sa fabrique deja presente, son journal SQLite et ses
budgets durables. Le bureau Windows et les confirmations imposent ici une
coordination sequentielle dans le seul runtime.

Le dispatch Hermes est couple a son gateway, ses sessions, ses enfants IA et
son pool de threads. Le copier remplacerait nos limites et nos points d'execution.
Le contrat de sortie Hermes peut demander un tour correctif et accepte du JSON
sans validation si jsonschema manque. Jarvis produit les rapports de confiance
localement et ne relance pas une action pour corriger un rapport du modele.

Pour MCP, les principes utiles sont separation configure/connecte/lazy/erreur,
limites et cooldown, environnement stdio reduit, controles de redirection et
reconciliation des schemas. L'adaptation du centre MCP reste en cours.

## Licence

La [licence amont inspectee](https://github.com/NousResearch/hermes-agent/blob/9d05e7ff92d3edd9abdf20fe3d04551905cb995e/LICENSE)
est MIT, copyright 2025 Nous Research. Aucun extrait de code amont n'a ete copie
dans les ajouts 9D/9E : implementation originale utilisant les modules Jarvis.
Toute future copie de code substantielle devra conserver la notice MIT complete,
indiquer fichier/commit source et etre declaree dans ce document.
