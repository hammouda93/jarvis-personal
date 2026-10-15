# Recette Windows réelle — P0 V11A

Ces essais utilisent votre configuration et votre mémoire existantes. Fermer l'autre instance Jarvis avant le lancement. La commande charge le `.env` du checkout, ou celui du dépôt principal quand elle est lancée depuis un worktree ; elle ne copie ni n'affiche les clés. Si votre installation V11A de test possède son propre `.env`, passer `-EnvPath 'chemin\vers\.env'` une fois dans votre commande de lancement.

## Préparation unique du Browser Bridge

Ouvrir Chrome avec le profil normal. Dans `chrome://extensions`, charger l'extension non empaquetée depuis **ce checkout P0**, dossier `extensions\personal-ai-browser-bridge`. Relever son ID et réutiliser l'installateur existant :

```powershell
.\scripts\install_browser_bridge.ps1 -ExtensionId 'ID_DE_L_EXTENSION_P0' -PythonExe 'C:\jv312\Scripts\python.exe'
```

Cliquer ensuite sur l'icône « Personal AI Browser Bridge » pour connecter l'extension. L'installation enregistre ce native host pour l'utilisateur courant ; si une autre copie de l'extension est active, la désactiver pendant la recette. Une extension chargée depuis un nouveau chemin peut avoir un nouvel ID et doit être appairée. La vieille extension ne fournit pas les nouvelles identités de génération nécessaires aux certificats P0. Cette préparation n'a pas été exécutée automatiquement par Codex.

## Une commande de lancement

Depuis le checkout P0 :

```powershell
.\scripts\run_p0_acceptance.ps1
```

Le script utilise `C:\jv312\Scripts\python.exe`, contrôle les dépendances et la connexion du bridge, puis ouvre Jarvis. Le profil temporaire du processus active Memory Core, mémoire V5 avec outils/direct et garde de scope, continuité des missions et Browser Core. Le navigateur CDP historique est désactivé pour cette recette. Les réglages du fournisseur, les chemins de données et les permissions existantes sont conservés. Le script n'active pas Skills ni apprentissage et ne lance pas de validation de clé API.

Le journal est dans `.cache\p0_live_YYYYMMDD_HHMMSS.log`. Options utiles si l'installation diffère : `-PythonExe 'chemin\python.exe'`, `-EnvPath 'chemin\.env'`, `-BrowserBridgeConfig 'chemin\bridge.json'`. `-CheckOnly` valide les dépendances du lanceur sans ouvrir Jarvis, solliciter Chrome ou appeler un modèle ; il ne valide pas la recette LIVE.

Le lanceur appelle `scripts/p0_acceptance.py` comme fichier Python pour fonctionner aussi avec Windows PowerShell 5.1. Il ne transmet plus de source Python via `-c` : cette méthode perdait des guillemets et provoquait `SyntaxError: '(' was never closed`. La correction du lanceur ne nécessite pas de réinstaller l'extension ni de réactiver un environnement virtuel.

Le candidat V12 ajoute `-Full` à ce même lanceur pour inclure Computer Core
UIA/OCR et vérifier ses dépendances Windows. Ce profil désactive le modèle de
grounding et démarre Skills/Learning à l'arrêt pour la session sans réécrire
les préférences. Le lancement P0 ci-dessus conserve son comportement. Voir la
[recette V12](V12_WINDOWS_TEST.md) pour la commande complète.

## A. Écriture mémoire et redémarrage

Donner cette demande naturelle une seule fois :

> Retiens que mon code de recette Jarvis est NEBULA-4827.

Attendre la fin du tour. Vérifier ensemble : réponse positive de mémorisation durable, événement mémoire `PERSISTED_VERIFIED`, mission certifiée avec portée SQLite et trace `GOAL_CERTIFIED` pour cette mission. Aucun message d'échec de relecture ne doit accompagner une certification positive. Si une relecture échoue réellement, l'état doit rester non certifié, sans répétition automatique de l'écriture.

Fermer complètement Jarvis et relancer la même commande. Demander :

> Quel est mon code de recette Jarvis ?

Attendu : `NEBULA-4827` retrouvé depuis la mémoire persistante, avec reçu de recherche dans les outils. Une réponse du modèle seule ne suffit pas à démontrer le rappel durable. Ne pas effacer la base pour effectuer l'essai. Si cette phrase avait déjà été enregistrée dans la recette précédente, ne pas confondre cette ancienne ligne avec le nouveau reçu d'écriture.

## B. Navigation et certificat global

Dans Chrome normal connecté, donner la demande du brief :

> Ouvre https://msfootball-1a882b44ed52.herokuapp.com/gestion_joueurs/, inspecte le menu latéral, ouvre-le si nécessaire, puis clique sur Joueurs. Vérifie la page affichée et donne-moi son URL exacte.

Attendu si le service et la session sont disponibles : inspection réelle du menu, ouverture si elle est nécessaire, clic sur le lien observé, relecture indépendante et URL finale exacte. La recette précédente indiquait `/gestion_joueurs/players/` ; la preuve doit venir de l'onglet courant. Le même `mission_id` doit relier les étapes, la réponse finale et le certificat navigateur. Une simple ouverture du menu ne doit pas provoquer une fin annoncée comme réussite globale ni exiger systématiquement « continue ».

En cas de pause réelle, donner :

> Continue la mission.

Attendu : objectif original conservé, nouveau grounding en lecture, aucun nouvel `open_url` ni clic déjà envoyé rejoué. Les preuves des deux tours peuvent certifier le résultat seulement avec une lecture actuelle cohérente. Si vous redémarrez l'application pendant une pause, sélectionner/reprendre la mission persistée dans le panneau de missions si elle n'est pas attachée automatiquement ; ce parcours UI est à valider réellement.

Un login, un service indisponible, une page inattendue ou un résultat incertain doit produire une pause ou un état non certifié, avec raison. Le badge « TOUR TERMINÉ · OBJECTIF NON CERTIFIÉ » reste ambre ; « OBJECTIF CERTIFIÉ » est réservé à la preuve complète.

## C. Récupération, permissions et interface

- Si un 429 survient naturellement, vérifier le cooldown et la bascule configurée, puis le maintien du goal et des reçus. Ne pas provoquer artificiellement du trafic payant pour cette recette ; le scénario reproductible de quota est déjà simulé dans les régressions.
- Après changement de DOM ou d'onglet, une ancienne référence doit être refusée et une nouvelle observation requise. Aucun effet incertain ne doit être rejoué pour obtenir un certificat.
- Une mutation sensible conserve ses permissions et sa confirmation existantes ; une lecture ordinaire ne doit pas recevoir une confirmation supplémentaire issue de P0.
- En mode texte, confirmer l'arrêt du microphone et la disponibilité de la réponse vocale. Tester les badges, le scroll et le redimensionnement au facteur d'échelle Windows utilisé.

Pour chaque essai, conserver la version/branche, le `mission_id`, le journal, la réponse, l'état du panneau et l'URL réellement visible. Éviter de partager des clés ou des contenus privés dans ces preuves. Mettre à jour la roadmap avec le résultat observé, y compris les pauses et échecs. Une CI verte, un replay ou `-CheckOnly` ne valide pas ce parcours sur le Windows réel.
