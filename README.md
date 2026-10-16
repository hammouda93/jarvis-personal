# Jarvis Personal

Assistant personnel Windows : conversation texte et voix, navigateur Chrome
habituel, outils Windows, mémoire SQLite et services MCP explicitement connectés.
Le même runtime choisit les outils et suit les missions. Un succès d'outil reste
distinct de la preuve que l'objectif entier est atteint.

## V17 — chef d’orchestre, univers spatial et veille

La branche `feature/orchestra-space-watch-v17`, issue de V16, ajoute de vraies
conversations spécialisées séparées : Jarvis répartit les analyses, reçoit les
rapports et garde l’exécution des actions externes. Deux spécialistes peuvent
travailler en parallèle, avec profils, outils autorisés et budget commun.

L’univers central donne accès aux agents par des planètes ; les étoiles
filantes représentent des échanges enregistrés. **PILOTAGE** regroupe l’équipe,
les travaux programmés, les rapports persistés et la configuration des appels.
Le flux graphique classique reste sélectionnable.

La veille est désactivée au départ. Une fois autorisée, elle recherche et
prépare sur un PC éveillé avec Jarvis ouvert. Les appels et les demandes reçues
par téléphone passent par un service MCP vocal à connecter ; aucune ligne
téléphonique n’est fournie ou simulée par cette version.

Voir le [guide V17 et les essais Windows](docs/V17_ORCHESTRA_SPACE_WATCH.md).

## V16 — espace personnel et gestion quotidienne

La branche `feature/personal-workspace-daily-v16` ajoute **QUOTIDIEN** à
l’interface : tableau de la journée, activités et échéances, profil personnel,
préparations d’actions et état des services. Les domaines quotidien, football,
MS Football et projets logiciels partagent le même stockage local versionné.
Jarvis peut consulter et modifier ces activités avec des outils dédiés.

Le profil reste vide au départ ; son inclusion automatique dans les requêtes
est un choix enregistré par l’utilisateur. Les services pourront être reliés
aux serveurs du centre MCP existant, y compris sous un identifiant personnalisé.
Une préparation reste locale jusqu’à une véritable exécution via les outils
autorisés du service. Aucune connexion ni notification permanente n’est simulée.

Voir les [parcours, architecture et essais V16](docs/V16_PERSONAL_WORKSPACE.md).
Les corrections V15 et les réglages existants sont conservés.

## Correctif V15 — mémoire utilisable et actions quotidiennes

La branche `fix/daily-use-context-tools-v15` corrige les régressions observées
sur V14 `0c10cde` : le fait explicitement mémorisé rejoint la conversation et
reste disponible lors du passage automatique à une mission composée. Un outil
sélectionné reste présent dans la requête du modèle malgré la réduction du
contexte ; un budget insuffisant suspend la requête avant l’API.

L’interface affiche une suspension lorsqu’un tour reste incomplet. Le profil
Full, les données locales et la cascade de cerveaux existante sont conservés.
Voir la [recette ciblée V15](docs/V15_DAILY_USE_REGRESSIONS.md). La validation
automatique ne remplace pas les essais réels sur le poste Windows.

## Socle V14 — profils d’agents et contexte par mission

La branche `feature/agent-profiles-context-v14` prolonge la V13 `7569493`.
**ARCHITECTURE > Profils** permet de modifier les responsabilités, compétences,
sélections d’outils et budgets. Les paramètres sont versionnés et appliqués aux
étapes de délégation supervisée, sans accorder de droit MCP ni démarrer de worker.

Un changement de mission sépare l’historique envoyé au modèle tout en conservant
les checkpoints. Les requêtes disposent d’un contrôle de taille avant l’API ;
les observations historiques restent relisibles par référence. La cascade et
les protections des outils sont conservées. Le journal affiche des estimations,
distinctes des compteurs de tokens renvoyés par les fournisseurs.

Voir les [contrats, limites et essais V14](docs/V14_AGENT_PROFILES_CONTEXT.md).
La suite doit d’abord valider les usages quotidiens et métier complets avant
d’étendre les workers autonomes.

## Socle V13 — architecture visible et corrections réelles

La branche `feature/architecture-console-live-fixes-v13` prolonge V12 `c5d0cdd`.
La recette utilisateur a confirmé la mémoire après redémarrage et révélé trois
défauts reproduits puis corrigés : contexte de contrôle capturé par un wrapper,
ouverture de recherche bloquée et nouveau document écrit dans un ancien buffer.

L’onglet **ARCHITECTURE** montre le graphe de Jarvis, les profils d’agents,
compétences, outils, connecteurs et développements prévus. Il distingue le
runtime partagé, les rôles déclarés et les fonctions planifiées. Le contrôle
affiche les préférences Skills/Learning et un bouton **Nouvelle demande**.

