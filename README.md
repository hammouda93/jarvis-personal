# Jarvis Personal

Assistant personnel Windows : conversation texte et voix, navigateur Chrome
habituel, outils Windows, mémoire SQLite et services MCP explicitement connectés.
Le même runtime choisit les outils et suit les missions. Un succès d'outil reste
distinct de la preuve que l'objectif entier est atteint.

## Candidat V13 — architecture visible et corrections réelles

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
