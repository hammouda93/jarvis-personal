# Tester le candidat V12 sur Windows

La branche de recette est `feature/autonomous-mission-reliability-v12`.
Fermer l'autre instance Jarvis, garder Chrome ouvert avec le profil habituel.
La recette utilise les clés, la mémoire et les connexions MCP existantes.

## Mettre à jour la copie de test

Depuis PowerShell, dans la copie de test déjà créée :

```powershell
cd D:\Django_Projects\jarvis-main\jarvis-mcp-test
if (git status --porcelain --untracked-files=no) { throw 'Modifications locales à préserver avant de changer de version.' }
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'Échec de récupération GitHub.' }
git switch --detach origin/feature/autonomous-mission-reliability-v12
if ($LASTEXITCODE -ne 0) { throw 'Échec de sélection de la V12.' }
git log -1 --oneline

$python = 'C:\jv312\Scripts\python.exe'
& $python -m pip install -r requirements.txt -r requirements-mcp.txt -r requirements-grounding.txt
if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances incomplète.' }
$source = 'D:\Django_Projects\jarvis-main\jarvis-main'
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath "$source\.env"
```

Le lancement affiche le commit utilisé et crée un journal
`.cache\p0_live_YYYYMMDD_HHMMSS.log`. Il vérifie les dépendances Windows et le
Browser Bridge avant d'ouvrir l'interface. En cas de dépendance manquante, le
diagnostic affiche une commande de réparation adaptée. En cas de bridge absent
ou déconnecté, suivre la [préparation unique de Chrome](P0_WINDOWS_ACCEPTANCE.md#préparation-unique-du-browser-bridge).
Ne réinstaller l'extension que si son chemin, son ID ou son native host a changé.

Pour contrôler seulement les dépendances :

```powershell
.\scripts\run_p0_acceptance.ps1 -Full -CheckOnly -EnvPath "$source\.env"
```

Ce contrôle n'ouvre pas l'interface et ne sollicite ni Chrome ni modèle. Un
contrôle réussi ne prouve pas que les comptes MCP sont connectés ou qu'une
mission réelle aboutira.

## Profil temporaire

Memory Core/V5 et outils mémoire directs, continuité des missions, contexte
efficient, Browser Core sur Chrome habituel et Computer Core UIA/OCR sont actifs.
Le navigateur CDP historique et le modèle de grounding sont désactivés.
Skills et Learning démarrent désactivés dans ce processus, même si d'anciennes
préférences les activaient. Un changement explicite dans leur panneau reste
possible. Les fichiers de préférences ne sont pas réécrits au lancement.

Les clés, modèles conversationnels, cascade Cerebras/Groq/cerveaux
supplémentaires, chemins de données et permissions MCP ne sont pas remplacés.
Aucun service MCP n'est connecté ou autorisé automatiquement par ce profil.

## Premières missions

Donner les demandes une par une dans Jarvis. Vérifier les actions et les
observations réelles, pas seulement la fluidité de la réponse.

1. **GitHub connecté** : « Inspecte mon dépôt GitHub jarvis-personal : retrouve
   les dernières branches, les derniers commits, les pull requests et l'état des
   tests, puis explique-moi ce qui reste à développer. » Attendu : propriétaire
   observé via le service, inventaires et CI réellement lus, réponse partielle
   explicite si une pagination ou un accès manque. Demander ensuite « Parmi ces
   changements, lesquels sont prêts à être testés ? » pour vérifier la continuité.
2. **Mémoire après redémarrage** : « Retiens que mon code de recette V12 est
   ORION-7316. » Attendre le reçu d'écriture, fermer Jarvis et relancer la même
   commande. Demander « Quel est mon code de recette V12 ? » Attendu : code
   retrouvé dans SQLite avec une recherche réelle. Choisir un autre code si
   celui-ci a déjà servi à une ancienne recette.
3. **Chrome** : « Ouvre une recherche sur les dernières nouvelles de Python,
   lis le premier résultat pertinent et résume-le avec son URL. » Attendu : liens
   observés, navigation réelle et URL exacte. Demander ensuite de fermer
   uniquement l'onglet de cette recherche ; les autres onglets doivent rester.
4. **Windows** : « Ouvre un nouveau document Bloc-notes, écris “Test Jarvis V12 :
   français, العربية, ✓”, puis relis le contenu affiché. Laisse le document
   ouvert sans l'enregistrer. » Attendu : fenêtre observée, cible éditable prouvée,
   saisie et relecture réelles ; aucune annonce de succès sans preuve.

Un quota doit produire un cooldown visible ou la bascule configurée. Une
tentative externe incertaine doit suspendre la mission avec sa raison ; ne pas
répéter un envoi pour forcer un succès. Les essais automatiques simulent déjà ces
cas sans consommer les quotas des comptes.

Après un échec, conserver le commit, la demande, la réponse, le `mission_id` et
les dernières lignes pertinentes du journal en retirant clés et contenus privés.
Les agents cloud permanents et les missions composées arbitraires ne sont pas
annoncés comme terminés par cette recette.
