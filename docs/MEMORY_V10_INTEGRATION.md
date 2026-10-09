# Memory V10 : Audit Et Integration Isolee

Audit du 2026-10-09, timezone utilisateur Africa/Lagos.

## Etat Retrouve

- Branche initiale : `feature/codex-jarvis-final-development`, HEAD et origin
  `699b7eb58f6d98fc10305e93987fd216c41e7edb`.
- Fichiers suivis propres avant reprise ; journaux et diagnostics utilisateur
  non suivis conserves, jamais stages ni supprimes.
- Reference 9C locale/distante : `5bdd542d3cab8b7d52ab7db9bc20167ca911e050`.
  `main` non modifiee. Aucun historique reecrit ni branche supprimee.
- Worktrees inspectes : checkout courant, `jarvis-9f-validation` (38aa8dd),
  `jarvis-v10a-validation` (4e5e4ad), `jarvis-v10b-validation` (330743d),
  `jarvis-v9g-validation` (fdf8429). Aucun deplacement/suppression.
- Integration creee sur `codex/integrate-memory-v10c` depuis 699b7eb ;
  merge local de 5d4e04f en 87f8876, sans conflit. Les trois PR restent ouvertes
  et non fusionnees. Ce checkout d'integration est candidat a la recette,
  pas une nouvelle version stable.

## PR Et Compatibilite

