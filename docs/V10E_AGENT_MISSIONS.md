# V10E : cerveau et missions generales

Date : 2026-10-11. Branche isolee `codex/agent-missions-v10e`, base
`56a8cfc0c5649e3ae9bca3f0f573c89dc31ffe39`. Aucun merge stable.
Recette : [V10E_WINDOWS_ACCEPTANCE.md](V10E_WINDOWS_ACCEPTANCE.md).

## Audit et corrections

Le runtime Cerebras/Groq et les adaptateurs Foundation/MCP/Hermes/Supervisor
existants sont conserves. Chrome Bridge, UIA/CUA, SQLite V5 et texte/voix ne
sont pas remplaces. Skills/Learning restent independants et OFF par defaut.

- Routage : les objectifs composes/contraints reviennent au cerveau. Les
  interdictions et conditions ne deviennent plus des actions positives dans
  les reparations lexicales. `texte affiche` n'ouvre pas une application.
- Domaines : une observation navigateur n'est plus une politique supprimant
  les outils Windows. Un objectif fichier explicite peut ouvrir le fichier
  puis quitter le mode navigateur, sans lever le garde de saisie OS.
- Fournisseurs : reponse tronquee refusee avant dispatch du batch. Une voie
  Cerebras incomplete peut passer a Secondary puis Groq, dans l'ordre initial.
  402/429/cooldown/Retry-After et limite Groq restent controles. Aucun replay
  automatique d'une mutation ni retry HTTP implicite ajoute.
- Sortie : Cerebras reserve 1024 tokens, Groq 512 par defaut, configurables
  avec bornes. La preflight Groq utilise la meme reserve de sortie que l'appel;
  sa limite d'entree n'est pas augmentee pour faire passer les tests.
- Memoire locale : budget Ollama froid controle de 45 s, chaud de 8 s,
  `keep_alive=5m`. Une limite constructeur explicite reste prioritaire. Un
  timeout ne relance pas le job et n'autorise pas un fournisseur cloud.
- Sauvegarde : outil read-only `verify_file_artifact`, chemin absolu local,
  nom exact et contenu UTF-8 ou SHA256, lecture bornee 4 Mio, fichier stable
  pendant lecture. Existence seule/Ctrl+S ne prouvent pas une sauvegarde.
- Document vide : contrainte explicite injectee avant premiere ecriture UIA
  ou Computer Core; valeur fraiche vide obligatoire. Valeur inconnue/non vide
  bloque avant dispatch. Saisie aveugle/visuelle refusee dans ce cas.

## Contexte efficient : opt-in

`JARVIS_EFFICIENT_CONTEXT_ENABLED=1` ou `-EfficientContext` dans le lanceur.
Le flag n'est pas inclus implicitement dans `-All`.

Politique systeme compacte et schemas selectionnes depuis le registre reel,
apres les filtres de scope. `request_tool_capabilities` expose les schemas
originaux des outils actuellement accessibles, sans permission ni action.
En delegation, le catalogue est construit apres filtrage du role; il ne permet
pas de sortir de ce role. Les lectures de contexte consomment le budget local,
sans observation Windows/reseau supplementaire ni preuve de fin de mission.

Seules les anciennes lectures reussies allowlistees sont archivees dans une
vue de requete. Transcript integral inchange, tool_call_id conserves, deux
lectures les plus recentes par outil intactes. Recu SHA256 et relecture locale
bornee par `read_observation_evidence`. Mutations, erreurs, approbations et
effets inconnus ne sont jamais archives. Sous pression Groq, une lecture recente
et un pack minimal recuperable sont conserves; la preflight peut encore refuser.

Mesure reproductible, sans API, sur observations synthetiques et schemas reels :

| Mesure | Avant | Apres |
| --- | ---: | ---: |
| Messages serialises, caracteres | 54 488 | 22 706 |
| Schemas d'outils, caracteres | 16 771 | 13 788 |
| Outils exposes | 33 | 25 |
| Politique de base, caracteres | 14 775 | 5 728 |

Commande : `python scripts/benchmark_agent_context.py`.
Six lectures integrales recuperables; transcript identique. Ce ne sont ni des
tokens factures ni une mesure de cout/latence/calls sur mission live.

