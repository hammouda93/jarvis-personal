# Jarvis : developpement final

Date de demarrage : 2026-10-08 (Africa/Lagos).

## Reprise Live V10D Du 2026-10-10

Base retrouvee locale/distante : `4e9e330`, PR #18/#19/#20/#21 ouvertes,
non fusionnees. Nouvelle branche isolee : `codex/live-regressions-v10d`.
699b7eb, reference 9C, main et les quatre worktrees de validation preserves.
Journaux utilisateur non suivis inchanges et exclus des commits.

Le brief humain confirme sur 4e9e330 : demarrage Windows, isolation micro en
texte, TTS Windows de secours, fallback Cerebras 402/Secondary/Groq dans les
scenarios rapportes, stockage SQLite, rappel brut apres redemarrage et lectures
par date/periode. Ce sont des resultats rapportes par l'utilisateur, pas de
nouvelles executions desktop par Codex. Les logs disponibles ici sont anciens
(2026-10-04 au plus tard) ; les traces completes de cette recette recente ne
sont pas jointes. Les reproductions suivent les sorties structurees du brief.

Premier lot : recherches avec destination explicite rendues au cerveau,
pas d'exceptions par application ; raccourcis Google/YouTube preserves.
Resultats memoire compactes en JSON valide, preuve brute avant projections,
garde contre les conclusions d'absence contredites par une source ou un index
incomplet. Isolation automatique des opt-ins V10 dans le runner de regression.
8 nouveaux tests reproduisent les defauts avant correction.

Baseline : 920 tests Windows passent hors sandbox. Dans le sandbox, un seul
test DPAPI echoue (profil Windows indisponible) ; il n'est pas desactive.
Premier lot corrige : 928 tests Windows, aucun echec/erreur/skip ; Node 45/45.
CI du nouveau lot et recette desktop : encore a effectuer. Projection durable,
corrections versionnees, missions generales et scheduler restent a poursuivre.

## Reprise Memory V10 Du 2026-10-09

Nouveau brief utilisateur : recuperer le checkpoint, auditer les PR #18/#19/#20
et integrer leurs capacites sous le meme cerveau, sans reconstruire Jarvis.

- Checkpoint retrouve : `699b7eb58f6d98fc10305e93987fd216c41e7edb`, confirme
  sur GitHub et localement. La premiere tentative de push Codex avait ete refusee
  par une limite du reviewer ; la presence distante a ensuite ete verifiee,
  sans attribuer cette publication a cette tentative refusee.
