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

## Relecture Skills Et MCP Du 2026-10-09

Skills : sources `tools/skill_manager_tool.py`, `agent/skill_utils.py`,
`tools/skills_tool.py` au commit `14ec243c1797412d93e6b52c41f29f41e75b0cef`.
Les principes de mutation serialisee, historique restaurable et filtrage des
Skills desactives sont adaptes a notre stockage SQLite/revisions CAS. OFF
runtime empeche toute injection/reutilisation, sans masquer la bibliotheque
operateur ni effacer V5. Pas de copie du moteur de Skills Hermes.

MCP : main `0670ba45240b734c1e6f6d1d5ead87233f37df49`,
[discovery](https://github.com/NousResearch/hermes-agent/blob/0670ba45240b734c1e6f6d1d5ead87233f37df49/tools/mcp_tool_discovery.py),
[transport](https://github.com/NousResearch/hermes-agent/blob/0670ba45240b734c1e6f6d1d5ead87233f37df49/tools/mcp_tool_transport.py).
Hermes supporte les serveurs resource/prompt-only, distingue les etats et borne
la decouverte. Jarvis reprend ces contrats avec le SDK officiel et son registre
existant. Les sessions restent courtes, sans adopter le gateway/pool Hermes,
ni remplacer Cerebras, Chrome Bridge ou UIA. Aucune portion amont copiee.

## Relecture Ressources Et Windows

Relecture ressources au meme main `0670ba45240b734c1e6f6d1d5ead87233f37df49` :
[retry_utils](https://github.com/NousResearch/hermes-agent/blob/0670ba45240b734c1e6f6d1d5ead87233f37df49/agent/retry_utils.py),
[context_compressor](https://github.com/NousResearch/hermes-agent/blob/0670ba45240b734c1e6f6d1d5ead87233f37df49/agent/context_compressor.py),
[micro_compaction](https://github.com/NousResearch/hermes-agent/blob/0670ba45240b734c1e6f6d1d5ead87233f37df49/agent/micro_compaction.py).
Les en-tetes de cooldown et la protection du texte utilisateur/du tail sont
retenus. Pas de nouveau modele de resume, cache/provider ni gateway adopte :
Jarvis deduplique uniquement les lectures identiques et conserve son transcript.
Le compactage Hermes avec LLM auxiliaire n'est pas une dependance de Jarvis.
Contrats croises avec [Groq rate limits](https://console.groq.com/docs/rate-limits)
et [HTTP Retry-After](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after).
La decouverte Windows utilise les pilotes locaux deja presents et les contrats
[Start Apps](https://learn.microsoft.com/en-us/powershell/module/startlayout/get-startapps)
et [App Paths](https://learn.microsoft.com/en-us/windows/win32/shell/app-registration).

## Audit Memory V10

Audit Memory V10 du 2026-10-09 : main
`b624a38f21f2f674d5a8ccf387687c1b481e23f6`,
[memory_tool.py](https://github.com/NousResearch/hermes-agent/blob/b624a38f21f2f674d5a8ccf387687c1b481e23f6/tools/memory_tool.py).
L'epingle d'une entree precise lors d'une correction approuvee est une piste
pour la prochaine etape versionnee, pas une fonctionnalite deja copiee/livree.
Les fichiers MEMORY.md et l'injection systematique Hermes ne remplacent pas
SQLite V5 ; les gates fail-open ne sont pas reprises. Voir l'audit V10.

## Licence

La [licence amont inspectee](https://github.com/NousResearch/hermes-agent/blob/9d05e7ff92d3edd9abdf20fe3d04551905cb995e/LICENSE)
est MIT, copyright 2025 Nous Research. Aucun extrait de code amont n'a ete copie
dans les ajouts 9D/9E : implementation originale utilisant les modules Jarvis.
Toute future copie de code substantielle devra conserver la notice MIT complete,
indiquer fichier/commit source et etre declaree dans ce document.
