# Jarvis — Étape 9B : MCP Capability Planner + routage des agents

## Position honnête du développement

**Étapes 1–8 :** intégrées, tests automatisés réussis sur les branches parents,
tests Windows réels reportés. **Étape 9 : en cours** (9A hub MCP, 9B proposition
de capacités pour les missions). Le **multi-agent autonome** n'est pas encore
opérationnel. Les étapes 10–11 restent prévues.

### Modifications 9B

- `capability_planner.propose_capabilities` : pour chaque étape d'un
  `MissionContract` *déjà enregistré*, comparer son intention et les
  exigences de preuve avec les capacités déclarées par les agents Jarvis et
  les outils MCP **activés et individuellement autorisés**.
- Sélection dynamique fondée sur les descriptions et capacités disponibles ;
  aucune table spécifique Gmail, WhatsApp, Maps ou Sheets. Les permissions du
  registre MCP restent la seule source de vérité pour les outils externes.
- Le routage est **strictement NON AUTORITAIRE**. Une proposition signifie
  « candidat possible », jamais « capacité testée, connectée, adaptée avec
  certitude, approuvée pour exécution ».
- Preuves/dépendances prioritaires : si une étape est déjà vérifiée, a des
  dépendances manquantes, ou si la mission est bloquée après un résultat
  externe incertain, **aucune action n'est proposée à l'exécution**.
- `LiveMissionContinuityRuntime.propose_mission_capabilities` : propriétaire
  vérifié via le checkpoint existant. Aucun nouvel appel Cerebras, aucun
  dispatch d'outil, aucun write SQL, aucune nouvelle session MCP.
- Bouton **« ⬡ Proposer agents / capacités MCP »** dans
  **▤ Supervision**, via la même boîte de commandes et le thread
  `AssistantWorker`. Le panneau distingue candidatures, dépendances,
  preuves manquantes et approbation obligatoire.
- Pour réduire les changements de comportement, les anciens chemins
  Chrome/UIA/CUA et la mémoire restent intacts, comme les Skills. Les Skills
  peuvent conseiller, mais ne retirent aucune capacité au runtime.
- Mesure de sûreté : un serveur MCP redécouvre un outil sous un nom déjà
  autorisé mais avec une description ou un schéma modifié → l'autorisation
  est **révoquée automatiquement** (empreinte SHA-256), puis nécessite une
  nouvelle autorisation du propriétaire.
- État du serveur : l'UI affiche seulement sa configuration ON/OFF et
  l'horodatage de la dernière découverte vraiment demandée avec succès ;
  **aucune découverte historique ne vaut certificat de connexion actuelle**.

### Contraintes persistantes

Un modèle peut proposer le mauvais outil, et la correspondance de mots ne
garantit aucune compréhension sémantique. C'est pourquoi ces suggestions
**ne déclenchent aucune action** et n'inhibent pas la découverte de nouvelles
capacités. Une étape future permettra une planification structurée par le
même cerveau et un superviseur d'exécution strict, mais pas dans ce patch.

Les outils MCP autorisés du registre peuvent déjà apparaître dans le moteur
de Cerebras existant (fonction de l'option `JARVIS_MCP_ENABLED`). Chaque appel
MCP exige toujours une confirmation explicite. Aucune reprise automatique
n'est autorisée lorsque l'effet externe d'un appel reste incertain.

Les connexions ChatGPT installées dans **cette conversation** ne partagent
pas automatiquement leurs jetons ni leurs sessions avec Jarvis. OAuth
indépendant, comptes externes et autorisations par fournisseur restent
nécessaires et **ne sont pas configurés par cette branche**.

Hermes est une **référence technique MIT**, avec un profil `hermes mcp serve`
optionnel préparé en 9A. Aucun paquet ou code Hermes n'a été incorporé.

### Vérification

- `tests/test_capability_planner.py` : routage par étape, dépendances,
  mission bloquée, étape prouvée, aucun provider inventé, description
  distante non fiable traitée comme données, garantie d'absence d'appel LLM,
  contrôle de propriété, Qt HTML échappé, autorisations révoquées.
- `tests/test_mcp_hub.py` : autorisation révoquée si changement de schéma
  ou de description, pas de confusion entre configuration, découverte
  historique et connexion active.
- `.github/workflows/mcp-capability-planner-v9b.yml` : tests critiques
  Windows, replays Browser Bridge, suite large Linux et conformité de l'API
  du SDK officiel MCP sans aucune connexion réelle à un fournisseur.

### Suite

9C : planification sémantique proposée par le **même modèle**
(schéma contrôlé, limites d'étapes et données inconnues explicites),
superviseur de délégation qui peut sélectionner des candidats mais exige
la preuve de réussite, la confirmation et le contrôle anti-replay existants.

Ne pas fusionner sans essais en conditions réelles sur Windows, y compris
Chrome, WhatsApp, messages à confirmation, mémoire, TTS, MCP Hermes local
facultatif et gestion des autorisations.


### Référence Hermes vérifiée dans le code source

- `hermes mcp serve` (voir `hermes_cli/subcommands/mcp.py` et
  `mcp_serve.py`) publie principalement les **conversations et canaux**
  de Hermes en tant qu'outils MCP. Il **ne transforme pas automatiquement**
  les connexions Google Sheets/Maps/WhatsApp internes à Hermes en serveurs
  indépendants accessibles à Jarvis.
- Hermes possède aussi un mécanisme distinct
  `agent.transports.hermes_tools_mcp_server` pour certains outils Hermes
  dans un contexte de runtime spécifique. Nous ne l'exécutons pas
  automatiquement : il exige sa propre installation, sa configuration et
  une validation de sécurité.
- Les cartes Gmail, Google Sheets, Google Maps, Google Drive, WhatsApp et
  GitHub sont des **raccourcis de configuration** sans URL inventée, sans
  clé secrète et sans connexion silencieuse. L'utilisateur doit ajouter
  séparément un serveur MCP fiable pour chacun.