- CI observee dans les logs : [37946221187](https://github.com/hammouda93/jarvis-personal/actions/runs/37946221187),
  Windows 889 passes ; Ubuntu 884 passes + 5 exclusions explicites ; Node 45/45
  sur chaque OS. Branche 9C toujours `5bdd542d3cab8b7d52ab7db9bc20167ca911e050`.
- PR #18 (`4e5e4ad`), #19 (`330743d`), #20 (`5d4e04f`) auditees, leurs
  commentaires humains relus et leurs CI Windows/Ubuntu inspectees : 893,
  903 et 910 tests respectivement, 5 exclusions Ubuntu et Node 45/45.
- Leur ancetre commun est exactement `699b7eb` : MCP, quotas, Skills OFF,
  apprentissage OFF, traces et supervision ne sont pas remplaces par ces PR.
- Branche d'integration isolee `codex/integrate-memory-v10c`, merge local
  non destructif `87f8876`, aucun conflit. Aucun merge de PR ni changement
  de la branche stable ou du checkpoint source. Les autres worktrees et les
  journaux utilisateur non suivis sont preserves.
- Corrections d'integration : lectures V5 connues classees non mutantes ;
  inventaire/date/range SQLite utilisables comme observations locales sur un
  critere approuve, jamais via un simple champ success/verified ou un outil MCP.
- Aucun scope memoire seul ni compactage de politique dans une mission
  supervisee ou un contexte injecte. Le compactage autonome conserve tous
  les messages utilisateur, systemes additionnels et appels/resultats d'outils.
  Seules les anciennes reponses libres sont omises ; budget Groq 7000 conserve.
- Tests nouveaux reproduisant les incompatibilites avant correction : echec
  des lectures classees mutations et perte de contexte/preuves reproduits.
  Apres correction : 116 tests runtime + 9 tests d'integration cibles passent ;
  regression complete finale : 920 tests Python Windows passes, 0 erreur/echec/
  exclusion ; Node 45/45.
- La premiere CI d'integration `51e6a0f` a detecte une assertion de fixture
  supposant tous les schemas au format function. Ubuntu exposait aussi le builtin
  Groq browser_search. Test corrige pour couvrir explicitement les deux formats,
  sans retrait de capacite ni modification du runtime ; regression relancee.
- Checkpoint code corrige `25d3e12626d097f987ddf806b285e1becc69d30b` publie.
  CI observee dans les logs : [37990022359](https://github.com/hammouda93/jarvis-personal/actions/runs/37990022359),
  Windows 920 passes, 0 exclusion ; Ubuntu 915 passes + 5 exclusions ; Node
  45/45 sur chaque OS, contrat SDK MCP officiel egalement passe.
- [PR #21](https://github.com/hammouda93/jarvis-personal/pull/21) brouillon vers
  la branche de developpement, sans fusion. PR #16 mise a jour pour refleter le
  checkpoint source 699b7eb et distinguer cette integration separee.
- Prochaine priorite technique : correction durable/versionnee des souvenirs,
  puis projection reprisee/non bloquante et fallback des missions generales.
  Le detail des dependances et des gates reelles figure dans l'audit V10.

La decision architecturale est preservee : V5 reste SQLite, accessible au meme
registre et a la meme conversation. Les opt-ins V10B/C restent explicites.
La projection a l'ecriture peut encore utiliser Ollama ; aucune cascade locale
complete, connexion de compte ou correction durable de souvenir n'est inventee.
Les validations humaines V10A/B ne certifient pas cette integration ni V10C sur
le desktop. Voir [audit et dependances](MEMORY_V10_INTEGRATION.md) et
[recette Windows](FINAL_WINDOWS_ACCEPTANCE.md).

## Reprise du 2026-10-09 : 9G, Opt-ins Et Centre MCP

### Centre MCP

- Acces direct par l'onglet MCP de la fenetre Jarvis. Vues Services / Catalogue /
  Ajouter, puis Capacites / Acces / Journal par serveur. Aucune connexion sur le
  thread Qt ni au demarrage.
- Interrupteur MCP persistant et applique au runtime deja construit ; outils
  natifs transmis sans modification, sans deuxieme cerveau. OFF initial en
  l'absence de consentement precedent `JARVIS_MCP_ENABLED` ou de fichier.
- Connexion/reconnexion explicites, une seule tentative, inventaire automatique
  borne des outils, ressources, templates et prompts declares. Pagination adaptee
  au SDK officiel 2.3. Pas de lecture de contenu ni execution de prompt.
- Etat honnete : deconnecte, autorisation necessaire, erreur, connexion testee
  et session fermee. Les sessions restent courtes, aucun badge de connexion
  permanente invente. Un test echoue desactive les outils du serveur.
- Bearer/PAT, OAuth et cles API `X-API-Key` / `X-Goog-Api-Key` dans le coffre
  OS, lies a l'endpoint. Changer de credential revoque le consentement des outils.
  Deconnecter conserve les secrets mais revoque les outils ; oublier les secrets
  ou supprimer le serveur purge le coffre.
- Catalogue officiel documente : Gmail, Calendar, Drive, Docs, Sheets (Google
  Workspace Developer Preview), GitHub, Maps, Hermes local. Selection/preparation
  n'ajoute aucun serveur et ne consent aucun compte. Aucun MCP WhatsApp personnel
  officiel n'est presente comme disponible.
- Les missions peuvent utiliser les outils explicitement selectionnes, via le
  registre generique et les confirmations existantes. Les sorties de serveur
  restent non verifiees, pas des preuves de completion de mission.

Tests ajoutes : inventaires SDK pagines/resource-only, bornes/cursors repetes,
connexions sans appel, etats/401/quotas, persistance/runtime gate, coffre API key,
metadata URI sans secrets en query, controles Qt et acces direct a 660 pixels.
La premiere regression a detecte un debordement avec police de remplacement ;
les boutons ont ete corriges puis les tests de geometrie relances avec succes.
Les captures Qt hors ecran ne sont PAS une recette Windows interactive.
Regression locale : 874 tests Python Windows, 0 echec/erreur/skip apres
correction ; Browser Bridge 45/45. Checkpoint `1467669` publie, CI observee
SUCCESS sur les deux OS :
[run 37942632322](https://github.com/hammouda93/jarvis-personal/actions/runs/37942632322).
Windows 874 passes ; Linux 869 passes + 5 exclusions Windows/DPAPI ;
45/45 replays Node sur chaque OS.
Connexions personnelles, expiration/revocation fournisseur, Linux SecretService
reel, missions MCP de bout en bout et sessions longues restent non valides.

### Ressources Fournisseurs Et Decouverte Windows

- Les 429 utilisent le header Retry-After (secondes ou HTTP-date), meme apres
  encapsulation d'une erreur SDK. Sinon cooldown de 60 secondes. Pas de sommeil
  bloquant ni requete repetee durant ce delai. Un statut HTTP structure prime
  sur une sous-chaine d'un body ; blocage 402 conserve separement par credential.
- Meme gate pour Groq autonome et le fallback Groq ; retries internes du SDK
  desactives pour que chaque tentative reserve soit observable.
- Compactage deterministe des observations de lecture strictement identiques,
  seulement dans la requete. Derniere copie, messages utilisateur/systeme,
  schemas, refs, resultats de mutation/echec et transcript original preserves.
  Aucun LLM de resume ni appel d'apprentissage. Ce n'est PAS encore un compactage
  semantique des longues histoires ; contexte unique trop gros reste refuse.
- Compteurs input/output retournes par Cerebras/Groq dans un scope de mission
  persistes par fournisseur et affiches dans la supervision. Reponses sans
  compteurs signalees, pas de tokens ni de facture inventes. Budget de requetes
  conserve, jamais remis a zero par une continuation. Panne d'ecriture : pause
  avant prochaine action/requete, sans refaire la requete recue.
- `list_applications` expose l'inventaire Windows existant en lecture seule
  (Start Apps / App Paths), sans lancer de programme. Refus d'une URL de protocole
  non HTTP guide vers cette capacite, sans transformer automatiquement une URL
  en autorisation de lancer une application. Aucune logique propre a VLC ajoutee.
- Controle **reel, lecture seule** sur cet ordinateur : recherche VLC a retourne
  un candidat `vlc` App Paths et les noms Start Apps associes, sans lancement.
  Ceci prouve cette decouverte locale seulement, PAS le choix du modele, lancement,
  fenetre UIA/CUA ou controle VLC. Ces recettes restent a faire ensemble.

Regression finale locale : 889 tests Python Windows passes, sans echec, erreur
ni exclusion ; Browser Bridge 45/45. Les tests du SDK sans retry et du dispatch
du compactage sont inclus. CI du nouveau checkpoint a controler apres publication.
Scheduler horaire/durable, autonomie naturelle complete,
V5/Ollama reel, cartes/lignes et preuves de resultats metier restent ouverts.
La demande precedente de cloture limitait alors la reprise au checkpoint courant.
Le brief V10 recu ensuite autorise l'audit et l'integration isolee ci-dessus.

### Reconciliation 9G Et Consentement Operationnel

Dernier brief utilisateur et commentaires des PR #16/#17 relus le 2026-10-09.
Les observations reelles signalees dans ces commentaires sont des donnees de
recette humaine, pas de nouvelles executions effectuees par Codex.
Les travaux Skills ont ete preserves dans `8b9864b`, puis la branche 9G
`b78266f` a ete reconciliee sans ecrasement dans `aa4740c` sur la seule branche
de developpement. La CI 9G a ete inspectee Windows/Linux SUCCESS :
[run 37928313324](https://github.com/hammouda93/jarvis-personal/actions/runs/37928313324).
La regression combinee avant changement de politique etait verte : 847 tests.

Travail ajoute apres reconciliation :

- Deux interrupteurs independants et persistants dans le centre de controle.
  Utiliser les Skills : OFF par defaut. Apprentissage operationnel : OFF par defaut.
- Le nouveau consentement operateur remplace le drapeau combine historique
  d'environnement; un fichier manquant/invalide laisse les deux modes OFF.
- ON/OFF reutilise les connaissances sans apprendre; OFF/ON peut apprendre
  mais n'injecte/reutilise aucune connaissance operationnelle. OFF/OFF n'emet
  aucun checkpoint LLM d'apprentissage ni ecriture automatique de profils/lecons.
- Filtrage des schemas Ollama/OpenAI/Cerebras, gardes de dispatch natives,
  controle des lectures/ecritures du backend kernel, application par le worker.
  Les connaissances existantes ne sont pas effacees. V5/missions/preuves restent
  independantes de cette politique.
- Les 402, clarifications versionnees, questions en entier, prevention 413 et
  garde de fausse affirmation d'envoi 9G sont conserves. Correction supplementaire
  du cooldown : un refus local sans requete ne prolonge plus le delai initial.

Verification locale actuelle : 856 tests Python Windows passes, 0 echec/erreur/skip;
45/45 replays Browser Bridge. Les deux interrupteurs, quatre modes et redemarrage
de configuration sont testes automatiquement. CI `2a47abe` observee SUCCESS :
[run 37939644901](https://github.com/hammouda93/jarvis-personal/actions/runs/37939644901),
Windows 856 passes, Linux 851 passes + 5 exclusions Windows/DPAPI, Node 45/45
sur chaque OS. La CI du centre MCP est egalement verte, voir ci-dessus.
L'acceptation MCP complete, l'autonomie naturelle bout-en-bout, le compactage de
contexte, la lecture fiable de lignes/cartes, V5/Ollama reel et le scheduler
durable restent ouverts. Aucun agent de production ni compte personnel utilise.
Les erreurs de nombres MS Football et refs Instagram restent a traiter.
Le routage/lancement VLC par le modele n'est pas declare valide par la seule
decouverte Windows ajoutee.

Etat de depart inspecte : `210e9d38653de23b44106eadb6ff31795efd8e54`, synchronise
avec la branche distante. Ses deux jobs Windows/Linux sont SUCCESS :
[CI 210e9d3](https://github.com/hammouda93/jarvis-personal/actions/runs/37858807575).
Les commits `420179b` (MCP) et `968cc4a`/`210e9d3` (contrat begin_turn V5)
sont conserves, sans retour arriere.

Travail 9F realise :

- Contrat `mission_checkpoint` dans une execution supervisee : continuer,
  attendre la verification, ou signaler un obstacle. Aucune declaration de
  completion ni autorisation ne peut etre fournie par cet outil.
- Une etape longue peut poursuivre plusieurs tours dans le meme runtime,
  sous les budgets cumulatifs existants. Une limite de tour n'est plus une
  preuve d'execution definitive de toute l'etape. Pas d'augmentation des 8 tours.
- La conversation d'une mission au plan approuve coordonne les etapes jusqu'a
  une pause justifiee. L'objectif original accompagne chaque delegation.
- Les mutations identiques deja reussies ne sont pas redispatchees lors d'une
  continuation. Les effets inconnus restent bloques avant une nouvelle action.
- Les rapports intermediaires passent par des signaux Qt, sans TTS ni attente
  d'un nouveau message. L'arret hors bande reste disponible pendant la mission.
- Les annonces finales supervisees proviennent de l'etat des preuves, pas
  d'une phrase du modele. Un retour de reponse ne remet plus une mission
  non verifiee dans l'etat visuel TERMINE.
- V5 implemente explicitement `run_with_context` : les ecritures/rappels dans
  une mission ne contournent plus la memoire. Les continuations operationnelles
  supervisees ne sont pas reclassifiees a chaque tour par Ollama.
- Les sorties Ollama vides/tronquees sont refusees, le thinking inutile du
  classificateur est desactive. Le timeout et l'opt-in cloud ne sont pas changes.
- Interface Qt : tous les controles dans le defilement, panneaux horizontaux
  et conversation redimensionnables, poignee de fenetre, dimensions initiales
  bornees a l'ecran logique, controles compacts et contrastes lisibles.

Verification locale : 815 tests Python Windows passes, 0 erreur/echec/skip;
45 replays Browser Bridge passes. Captures Qt offscreen inspectees en
660x500, 1024x640 et 1500x900; aucun debordement horizontal de la console avec
Segoe UI. Tests de geometrie egalement passes avec la police de secours.
Ce sont des tests automatiques, pas une nouvelle installation Windows reelle.
CI de `38aa8dd` verifiee SUCCESS sur Windows/Linux :
[run 37903921448](https://github.com/hammouda93/jarvis-personal/actions/runs/37903921448).
Windows : 815 passes; Linux : 810 passes et 5 skips Windows/DPAPI explicites.
45 replays Browser Bridge et contrat du SDK MCP officiel passes sur chaque OS.

Travaux Skills preserves avant reconciliation 9G : snapshots transactionnels,
edition/restauration operateur, desactivation non revoquee par l'apprentissage,
historique legacy sans versions inventees. 15 nouveaux tests, regression Windows
locale 830 verte. L'utilisation et l'apprentissage doivent devenir OFF par defaut
avec deux reglages persistants conformement au nouveau brief du 2026-10-09;
ce changement de politique n'est pas encore integre dans ce checkpoint intermediaire.

Limites importantes : la continuation automatique 9F exige un plan supervise
approuve. Le parcours conversationnel sans plan conserve son fonctionnement
historique, mais l'installation complete en langage naturel n'est PAS encore
demontree. Les confirmations de mutations du superviseur restent conservatrices;
une permission precise pour des transitions UI ordinaires reste a concevoir.
Les predicates finaux d'installation/raccourci doivent etre configurables et
valides en conditions reelles. Aucune boucle ne transforme un clic en preuve
de l'objectif complet.

## Tests Windows signales par l'utilisateur

Source : brief de continuation fourni le 2026-10-09. Ce sont des observations
humaines rapportees, pas des executions realisees par Codex pendant cette reprise.

| Parcours | Observation humaine | Limite restant ouverte |
| --- | --- | --- |
| Conversation francaise texte/voix | Conversation, changement de mode, micro coupe, fallback TTS Windows | Refaire avec 9F |
| Chrome habituel / YouTube | Recherche complete, premier resultat, retour, fermeture ciblee | Refaire avec 9F et onglets temoins |
| WhatsApp Web generique | Recherche/contact, saisie Unicode, confirmation et soumission | Livraison au destinataire non prouvee; lecture variee a tester |
| MS Football | Consultation/comptage, preview et approbation de mutation | Mutation definitive non validee |
| Installateur Windows | Fichier ouvert, licence/Next/options observes, dialogue de fermeture traite | Installation achevee, raccourci et lancement non verifies |
| Memoire V5 | Rappel en session rapporte; timeout local et rappel apres redemarrage en echec | Validation Ollama et stockage persistant reel requise |

Lecture locale sans inference : Ollama `127.0.0.1:11434/api/tags` repond et
liste `qwen3:4b-instruct`, `qwen3:4b` et `gemma3:latest`. Cela prouve la
disponibilite du serveur et la presence des modeles, pas la latence de V5,
le chargement effectif du modele ni la fiabilite du rappel apres redemarrage.

Procedure : `docs/FINAL_WINDOWS_ACCEPTANCE.md`. Details du contrat :
`docs/MISSION_CONTINUATION_V9F.md`. MCP : `docs/MCP_SECURITY_AND_OAUTH.md`.

## Base et invariants

- Branche de travail : `feature/codex-jarvis-final-development`.
- Base verifiee : `5bdd542d3cab8b7d52ab7db9bc20167ca911e050` (9C).
- Reference preservee : `feature/semantic-planning-authoring-v9c`.
- Aucun reset, remplacement du runtime, merge stable ou compte personnel connecte.
- Les journaux et diagnostics locaux non suivis preexistants sont preserves.
- Cerebras/Groq, Chrome Bridge, UIA/CUA, memoire V5 et texte/voix restent les voies d'execution existantes.

## Audit initial (historique 9C)

`build_agent_runtime` assemble un seul registre d'outils et le fournisseur choisi,
puis des wrappers opt-in : foundations, memoire, traces, continuite de missions,
fiabilite Hermes. `LiveMissionContinuityRuntime` conserve les tours, le graphe,
les preuves et la reprise avec controle de proprietaire. Il bloque une action
interrompue et ne deduit pas le succes du but d'un retour d'outil.

`semantic_goal_supervisor` est actuellement une revue deterministe sans dispatch.
`CapabilityAgentRouter` et `capability_planner` proposent des responsabilites ;
ils ne deleguent pas encore les etapes. `llm_mission_planner` produit un plan
borne via le fournisseur existant, avec une requete explicite et sans outils.

Le MCP Hub possede deja un registre, une permission par outil et une empreinte
de schema qui revoque une autorisation modifiee. Le transport ouvre une session
temporaire et ne retente pas un effet incertain. Il ne supporte actuellement
que le profil stdio `hermes mcp serve` et HTTP avec bearer d'environnement.
OAuth, stockage securise, stdio generique et suivi d'utilisation restent ouverts.

L'apprentissage operationnel et les Skills existent dans `AgentKnowledgeStore` ;
il faut etendre leur validation, versionnement, desactivation et retour arriere.
`MissionScheduler` est une file kernel en memoire, pas un scheduler personnel
durable. L'interface PySide6 possede deja conversation, visualisation, supervision,
MCP et roadmap. Ces composants seront etendus.

## Etat des jalons

| Jalon | Code integre | Validation automatique nouvelle branche | Test reel |
| --- | --- | --- | --- |
| 1-8 et 9A-9C | Base historique preservee | Regression cumulative locale et CI 9D verte | EN ATTENTE DE TEST REEL |
| 9D superviseur actif | Integre, opt-in ; limites documentees | 20 tests dedies locaux ; CI Windows/Linux verte | EN ATTENTE DE TEST REEL |
| 9E delegation controlee | Integree dans le runtime existant ; roles sans outils bloques | 38 tests dedies ; CI Windows/Linux verte | EN ATTENTE DE TEST REEL COMPLET |
| 9F continuite / V5 / Qt | Integre pour les plans supervises ; autonomie conversationnelle complete encore ouverte | 29 nouveaux tests; CI 38aa8dd Windows/Linux verte | EN ATTENTE DE TEST REEL |
| Centre MCP complet | HTTP/stdio, OAuth SDK, coffre OS, quotas/journal integres dans 420179b | CI 210e9d3 Windows/Linux verte | Aucun compte personnel connecte |
| 10 apprentissage et Skills | Historique, edition, restauration, desactivation et deux opt-in independants | 24 nouveaux tests locaux; regression 856 verte; CI combinee a observer | EN ATTENTE DE TEST REEL |
| 11 automatisations durables | A developper | A developper | EN ATTENTE DE TEST REEL |
| Interface finale et acceptation | Partiel | A etendre | EN ATTENTE DE TEST REEL |

## Tests et problemes

Les chiffres historiques fournis (480 Windows, 661 Linux, 45 Browser Bridge)
concernent la CI 9C et ne prouvent pas les futurs commits.

Le `.venv` local reference un Python 3.9 absent. Un environnement separe
`.cache/t` est cree avec Python 3.12.14 pour les tests ;
le `.venv` et les donnees de l'utilisateur ne sont pas modifies.

Le premier environnement de test long a echoue a installer ElevenLabs (limite
de chemins Windows) ; le chemin raccourci corrige ce probleme sans changer
les dependances ni la configuration Windows.

La suite complete a revele des connexions SQLite conservees apres les blocs
`with`, ce qui empêche le nettoyage des fichiers sur Windows. Les stores
utilisent maintenant des connexions qui conservent commit/rollback puis ferment
le handle. `JARVIS_DATA_DIR` permet d'isoler les stores initialises a l'import.
La lecture de telemetrie inaccessible est traitee comme indisponible.

Resultats locaux du checkpoint 9D : 685 tests Python Windows passes, dont 20
tests dedies de supervision/Qt ; 45 replays Browser Bridge passes ; contrat
du SDK officiel MCP 2.3.0 verifie. CI du commit `539e1fdc23efd08f87fe2a96117cb4ca08d62f72` :
[run 37837187767](https://github.com/hammouda93/jarvis-personal/actions/runs/37837187767),
Windows et Linux SUCCESS. Windows : 685 tests passes ; Linux : 681 passes et
4 skips Windows explicites ; 45 replays Browser Bridge passes sur chaque OS.
Aucun de ces tests ne certifie une action Windows reelle.
Au moment de ce checkpoint 9D, aucun essai reel effectue par Codex.
Les observations humaines ulterieures sont listees en tete de ce rapport.

Checkpoint 9E en preparation : 723 tests Python Windows passes, dont 38 dedies
a la delegation ; 45 replays Browser Bridge passes. Les tests incluent l'assemblage
reel des wrappers avec fournisseur simule, permissions exactes, reprise sans
rejeu, arrets et budgets de memoire V5. Deux echecs de fixtures (plan immuable,
projection incompletement attendue) ont ete analyses/corriges avant cette passe.
La CI 9E a ensuite ete verifiee SUCCESS sur Windows/Linux :
[run 37854436125](https://github.com/hammouda93/jarvis-personal/actions/runs/37854436125).

PR brouillon ouverte et rattachee :
[PR 16](https://github.com/hammouda93/jarvis-personal/pull/16), base reference 9C,
head branche de developpement. Aucune fusion effectuee.

## Hermes

Reference initiale inspectee : `d94b70f675205c2c046138997819428772cd2678`.
Main revalide et sources delegation relues : `9d05e7ff92d3edd9abdf20fe3d04551905cb995e`.
Comparaison, incompatibilites et licence : `docs/HERMES_ADAPTATION.md`.
Sources : `tools/mcp_tool_discovery.py`, `tools/mcp_tool_transport.py`,
`hermes_cli/mcp_security.py`, `hermes_cli/subcommands/mcp.py`, `mcp_serve.py`,
`agent/transports/hermes_tools_mcp_server.py`.

La decouverte actuelle distingue configuration, session active, lazy et erreur,
borne la concurrence et applique un cooldown aux connexions echouees.
Le transport applique des controles d'environnement, de redirection et de
negociation. Les mecanismes utiles seront adaptes sans installer Hermes.
`hermes mcp serve` expose les conversations/canaux ; le serveur d'outils publie
une selection et depend de son runtime. Aucun des deux ne prouve qu'un service
personnel est connecte a Jarvis. Aucun code Hermes copie a ce stade.

## Prochain travail

1. Publier 9F, observer sa CI Windows/Linux et corriger toute regression.
2. Etendre l'autonomie depuis les demandes naturelles avec permissions de
   transition UI precises et predicates finaux generiques, sans scripts par app.
3. Tester V5/Ollama et missions longues en conditions reelles controlees.
4. Completer le catalogue MCP officiel et les connecteurs personnels, puis
   Skills versionnes/desactivables et scheduler durable avec tests associes.
5. Publier les commits autorises, observer la CI et maintenir la PR brouillon.
6. Finaliser l'architecture, la comparaison Hermes, les couts, la securite,
   l'installation/rollback et la procedure d'acceptation Windows.
