# V10D : Regressions Live Et Projection Durable

Reprise du 2026-10-10 (Africa/Lagos). Base locale/distante 4e9e330,
branche isolee `codex/live-regressions-v10d`, PR brouillon
[#22](https://github.com/hammouda93/jarvis-personal/pull/22), base integration
V10C. Aucun merge stable ; 699b7eb, 9C, main, worktrees et journaux preserves.

## Preuves Et Limites

Les succes Windows V10C (texte/micro, TTS Windows, stockage/restart, recherches
brutes/date/periode, certains fallbacks Primary/Secondary/Groq) sont rapportes
par l'utilisateur dans son brief. Codex ne les a pas rejoues sur le desktop.
Les journaux locaux retrouves sont anterieurs a cette recette : ses traces
completes restent a fournir pour diagnostiquer exactement les anciens timeouts.
Les tests nouveaux reproduisent les sorties structurees rapportees, sans compte.

## Corrections Livrees

- Les recherches portant une destination dans/within/in/inside ou une source
  non web apres sur restent sous le cerveau conversationnel. Pas de liste
  d'exceptions par application. Google/YouTube et recherche contextuelle simple
  conservent leur chemin historique ; les missions composees restent supervisees.
- Les gros resultats memoire restent JSON valide. Les sources brutes passent
  avant les projections ; omissions explicites, pas de JSON coupe en plein texte.
- Un resultat brut retrouve n'est pas ignore simplement parce que hits est vide.
  Une conclusion negative contredite par les sources locales est remplacee par
  des citations identifiees, sans inventer l'extraction d'un fait. Une recherche
  incomplete/ambigue/echouee ne devient pas un agenda vide. Le garde final ne
  reinterprete pas les conclusions d'une mission contenant d'autres sources.
- Le runner neutralise les opt-ins V10 herites avant l'import du runtime.
  La CI transmet volontairement les options live pour verifier cette isolation.
- La reponse a une ancienne question navigateur ne contourne plus le controle
  de destination ; une reponse simple ("Piano") conserve le raccourci historique.

## Fournisseurs Et Continuite

Les reponses tronquees/filtrees ou les batches avec identites manquantes,
arguments absents, JSON ambigu, non-objet ou non fini sont refuses AVANT le
premier dispatch. Aucune mutation partielle, fabrication d'arguments vides ou
reprise automatique n'est introduite. Le tour reste incomplet et la supervision
conserve ses preuves ; l'utilisateur doit verifier les effets deja produits.

Une observation navigateur recente ne remplace plus le contexte d'une mission
supervisee, ses permissions, une approbation en attente ou un grounding de session.
Le compactage navigateur ordinaire reste disponible. Les budgets ne sont pas
augmentes pour masquer une requete trop grande : refus preflight toujours visible.

Le runtime Cerebras/Groq enregistre le fournisseur du dernier retour API observe,
la voie primary/secondary/groq_fallback et l'usage fourni, pas le fournisseur
simplement configure. La console affiche ces valeurs ; un usage manquant n'est
pas remplace par celui d'une ancienne reponse. Un test du vrai chemin local,
avec clients SDK simules, traverse 402 puis 429 puis Groq avec contexte et preuve
d'outil preserves, sans rejouer la mutation. Ce n'est PAS un appel fournisseur live.

## Projection Differee Opt-in

`JARVIS_MEMORY_ASYNC_PROJECTION_ENABLED=1` exige le mode outils V10B.
Le launcher propose `-AsyncMemoryProjection`, sans changer ses modes existants.
La voie V5 historique et la projection synchrone restent disponibles par defaut.

La note originale, son admission explicite et son job sont commits ensemble
dans le SQLite existant. L'accuse de stockage n'attend aucun modele d'indexation.
Un worker derive uniquement le sidecar ; il ne devient pas un second cerveau.
Les lectures ne lancent jamais de projection ou de classification supplementaire.

La file est persistante, avec cle unique source/hash/parser/politique, lease
exclusive, publication atomique des faits et completion du job. Source changee
ou lease reprise : publication tardive refusee. Trois reprises de crash maximum.
Un echec connu ne boucle pas : sa reprise exige une demande operateur bornee.
Le texte brut n'est jamais supprime. Une projection terminee sans faits reste
distincte d'un index complet ; les lectures exposent coverage et les erreurs.

La politique de projection epingle fournisseur, chaine autorisee, modeles,
endpoints et consentement cloud sous empreinte, sans enregistrer de cle API.
Changer cette politique ne consomme pas automatiquement les anciens jobs.
L'admission legacy n'est pas transformee en admission explicite lors d'une reprise.
Le worker est local au processus ; Jarvis ferme ne traite pas la file. Apres un
arret brutal, un job running attend l'expiration de sa lease (300 s par defaut).
Ce n'est pas un scheduler de rappels, un service Windows ou un agent cloud.

## Contrats MCP Et Reprise

Les deux chemins MCP (registre live et adaptateur kernel passif) exigent un
statut booleen explicite du transport Jarvis. Une reponse manquante/malformee,
un succes contredit par outcome_unknown, un timeout ou une erreur apres dispatch
restent incertains, jamais "aucun effet". Le SDK officiel convertit le resultat
protocolaire ; ce contrat n'impose pas un champ success au serveur MCP distant.
Les bodies d'exceptions ne deviennent pas des diagnostics partageables du kernel.
Provenance serveur/outil/tentative epinglee ; donnees distantes jamais preuve finale.

Le journal MCP bloque durablement le meme outil apres effet inconnu ou reservation
en vol, meme Hermes OFF, apres redemarrage et au-dela du cooldown. Les inconnus
ne sont pas purges par la retention ordinaire. La revue explicite est dans
MCP > Services > Journal : selectionner la tentative, verifier l'effet dans le
service puis confirmer avec le bouton de revue (defaut Non). Elle ajoute un
acknowledgement local sans effacer l'issue initiale, autoriser un nouvel outil,
marquer la mission terminee ou relancer l'appel. Une reservation recente n'est
pas liberable ; un arret brutal exige attente et verification externe.

Hermes et Supervisor conservent leurs protections independantes : cette revue
MCP ne resout pas automatiquement une action inconnue dans leur propre registre.
Tous les appels ulterieurs exigent toujours une nouvelle confirmation exacte.
Le catalogue, les comptes personnels et OAuth ne sont pas connectes par ces tests.

## Recette Isolee

Dans PowerShell, utiliser le meme dossier de donnees lors du redemarrage :

```powershell
$env:JARVIS_AI_PROVIDER = 'cerebras'
$env:JARVIS_MEMORY_AGENT_TOOLS_ENABLED = '1'
$env:JARVIS_MEMORY_SCOPE_GUARD_ENABLED = '1'
$env:JARVIS_DATA_DIR = Join-Path (Get-Location) '.cache\live-v10d-recette'
.\scripts\run_jarvis_foundations_v4.ps1 -PythonExe '.\.cache\t\Scripts\python.exe' -SemanticMemoryV5 -AsyncMemoryProjection
```

1. Bonjour, puis question memoire sans dire "memoire persistante".
2. Retenir une note synthetique et verifier un accuse raw_saved/projection_queued
   rapide. Mesurer separement la latence du cerveau et celle de l'outil SQLite.
3. Retrouver cette note avant projection, fermer/reouvrir et retrouver a nouveau.
4. Avec projection locale indisponible, verifier que la note reste lisible et
   que les lectures par date ne concluent pas abusivement "aucun evenement".
5. "Cherche dans ma memoire", "dans mes fichiers", "dans une application inconnue"
   : aucun Google implicite. Une source indisponible doit etre annoncee telle quelle.
6. Browser Core explicitement active : recherche YouTube, premier resultat observe,
   retour et fermeture ciblee, avec des onglets temoins. Aucun desktop affirme ici.
7. Fallback reel lorsque le fournisseur est indisponible : verifier le contexte
   et l'absence de deuxieme ecriture, pas provoquer une depense/quota artificielle.
8. Skills et apprentissage restent OFF ; aucune connexion MCP personnelle inventee.

Inspection et reprise explicites de la file du dossier de test :

```powershell
.\.cache\t\Scripts\python.exe -m jarvis_agent.semantic_memory_cli --db '.cache\live-v10d-recette\memory.sqlite3' projection-jobs
.\.cache\t\Scripts\python.exe -m jarvis_agent.semantic_memory_cli --db '.cache\live-v10d-recette\memory.sqlite3' projection-jobs --retry-failed --process
```

Ajouter `--enqueue-missing` uniquement pour autoriser l'indexation de notes
anciennes non indexees. Par defaut local-first, aucun --allow-cloud implicite.
Le fournisseur/modele doit correspondre a la politique des jobs. Un autre mode
necessite cette nouvelle demande explicite ; aucun effacement/rejeu des notes.

## Validation

Premier checkpoint 8cbd9ec : 928 tests Windows locaux, Node 45/45;
[CI 38042159567](https://github.com/hammouda93/jarvis-personal/actions/runs/38042159567)
Windows/Ubuntu SUCCESS, isolation V10 active dans les deux jobs.
Checkpoint projection 7e24521 : 943 passes Windows, Ubuntu 938 passes et
5 exclusions explicites ; Node 45/45 sur chaque OS.
[CI 38042834584](https://github.com/hammouda93/jarvis-personal/actions/runs/38042834584)
verte, logs verifies. Checkpoint fournisseurs 5f09e7d : Windows 958 passes,
Ubuntu 953 passes + 5 exclusions explicites, Node 45/45 sur chaque OS,
[CI 38043762908](https://github.com/hammouda93/jarvis-personal/actions/runs/38043762908)
verte, logs verifies.
17 tests de file et 9 tests fournisseurs, plus regressions du worker et Qt.
DPAPI reste teste hors sandbox, jamais desactive.
L'affichage expose les vrais counts queued/running/failed, sans note privee.
Rendus Qt hors ecran controles a 360 et 680 px avec Segoe UI chargee explicitement
(la plateforme offscreen ne decouvre aucune police systeme par defaut ici).
Les nouveaux champs se replient ; ce controle n'est pas une recette desktop/DPI.
Lot MCP : 12 nouveaux tests reproduisent les enveloppes invalides et les reprises
incertaines. 111 tests MCP et 53 tests kernel passes. Regression cumulative locale
970 passes, aucun echec/erreur/skip ; nouvelle CI encore a observer.

## Hermes Et Sources

Main Hermes revalide le 2026-10-10 : a7b2ba02e3b9bb95d0cac87a6312cc82a3bc3eed.
[memory_tool.py](https://github.com/NousResearch/hermes-agent/blob/a7b2ba02e3b9bb95d0cac87a6312cc82a3bc3eed/tools/memory_tool.py)
et [memory_tool_store.py](https://github.com/NousResearch/hermes-agent/blob/a7b2ba02e3b9bb95d0cac87a6312cc82a3bc3eed/tools/memory_tool_store.py)
consultes : cibles epinglees, ecriture preparee versus appliquee, refus de perte
de sources. Ces principes sont reutilises, aucun code amont copie. Les fichiers
MEMORY.md, prompts figes et gates fail-open ne remplacent pas SQLite/Jarvis.
[Transactions SQLite Python](https://docs.python.org/3.12/library/sqlite3.html)
restent la primitive de concurrence et publication, pas une nouvelle plateforme.
[Cerebras tool use](https://inference-docs.cerebras.ai/capabilities/tool-use)
et [Groq tool use](https://console.groq.com/docs/tool-use/overview) consultes
pour le contrat local des identites, arguments et messages outils.
Hermes [mcp_tool_content.py](https://github.com/NousResearch/hermes-agent/blob/a7b2ba02e3b9bb95d0cac87a6312cc82a3bc3eed/tools/mcp_tool_content.py)
et [mcp_tool_errors.py](https://github.com/NousResearch/hermes-agent/blob/a7b2ba02e3b9bb95d0cac87a6312cc82a3bc3eed/tools/mcp_tool_errors.py)
consultes : bornes de contenu et distinction erreur protocole/execution.
[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
consulte ; les annotations du serveur ne conferent jamais de confiance locale.

## Feuille De Route Globale

| Etape | Code/contrats actuels | Validation restante |
| --- | --- | --- |
| A Routage universel | Sources qualifiees preservees, registre existant | Choix reel du modele, missions multi-app et inconnues |
| B Memoire | Raw/source/coverage, file durable opt-in | Recette async, edits versionnes/preview/CAS, doublons, recurrences/timezones |
| C Fournisseurs | 402/429 preserves, batches incomplets refuses, provenance/usage reels, contexte supervise protege | Recette API reelle, contextes longs et optimisation continue |
| D MCP/services | SDK/OAuth/coffre/permissions, provenance et inconnus bloques/revus durablement | Comptes Gmail/Calendar/Drive/GitHub/WhatsApp/MS Football reels non connectes ici |
| E Missions | Planification/verification/continuite existantes | Missions composees reelles, contradictions de sources |
| F Computer use | Chrome Bridge, UIA/CUA/refs preserves | Recette Windows nouvelle branche, installation complete |
| G Autonomie | Supervision bornee et recovery existants | Longues missions et reprise reelles |
| H Scheduler | Queue kernel en memoire | Scheduler personnel horaire/durable non livre |
| I Isolation/cloud | Contrats existants seulement | Session Windows/navigateur/agent cloud isoles non livres |
| J Skills/learning | OFF, contrats/versionnement existants | Recette explicite apres moteur general |
| K Interface | Etats reels, counts de projection, voie fournisseur observee et usage nullable | Desktop/DPI et missions reelles |

Jarvis final generaliste n'est pas declare termine par ces lots.