| Source inspectee | Changements conserves | Validation automatisee observee |
| --- | --- | --- |
| [#18](https://github.com/hammouda93/jarvis-personal/pull/18), 4e5e4ad | Admission dictee `retient que`, recherche ciblee sans fuite d'inventaire recent, termes de recherche nettoyes | [37956109849](https://github.com/hammouda93/jarvis-personal/actions/runs/37956109849) : Windows 893 passes ; Ubuntu 888 passes + 5 exclusions ; Node 45/45 |
| [#19](https://github.com/hammouda93/jarvis-personal/pull/19), 330743d | V5 sous les outils du cerveau, pas de classification Ollama obligatoire ; inventaire/date/range/metadonnees, source brute de l'utilisateur, validation des conflits de dates, garde de citation navigateur mieux scopee | [37966428976](https://github.com/hammouda93/jarvis-personal/actions/runs/37966428976) : Windows 903 passes ; Ubuntu 898 passes + 5 exclusions ; Node 45/45 |
| [#20](https://github.com/hammouda93/jarvis-personal/pull/20), 5d4e04f | Restriction optionnelle des tours explicitement memoire, blocage au dispatch des outils hors sujet, alerte d'annee non justifiee, fallback lecture seule borne | [37970227783](https://github.com/hammouda93/jarvis-personal/actions/runs/37970227783) : Windows 910 passes ; Ubuntu 905 passes + 5 exclusions ; Node 45/45 |

L'ascendance reelle est 699b7eb -> V10A -> V10B -> V10C. Pas de remplacement
des modules MCP, Skills/learning, provider gate ou supervision du checkpoint.
Les succes CI sont des contrats automatises ; ce ne sont pas des preuves de
conversation vocale, UIA, livraison WhatsApp ou connexion OAuth personnelle.

## Corrections Necessaires A L'Integration

1. Hermes/Supervisor ne connaissait pas les nouveaux noms de lecture V5.
   Ils auraient demande des confirmations de mutation et interdit des relances
   de lecture en continuation. Ajouter uniquement les trois primitives locales
   de lecture a la liste connue ; les nouveaux noms inconnus restent mutateurs.
2. Le compactage V10C remplacait le systeme complet et supprimait l'historique
   des outils et des consignes utilisateur au-dela de quatre messages. Il aurait
   perdu le but/les preuves injectes par `run_with_context`.
   Desactiver ce compactage pour contexte injecte, supervision ou ancrage de
   session. Pour la lecture memoire autonome, conserver les consignes utilisateur,
   systemes additionnels et toutes les enveloppes d'appels/resultats historiques.
   Les longs contenus uniques peuvent encore echouer avant envoi : aucune
   augmentation du budget, aucun effacement de preuve pour masquer cet echec.
3. Une mission conserve son scope de responsabilite general au lieu d'etre
   restreinte par une mention de memoire dans son objectif. Le routage V10C reste
   un garde conservateur opt-in, pas un nouveau classificateur ou cerveau.
4. Les trois lectures SQLite connues peuvent verifier un critere local approuve
   par sa valeur concrete. `semantic_memory_search` legacy est exclu de ce chemin
   car il peut encore faire de l'inference/indexation ; MCP, ecriture et champs
   success/verified ne deviennent jamais des preuves de completion.

Les tests nouveaux reproduisent les incompatibilites avant correction et
exercent ensuite le vrai dispatch/fallback avec fournisseur simule, ainsi que
le vrai SQLite dans un repertoire temporaire. Aucun compte ou bureau manipule.

## Architecture Et Activation

Un runtime, un cerveau conversationnel, un registre de capacites, V5 SQLite,
un contexte de mission et des preuves independantes. Priorite Cerebras primary
-> secondary -> Groq inchangee. Aucune nouvelle cascade Ollama de conversation
n'est ajoutee ni annoncee comme disponible lorsque tous les clouds echouent.

Opt-ins conserves, aucun changement silencieux de leurs valeurs par defaut :

- `JARVIS_MEMORY_CORE_ENABLED=1`
- `JARVIS_SEMANTIC_MEMORY_V5_ENABLED=1`
- `JARVIS_MEMORY_AGENT_TOOLS_ENABLED=1`
- `JARVIS_MEMORY_SCOPE_GUARD_ENABLED=1` pour tester les gardes V10C.

Les deux premiers sans le troisieme conservent le routeur V5 historique.
Skills et apprentissage operationnel restent deux OFF persistants independants.
MCP demande toujours configuration/connexion/consentement explicites.
Le choix du cerveau conversationnel cloud ne constitue pas une permission
d'indexer les notes avec un nouveau fournisseur cloud : les reglages de
confidentialite/extraction existants ne sont pas modifies.

## Dependances Du Prochain Travail

| Ordre | Travail | Condition / limite actuelle |
| --- | --- | --- |
| 1 | Recette de cette integration, tours memoire et mixtes, voix/texte, redemarrage | CI et fixtures ne prouvent pas le choix reel d'outils de Cerebras ni le desktop |
| 2 | Correction durable/versionnee des souvenirs | Identifier la source et la version, preview et consentement, CAS transactionnel, historique, verification apres ecriture ; aucun outil d'update n'est encore livre |
| 3 | Projection plus rapide/reprisee | Note brute deja durable, sidecar encore synchrone ; queue durable et reprise sans inference cloud implicite avant de differer l'indexation |
| 4 | Temps et couverture semantique | Fuseau/local-day, dates relatives/recurrences, limites des annees et champs de dates contradictoires, etats d'index incomplet ; pas de faux negatif "agenda vide" |
| 5 | Fallback pour missions generales | Compactage qui conserve but/permissions/preuves, schemas pertinents et budgets ; le nouveau compactage reste strictement memoire autonome en lecture |
| 6 | Sources MCP/API autorisees et missions mixtes | V5 et Calendar/Gmail/Drive sources distinctes, OAuth/permissions reels, provenance et deduplication ; aucun compte active ici |
| 7 | Autonomie, computer use et scheduler | Erreurs MS Football/refs et preuves de fin d'installation ouvertes ; scheduler horaire/durable et execution isolee non livres |

## Hermes Actuel

Main inspecte : `b624a38f21f2f674d5a8ccf387687c1b481e23f6`.
[memory_tool.py](https://github.com/NousResearch/hermes-agent/blob/b624a38f21f2f674d5a8ccf387687c1b481e23f6/tools/memory_tool.py)
epingle l'entree exacte pour une correction approuvee et distingue l'etat
prepare de l'ecriture effective. Ces principes servent a la prochaine etape,
sans reprendre ses fichiers MEMORY.md, son snapshot systeme ni ses gates
fail-open. Aucun code amont copie ; SQLite V5 et les gardes Jarvis restent la base.

## Validation Courante

La regression de la chaine integree avant correction : 910/910 locale.
Tests cibles corriges : 116 runtime et 9 integration ; Node 45/45.
Regression cumulative finale : 920 tests Windows locaux passes, sans erreur,
echec ni exclusion. Aucun modele
reel ni compte personnel appele pour ces tests. Les vrais tests Windows sont prepares dans
[FINAL_WINDOWS_ACCEPTANCE.md](FINAL_WINDOWS_ACCEPTANCE.md), non executes ici.

Premiere CI de l'integration 51e6a0f : Ubuntu a trouve un KeyError dans la
nouvelle assertion du test de scope, car le builtin Groq browser_search n'a pas
de champ function. Assertion corrigee et fixture forcee avec ce builtin actif
sur tous les OS ; runtime et capacites de production inchanges. Les nouvelles
regressions ont passe, puis la CI du checkpoint corrige `25d3e12` a ete
inspectee dans les logs : [37990022359](https://github.com/hammouda93/jarvis-personal/actions/runs/37990022359),
Windows 920 passes, sans exclusion ; Ubuntu 915 passes + 5 exclusions
Windows/DPAPI ; Node 45/45 sur chaque OS ; imports du SDK MCP officiel passes.
Ces resultats valident les contrats automatises, pas le choix reel d'outils
du modele sur le bureau. [PR #21](https://github.com/hammouda93/jarvis-personal/pull/21)
reste brouillon, non fusionnee, a destination de la branche de developpement.