## Verification et provenance

Baseline V10D : CI run 38044416414, Windows 970 passes; Linux 965 passes et
5 exclusions explicites (970 executes). Node 45 passes sur chaque OS.
V10E local initial : 1005 tests passes, puis ajout de 3 tests d'integration
contexte/factory/superviseur. Final local : 1008 tests passes en 41,755 s,
0 echec/erreur/exclusion; Node 45/45. CI du checkpoint suivie dans
[FINAL_DEVELOPMENT_PROGRESS.md](FINAL_DEVELOPMENT_PROGRESS.md).

38 nouveaux tests V10E : missions 11, contexte 12, fournisseurs/memoire 4,
preuve fichier 5, preconditions 6. Regressions historiques conservees; fixtures
fournisseurs mises a jour pour representer une reponse SDK complete, sans
desactiver les assertions de cooldown/ordre/nombre d'appels.

Un lancement direct de unittest sans runner isole a echoue a l'import SQLite
dans le sandbox. Utiliser `scripts/run_final_regression.py --pattern ...` : le
runner fixe un repertoire jetable avant tout import. Aucun acces au contenu de
la SQLite personnelle, compte API reel ou action desktop realise pour ces tests.

Sources primaires consultees et adaptation compatible, sans remplacer Jarvis :

- [Hermes context_compressor.py, c573316](https://github.com/NousResearch/hermes-agent/blob/c57331677d7fc46298a7159cf3a650853f0966c5/agent/context_compressor.py) : preservation du protocole et gestion du contexte; pas d'import de son moteur/summarizer.
- [Cerebras Chat Completions](https://inference-docs.cerebras.ai/api-reference/chat-completions) : budget de completion incluant le raisonnement et motifs de fin.
- [Ollama Chat](https://docs.ollama.com/api/chat) : maintien local du modele charge.

## Limites et suite A-I

Les heuristiques de clauses ne remplacent pas un contrat semantique complet.
Le retour Windows via ouverture de fichier est couvert automatiquement; tous
les parcours composes et changements de fenetre restent a tester en direct.
La preuve fichier etablit la correspondance des arguments verifies, pas la
creation recente du fichier ni a elle seule toute la mission utilisateur. Le
garde de reponse est lexical; plusieurs artefacts/format Word demandent un
contrat supervise explicite et une verification adaptee.

Archive de contexte en RAM seulement; aucune reprise durable automatique de
cette archive. Precondition vide appliquee a la premiere ecriture du tour, pas
encore un invariant semantique durable a travers restart/confirmation. CUA sans
preuve fraiche reste bloque. UIA n'offre pas de compare-and-set atomique.
Un historique massif de mutations/unknown ou une observation recente trop
grande peut encore depasser Groq; aucune disparition silencieuse de ces faits.

- A, cerveau : lot prioritaire implemente, couts et fournisseurs live a mesurer.
- B, missions semantiques : contrat existant preserve; contraintes/artefacts
  multiples et reprise longue a renforcer apres recette des nouveaux gardes.
- C, computer use : primitives existantes et preconditions renforcees; couverture
  reelle multi-applications/vision toujours partielle.
- D, Supervisor : budgets/scopes/proofs existants preserves; contexte local ne
  termine aucune mission. Recette en premiere passe Supervisor OFF, puis ON.
- E, memoire : raw/projection durable V10D conserves; froid/chaud V10E a chronometrer.
- F, Skills/Learning : revision/opt-ins existants, tests avances et deduplication
  longue a poursuivre; aucun apprentissage requis pour les missions generales.
- G, MCP : centre d'acces, SDK et issues inconnues testes; comptes personnels
  non connectes par ce travail, mutations reelles non validees.
- H, runtime durable : scheduler local et backup preserves, non integres ni
  actives ici; reprise/suspend/resume a poursuivre sans doubler les effets.
- I, interface : historique conserve, statut de recette honnete actualise;
  campagne UI Windows/voix et vues longues encore a effectuer.

Priorite suivante : recette composee et budgets reels, puis invariants durables
de mission et integration scheduler revue. Jarvis n'est pas declare final ni
autonome sur la base de tests simules.