Lire l’[étude d’architecture et son plan de migration](docs/JARVIS_ARCHITECTURE_V13.md),
puis l’[analyse des tests et la recette ciblée](docs/V13_LIVE_FIXES.md).
Le panneau ne démarre aucun worker autonome et ne modifie pas les droits MCP.

## Socle V12 conservé

La branche `feature/autonomous-mission-reliability-v12` poursuit le dernier
checkpoint `fix/mcp-blocker-evidence-report-v1` (`53f7999`).

- Suspension immédiate après un résultat MCP incertain, avec identification de
  la tentative à examiner. Les autres actions du lot ne sont pas exécutées.
- Conservation bornée des réponses d'outils entre tours, avec les appels et
  réponses associés, pour poursuivre une mission sans tout redécouvrir.
- Réponses MCP volumineuses signalées comme partielles ; passages conservés
  relisibles localement quand le contexte efficient est actif.
- Confirmations cohérentes sur Chat Completions et Responses, reçus précis de
  quotas/cooldowns et arrêt des sélections d'outils sans progrès.
- Paramètres et contenu MCP exclus des journaux diagnostiques du runtime.
- Profil de recette `-Full` : mémoire V5, Chrome habituel et Windows UIA/OCR,
  avec version affichée et contrôle des dépendances avant démarrage.

Voir le [rapport V12 et ses limites](docs/V12_MISSION_RELIABILITY.md).
Ce candidat nécessite encore une recette Windows réelle et des comptes connectés.

## Capacités et configuration

| Domaine | Implémentation existante | Configuration / preuve attendue |
| --- | --- | --- |
| Cerveaux | Cerebras principal, secondaire, Groq ; cerveaux supplémentaires configurables | Clés personnelles et Brain Manager ; ordre existant préservé |
| Conversation | Chat texte, voix et TTS de secours | Micro coupé en mode texte ; essai audio réel |
| Chrome | Observation DOM, navigation, écriture et onglets ciblés | Extension Browser Bridge dans le profil Chrome habituel |
| Windows | Découverte d'applications/fichiers, UIA et perception visuelle de secours | Python Windows et dépendances du pilote utilisé |
| Mémoire | SQLite V5, recherches et outils directs de l'agent | Aucun enregistrement personnel sans demande explicite |
| Quotidien | Profil, activités, échéances et préparations SQLite ; interface et outils Jarvis | Fuseau et profil choisis par l’utilisateur ; agendas externes via leurs services |
| Missions | Checkpoints, preuves et reprise contrôlée | Effets incertains examinés avant reprise ; pas de replay automatique |
| MCP | Centre de connexion, découverte et autorisations par service/outil | Connexion et droits de l'opérateur |
| MS Football | Outils métier et passerelle déjà présents | Instance et accès configurés ; essais métier réels nécessaires |
| Skills / apprentissage | Opt-ins séparés | Désactivés par défaut ; ce candidat ne les active pas |

## Installation et lancement Windows

Utiliser Python 3.12 dans l'environnement habituel de Jarvis. Les clés restent
dans le fichier `.env` existant ou le coffre système ; elles ne vont pas dans Git.

```powershell
python -m pip install -r requirements.txt -r requirements-mcp.txt -r requirements-grounding.txt
```

Le lanceur existant charge le `.env` indiqué, vérifie les dépendances et le
Browser Bridge, puis démarre l'interface. Pour une copie de test distincte :

```powershell
$source = 'D:\Django_Projects\jarvis-main\jarvis-main'
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath "$source\.env"
```

Ajouter `-CheckOnly` contrôle les dépendances sans ouvrir l'interface, connecter
Chrome ni appeler les modèles. Le profil `-Full` demande Windows, UIA et OCR ;
il désactive le modèle de grounding ainsi que Skills/Learning pour cette session,
sans réécrire les préférences. Les réglages de cerveaux, données et droits MCP
existants sont conservés. Le lancement P0 sans `-Full` reste disponible.
Voir la [recette Windows V12](docs/V12_WINDOWS_TEST.md) pour la mise à jour et
les premières missions, et la [configuration du bridge](docs/P0_WINDOWS_ACCEPTANCE.md).

## Validation automatique

```powershell
python scripts/run_final_regression.py
node --test tests/browser_bridge_replay.mjs
```

Le runner Python isole les données et désactive les accès aux comptes réels.
Sur Linux, les contrôles Windows sont signalés explicitement comme non exécutés.
Une CI verte ne certifie pas une mission sur le PC de l'utilisateur.

La [recette historique des capacités](docs/P0_HISTORICAL_CAPABILITY_GATES.md)
couvre notamment Chrome/YouTube, les documents Windows, la découverte d'un
installateur, la mémoire après redémarrage et la cascade de cerveaux.

## Ancien script d'accueil

`jarvis.py` conserve le scénario d'accueil par double claquement de mains.
Le point d'entrée de l'assistant personnel actuel est `run_jarvis.py` et les
lanceurs du dossier `scripts/`.
