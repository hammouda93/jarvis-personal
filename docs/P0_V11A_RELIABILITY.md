# P0 — V11A Reliability & Autopilot Recovery

Date d'audit : 13 octobre 2026. Les validations automatiques décrites ici ne sont pas une validation de Jarvis sur le bureau Windows réel de l'utilisateur.

## 1. Version et état de départ

- Branche corrective : `codex/v11a-p0-reliability`.
- Base distante vérifiée par `git ls-remote`, puis récupérée : `feature/unified-mission-autopilot-v11a`, commit `e5947c4536589548757f13a0e111d3e577e86891`.
- PR de base : [#30, Unified Mission Autopilot V11A](https://github.com/hammouda93/jarvis-personal/pull/30), draft, sur `fix/conversation-progress-v10k`. La chaîne de PR ouvertes a été inspectée avant modification.
- CI de base : [run 38336417711](https://github.com/hammouda93/jarvis-personal/actions/runs/38336417711), régression Windows et Ubuntu réussie au HEAD V11A.
- Worktree isolé : `C:\Users\salah\.codex\worktrees\jarvis-v11a-p0-reliability\jarvis-main`.
- Le checkout initial `D:\Django_Projects\jarvis-main\jarvis-main`, ses modifications locales, `.env`, journaux et bases personnelles ont été conservés. Aucune modification de `main`, de branche stable ou de l'historique partagé.

La PR corrective est empilée sur la branche V11A. Son URL et son SHA final sont donnés dans le rapport de livraison ; ce document fait partie des commits qu'elle contient.

## 2. Ce qui fonctionne et ce qui reste à prouver

| État | Résultat P0 |
|---|---|
| Implémenté | Reçu mémoire JSON commun et versionné ; relecture SQLite indépendante ; preuves navigateur cumulées dans le graphe durable ; reprise liée à l'objectif original ; continuation bornée ; réponse et état de mission liés au certificat ; affichage distinct des résultats non certifiés. |
| Validé automatiquement | 24 nouvelles régressions P0, suite Python complète, replay Browser Bridge, imports SDK MCP, dépendances du lanceur Windows. Les fournisseurs et le navigateur sont simulés dans les tests de mission. Une relecture mémoire utilise une vraie base SQLite temporaire et un nouveau processus Python. |
| Windows en conditions réelles | Les anomalies de départ et le rappel après redémarrage ont été rapportés par l'utilisateur. Aucun parcours réel avec ces nouveaux correctifs n'a été exécuté par Codex. |
| Non testé | Chrome normal connecté avec la nouvelle extension, navigation réelle MS Football, appels Cerebras réels, arrêt et relance de l'interface avec la mémoire personnelle, résultat vocal et ergonomie à l'échelle Windows. |
| Limité | Certification de navigation pour un profil explicite URL initiale → lien nommé → URL finale, éventuellement avec ouverture de menu. Ce profil ne certifie pas une mission mixte Windows/Web/recherche/MCP, ni une exigence supplémentaire de lecture ou de comparaison. |

La compatibilité avec les anciens checkpoints est conservée pour le contexte. Les checkpoints sans reçus structurés complets ne sont pas promus rétroactivement en preuves certifiantes. Les effets incertains exigent une vérification ; une reprise ne les rejoue pas automatiquement.

### Audit de la vision cible au HEAD V11A

| Domaine | Déjà présent | Partiel ou manquant |
|---|---|---|
| Mission OS | Runtime existant, journal, graphe, checkpoint, contrats sémantiques, supervision et contrôle opérateur. | Contrats et preuves composés pour des missions nouvelles impliquant plusieurs capacités ; récupération générique bornée et certification de tous les sous-objectifs. |
| Windows et navigateur | UIA/OCR/CUA, Browser Core et extension Chrome normal, références DOM liées à l'observation et à l'onglet. | Validation générale sur interfaces inconnues ; certaines mutations ou interfaces opaques restent non vérifiables. P0 renforce les preuves sans ajouter de scénario par site. |
| Mémoire | SQLite brute, recherche sémantique, projection et garde des outils. | Correction/versionnage, contrats composés, provenance et suivi d'une projection différée à approfondir. P0 traite l'écriture explicite et sa preuve brute. |
| Cerveaux et budgets | Cascade Cerebras primaire/secondaire/Groq, gestion de quota et cooldown, configuration de cerveaux supplémentaires, limite de tours. | Routage et budget global par mission, comptabilité réelle des coûts et optimisation générale inter-capacités. |
| Recherche | Outils de recherche et décomposition de questions. | Lecture indépendante des documents/pages, comparaison structurée des sources et provenance complète de chaque conclusion. |
| MCP et services | Hub, registre de capacités, transports MCP et contrôles d'accès. | Comptes personnels, OAuth et résultat final vérifié pour chaque intégration métier. Un SDK importable ne prouve pas une connexion réelle. |
| Planification temporelle | Fondations de scheduler et contrôle de missions. | Scheduler de production durable : récurrences, redémarrage, idempotence, notifications et journal d'exécution. |
| Interface | Console opérateur, workbench, télémétrie, préférences voix/texte. | Control Center complet et campagne visuelle Windows. P0 corrige les badges et la portée du certificat. |
| Délégation, Skills et apprentissage | Fondations et protections existantes, options indépendantes OFF par défaut. | Délégation multi-agent utile validée, apprentissage contrôlé et promotion de compétences. Aucune activation nouvelle en P0. |
| Cloud et release | CI multiplateforme et bases de tests. | Campagne réelle des missions mixtes, isolation cloud et critères de release finaux. |

La priorité P0 est ajoutée au snapshot de roadmap sans transformer les anciens jalons en affirmations de fiabilité LIVE.

## 3. Modifications et choix architecturaux

### Mémoire : un seul contrat de reçu

`memory_goal_proof.py` définit `MemoryWriteReceipt` et son parseur partagé. Le reçu contient une version, un identifiant entier positif, l'empreinte SHA-256 du texte brut, la date SQLite, la source, le dispatch et la vérification. Les consommateurs ne recherchent plus `memory_id=...` dans un reçu JSON moderne.

`foundation_tools.py` réserve l'écriture avant le dispatch, puis relit le contenu exact. Un commit suivi d'une exception produit un résultat incertain ; aucun deuxième enregistrement n'est tenté. Un reçu invalide ou une relecture impossible ne produit pas d'accusé de réussite. La réponse, le vérificateur et la trace utilisent le même contrat. `semantic_memory_runtime.py` emploie le même parseur ; son support strict du texte ancien sert uniquement à la projection historique.

### Navigateur : conserver les preuves et relire l'état courant

`runtime_convergence.py` conserve les reçus ordonnés dans les résultats du graphe SQLite existant : version, séquence, date, résultat et champs de preuve bornés. Les arguments bruts et les valeurs saisies ne sont pas recopiés. Les reçus mal formés, incomplets ou trop nombreux ne peuvent pas devenir un certificat. Aucune migration de schéma ni deuxième exécuteur n'est ajouté.

`browser_goal_proof.py` combine les tours de la même mission. Le goal original est transmis dans un contexte de confiance en mémoire du processus, sans donner d'autorité au texte du modèle ou au résumé de checkpoint. Après reprise, il faut une lecture actuelle : les seuls anciens reçus ne suffisent pas. Les lectures automatiques réelles de grounding sont conservées et réutilisées.

Les générations DOM et documents sont comparées aux références du clic. L'onglet et la fenêtre connus restent contraints. L'ouverture du menu doit exposer effectivement le lien demandé dans une observation fraîche ; un dispatch ou un changement générique de structure ne suffit pas. Une observation ultérieure contradictoire invalide la preuve. Une lecture échouée peut être remplacée par une lecture réussie du même capteur ; un effet inconnu reste bloquant.

`service_worker.js` ajoute les identités de l'observation, du document et de la fenêtre sources aux reçus d'interaction, y compris la navigation vers un href déjà observé. Il ne change pas la sélection des actions. Les chemins de clic et les protections Chrome existants sont conservés.

### Continuité, budget et interface

Dans la boucle commune Groq/Cerebras de `agent_runtime.py`, une fin textuelle prématurée peut déclencher au plus deux continuations à des checkpoints frais distincts. Elles consomment le budget de tours existant. Elles exigent un objectif de navigation reconnu et des preuves ordinaires ; elles sont refusées en présence d'approbation, d'exécution supervisée ou d'effet incertain. Dès que la preuve indépendante est suffisante, la boucle rend le résultat sans demander au modèle de le reformuler.

Les gardes de reprise refusent un nouveau `open_url` déjà envoyé, un clic déjà envoyé sur la même référence et une mutation sans DOM frais. Elles ne délivrent aucune permission. Les reçus connus sont également persistés lors d'une pause fournisseur, dont HTTP 429. `mission_outcome_policy.py` traite un résultat techniquement réussi mais `outcome_unknown=true` comme nécessitant une revue.

`assistant_v3.py` et `mission_trace_v10h.py` gardent l'objectif et les indices d'étapes de la mission sur plusieurs tours. Les indices de dispatch restent distincts d'une preuve d'objectif. La trace mémoire accepte le certificat indépendant SQLite. `operator_console.py` affiche en vert « OBJECTIF CERTIFIÉ » seulement pour un objectif vérifié ; un tour terminé sans preuve porte un badge ambre distinct. `operator_telemetry.py` expose la portée du certificat.

Autres fichiers : `scripts/run_final_regression.py` isole aussi le registre de cerveaux et imprime chaque skip ; `project_roadmap.py` documente la priorité P0 ; `scripts/run_p0_acceptance.ps1` et `scripts/p0_acceptance.py` rassemblent le profil de recette dans un seul lancement. Les fichiers `tests/test_p0_live_reliability.py`, `tests/test_browser_goal_proof_v11b.py`, `tests/browser_bridge_replay.mjs`, `tests/test_live_regressions_v10d.py` et `tests/test_p0_launcher.py` portent les régressions.

## 4. Validation automatique

Les deux reproductions initiales échouaient : accusé mémoire négatif malgré la ligne SQLite, et parcours navigateur achevé sur deux tours mais non certifié. Elles passent après correction.

| Vérification locale, Python 3.12.10 sur Windows | Résultat |
|---|---|
| Nouvelles régressions P0 | 24 exécutées, 24 réussies. |
| Régressions du lanceur Windows PowerShell 5.1 | 2 exécutées, 2 réussies. |
| `python scripts/run_final_regression.py` | 1177 exécutées, 1176 réussies, 0 échec, 0 erreur, 1 skip explicite. |
| `node --test tests/browser_bridge_replay.mjs` | 46 exécutées, 46 réussies, 0 skip. |
| Imports SDK officiel MCP | Réussis. Pas de serveur personnel sollicité. |
| Lanceur `run_p0_acceptance.ps1 -CheckOnly` | Réussi sous PowerShell 7 et Windows PowerShell 5.1. Imports Qt, audio, dotenv, OpenAI-compatible et Windows COM. Aucun modèle ni interface démarré. |

Skip local : `test_file_artifact_v10e.FileArtifactTests.test_symbolic_link_is_not_accepted_as_independent_file_proof`, motif « File symlinks are not available for this test account ». Le contrat de symlink n'est donc pas validé par ce compte local ; son état CI doit être lu séparément.

La première régression complète a exposé une erreur d'isolation : un test de cooldown lisait le registre personnel de cerveaux. Le runner utilise maintenant `JARVIS_BRAIN_CONFIG_PATH` dans son répertoire temporaire. Il n'a pas modifié ce registre personnel. La régression complète corrigée passe.

Les tests P0 couvrent notamment le commit mémoire suivi d'une exception, les reçus mal formés, le redémarrage de processus, les permissions, les contradictions de lecture, le menu non prouvé, les références périmées, la reprise sans nouveau dispatch et la transition simulée Cerebras primaire 429 → secondaire dans la même boucle. Les appels simulés ne démontrent ni le quota réel ni le coût réel du fournisseur.

La CI de la nouvelle PR est déclenchée après publication. Son résultat sur le SHA final est communiqué dans le rapport de livraison ; la CI V11A citée plus haut constitue seulement l'état de départ.

### Correctif de lancement Windows PowerShell 5.1

Le premier lancement réel signalé par l'utilisateur a échoué avant l'interface : Windows PowerShell 5.1 supprimait des guillemets du bootstrap passé à Python avec `-c`. Le contrôle initial utilisait PowerShell 7 et ne couvrait pas cette frontière. Le bootstrap est maintenant un fichier Python, lancé avec des arguments ordinaires. Deux régressions exécutées avec le vrai `powershell.exe` reproduisaient le défaut et passent après correction : `-CheckOnly` avec chemins contenant des espaces et chemin de lancement journalisé avec configuration de bridge absente. Aucun de ces tests ne démarre une interface ou un appel modèle. La commande exacte avec la configuration existante passe aussi en `-CheckOnly` sous Windows PowerShell 5.1. L'appairage de l'extension reste valable.

## 5. Interface

Le changement se limite aux statuts, à la portée de preuve et à la continuité de trace. La disposition générale et la règle « microphone arrêté en mode texte, réponse vocale disponible » restent dans leurs chemins existants. Leur fonctionnement avec ces correctifs doit être confirmé en recette LIVE.

## 6. Prochaine étape : P1 dans le runtime existant

P0 reste en attente d'acceptation réelle. La suite vise un Unified Mission OS général, en étendant les contrats existants plutôt qu'en créant une nouvelle boucle d'exécution.

1. Définir des sous-objectifs typés avec contraintes, scope, preuves exigées et résultat attendu. Réutiliser `MissionContract`, le graphe et le contexte durable.
2. Composer les vérificateurs : navigation, lecture de contenu, artefact/fichier, mémoire et outils connectés. Certifier le goal seulement lorsque tous les objectifs obligatoires sont prouvés ; aucune permission ne découle du certificat.
3. Propager un budget de mission et des états de récupération explicites. Distinguer lecture répétable, mutation prouvée et effet inconnu à revoir.
4. Relier la même projection de mission à la réponse, à la trace et au panneau opérateur après redémarrage.

Dépendances : résultat des recettes mémoire/navigateur ci-dessous, preuves P0 structurées, gardes existantes et suite verte. Critères : une mission nouvelle mêlant navigation, lecture et écriture de fichier ; redémarrage et reprise sans doublon ; refus d'une exigence non prouvée ; mutation sensible conservant ses autorisations ; comparaison de plusieurs missions différentes sans règles par site.

## 7. Recette LIVE

Voir [P0_WINDOWS_ACCEPTANCE.md](P0_WINDOWS_ACCEPTANCE.md) : préparation unique de l'extension, une commande de lancement, demandes naturelles et observations attendues. Ne marquer `windows_real` comme validé qu'après les résultats observés de ces essais.
