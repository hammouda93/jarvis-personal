# V15 — rétablir les usages quotidiens

Base : V14 `0c10cde670f7ee8ebe6249dc7277c6bea8d18729`.
Branche : `fix/daily-use-context-tools-v15`.

## Résultats de la recette V14

Le journal du 15 octobre montre un décalage entre les tests automatiques et
l’expérience réelle. SQLite mémorise « Weekend » mais le modèle ne reçoit pas
ce fait au tour suivant. Les demandes YouTube puis Cursor sélectionnent des
capacités sans exécuter les actions. Bloc-notes s’ouvre par le chemin direct.
La bascule Cerebras secondaire fonctionne après le quota primaire ; elle ne
résout pas la perte de contexte ou des schémas d’outils.

| Cause reproduite | Correction |
| --- | --- |
| Le raccourci de mémoire retourne avant d’enregistrer le tour dans le runtime conversationnel | Enregistrer la source utilisateur exacte et le reçu, sans nouvel appel modèle ni nouvelle écriture SQLite |
| Une frontière de contexte attend le prochain tour modèle et saute les actions locales | Le même ajout de message humain consomme la frontière pour les deux chemins |
| L’entrée automatique en mission composée écarte le fait récent avec l’ancien historique | Garder la dernière source de mémoire vérifiée comme donnée de session bornée, avec son identifiant et indication de troncature |
| Le contrôle de budget retire les outils explicitement sélectionnés | Les conserver dans les passes de réduction, y compris Groq et les cerveaux supplémentaires ; suspendre avant API si le budget reste insuffisant |
| `already_exposed` décrit le catalogue avant réduction | Mettre à jour l’exposition avec les schémas effectivement admis pour la requête |
| Un tour suspendu sans mission attachée repasse en `success` | Transmettre la suspension au graphique ; afficher « Demande suspendue — résultat incomplet ». Une conversation disponible affiche « Prêt » |

La source récente est de la donnée, jamais une nouvelle instruction ni une
autorisation. Son reçu est validé et son empreinte comparée au texte utilisateur.
Cette projection conserve au plus 2400 caractères de la dernière source ; la
source complète reste dans SQLite et dans le tour conversationnel conservé.
Une remise à zéro efface la projection de session, sans supprimer SQLite.
Une écriture ou relecture non vérifiée reste signalée comme telle dans la
conversation ; aucun reçu de succès n’est fabriqué.

La réduction change seulement l’exposition des schémas. Les confirmations,
permissions MCP, verrous d’effets incertains, contrôles UIA/DOM et plafonds des
fournisseurs restent appliqués. Une sélection de capacités n’est pas une action
exécutée : le journal utilise maintenant `tool_results` plutôt que
`completed_actions` pour le nombre de résultats reçus.

## Vérifications

Les tests de reproduction traversent le vrai assemblage des requêtes, la
sélection locale, l’admission du budget, le suivi des missions et SQLite.
Les réponses des modèles et les effets sur le système sont simulés.

- Exposition sous pression puis un seul dispatch : `open_url`, `open_file`,
  `find_file`, sur Cerebras et Groq.
- Même progression après quota primaire, via Groq ou un cerveau supplémentaire.
- Arrêt avant API si le schéma sélectionné ne tient pas dans le budget.
- Mémorisation exacte puis présence de « Weekend » et du reçu dans la requête
  suivante, avec et sans contexte injecté, y compris à l’entrée d’une mission.
- Relecture mémoire en erreur : fait et incertitude conservés, sans doublon.
- Affichage de suspension après un tour inachevé ; disponibilité ordinaire.

Validation locale du candidat : **1423 tests examinés**, dont **1413 réussis**
et **10 exclusions Windows explicites**, sans échec ni erreur ; **46 tests
Browser Bridge réussis** et contrat d’import du SDK MCP officiel validé.
Ce lot ajoute neuf tests à V14, dont plusieurs cas par fournisseur et outil.
Les essais Windows ci-dessous restent nécessaires : les replays ne prouvent pas
le comportement d’un modèle réel ni celui des contrôles présents sur le poste.

## Essai Windows ciblé

Fermer Jarvis avant de changer le checkout de test. Dans PowerShell :

```powershell
$ErrorActionPreference = 'Stop'
cd D:\Django_Projects\jarvis-main\jarvis-mcp-test
if (git status --porcelain --untracked-files=no) { throw 'Modifications locales à préserver.' }
git fetch origin
if ($LASTEXITCODE) { throw 'Échec de récupération GitHub.' }
git switch --detach origin/fix/daily-use-context-tools-v15
if ($LASTEXITCODE) { throw 'Échec de sélection de la V15.' }
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath 'D:\Django_Projects\jarvis-main\jarvis-main\.env'
```

Le lanceur affiche encore « V12 full profile » : il s’agit du nom historique du
profil de démarrage. Vérifier le commit affiché et la branche sélectionnée.
Les clés et paramètres personnels ne doivent pas être copiés dans ce dépôt.

| Demande | Résultat à observer |
| --- | --- |
| « Retiens que je vais regarder le film Weekend le 20 octobre 2026 » | Une seule écriture SQLite vérifiée |
| « Ouvre YouTube et recherche le nom du film que je viens de t’annoncer » | Weekend utilisé sans redemander le titre ; outils de navigation, observation, saisie et soumission appelés réellement |
| « Qu’ai-je prévu le 20 octobre 2026 dans ta mémoire ? » | Consultation de la mémoire et du fait daté ; aucune prétention d’avoir créé un événement dans un agenda externe |
| « Ouvre Bloc-notes » | Ouverture habituelle conservée |
| « Retrouve l’installateur Cursor dans mes téléchargements sans le lancer » | Recherche de fichier réelle et chemin observé ; aucune installation lancée |

Le journal `.cache/p0_live_*.log` doit permettre de distinguer la sélection de
schémas, les vrais appels d’outils, leurs résultats et la preuve finale. Si une
étape reste en échec, conserver ce journal avec la phrase exacte et le résultat
visible. Une ouverture d’installateur ne prouve jamais une installation achevée.

## Priorités produit suivantes

La priorité est la gestion quotidienne, le football personnel, MS Football et
les projets logiciels : connaître les faits confiés, garder le fil, agir dans
les applications puis vérifier le résultat. La connaissance disponible dans
une conversation ChatGPT n’est pas automatiquement importée dans Jarvis.

Les prochains lots devront rendre les informations personnelles et activités
consultables dans l’interface, puis relier les échéances et les outils métier
réellement configurés. Chaque lot devra être accepté sur un parcours utilisateur
complet avant d’étendre le nombre de workers ou de connecteurs.
