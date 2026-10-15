# Jarvis Personal

Assistant personnel Windows : conversation texte et voix, navigateur Chrome
habituel, outils Windows, mémoire SQLite et services MCP explicitement connectés.
Le même runtime choisit les outils et suit les missions. Un succès d'outil reste
distinct de la preuve que l'objectif entier est atteint.

## Candidat V12 — 15 octobre 2026

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
python -m pip install -r requirements.txt -r requirements-mcp.txt
```

Le lanceur existant charge le `.env` indiqué, vérifie les dépendances et le
Browser Bridge, puis démarre l'interface. Pour une copie de test distincte :

```powershell
$source = 'D:\Django_Projects\jarvis-main\jarvis-main'
.\scripts\run_p0_acceptance.ps1 -EnvPath "$source\.env"
```

Le mode `-CheckOnly` contrôle les dépendances sans ouvrir l'interface, connecter
Chrome ni appeler les modèles. Les réglages de cerveaux et les droits MCP
existants sont conservés. Pour Computer Core/OCR, voir la
[recette de l'environnement](docs/P0_WINDOWS_ACCEPTANCE.md) et le lanceur
`scripts/run_jarvis_foundations_v4.ps1`.

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
