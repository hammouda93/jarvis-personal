# V13 — analyse de la recette V12 et corrections

Base : V12 `c5d0cdd`. Analyse du journal utilisateur du 15 octobre 2026. Les
scénarios de régression utilisent des données synthétiques ; aucun compte
personnel, message réel ou document Windows n’a été manipulé par ces tests.

## Résultats observés

| Essai | Constat du journal | Diagnostic / suite |
| --- | --- | --- |
| Démarrage complet | Dépendances et bridge OK, interface ouverte | Les warnings PowerShell/COM/DPI ne sont pas un arrêt du programme |
| Mémoire explicite | `PERSISTED_VERIFIED`, puis recherche du même souvenir après redémarrage | Rappel durable réellement observé ; ne pas réinitialiser SQLite |
| MCP GitHub | Identité, branches et commits reçus ; pull requests refusées avant dispatch | Ancienne tentative inconnue #3 ; garde persistante conservée |
| Changement de mission | `JSONDecodeError`, ancienne mission réutilisée | Contexte de contrôle envoyé au mauvais objet décorateur |
| Recherche puis premier article | Navigation initiale bloquée, observation d’un onglet sans rapport | Frontière entre ouverture de recherche et sélection de résultat mal détectée |
| Nouveau document | Remplacement du contenu d’un ancien journal ouvert | Condition de valeur vide active seulement si le mot « vide » était explicite |
| Cascade | 429 du principal, réponse du secondaire | Bascule observée ; les quotas du fournisseur restent applicables |
| Voix | ElevenLabs indisponible, voix Windows utilisée | Repli observé ; disponibilité ElevenLabs non validée |

Le remplacement dans Bloc-notes concerne le buffer ouvert. Le journal ne montre
pas d’enregistrement sur disque du fichier précédent. Il ne faut pas confondre
texte correctement relu et création du nouveau document demandé.

## Corrections de fond

Le résolveur de fournisseur traverse maintenant les décorateurs avant de poser
l’état temporaire d’une requête de contrôle. Auparavant, un attribut était posé
sur le wrapper tandis que `_chat` s’exécutait sur le fournisseur interne : il
voyait la conversation et son catalogue d’outils. Les tests vérifient désormais
la requête réellement envoyée au faux client, sans outils, avec le système de
contrôle et sans mutation de l’historique. La cascade existante reste utilisée.

Une commande autonome telle que « changement de mission et oublie tt » détache
localement le suivi sans dépendre d’une sortie JSON. Elle ne supprime pas SQLite
et ne lève pas les verrous d’une ancienne tentative. Les autres changements de
sens restent interprétés puis validés par le contrat de mission. Un bloc JSON
Markdown seul est accepté ; du texte arbitraire autour d’un objet ne l’est pas.

Une ouverture de recherche précédant « lis le premier résultat » est maintenant
une phase de navigation initiale. L’ouverture du nouveau site est permise dans
cette phase ; la sélection du résultat exige toujours un lien réellement
observé. Une lecture ou synthèse demandée empêche la terminaison rapide après
le seul clic vérifié.

Une demande de **nouveau document** impose une valeur vide relue avant la
première écriture, même sans le mot « vide ». Les outils UIA et Computer Core
utilisent la précondition existante, qui refuse une valeur non vide ou inconnue
avant dispatch. Les saisies visuelles ou clavier sans preuve de valeur ne
contournent pas cette condition. Après refus, Jarvis reçoit l’instruction de
créer un document vierge par un contrôle observé ou Ctrl+N dans la fenêtre
inspectée. Aucune nouvelle instance d’application n’est imposée.

Les lignes stderr natives sont converties en texte dans le pipeline du lanceur
avant journalisation. Les warnings gardent leur contenu sans l’emballage
PowerShell `NativeCommandError`. Le code de sortie du processus reste contrôlé.
Cette présentation ne désactive ni les avertissements COM ni les contrôles DPI.

Le panneau Architecture évite de reconstruire les vues HTML inchangées à chaque
rafraîchissement. La position de lecture de la feuille de route est conservée,
ainsi que la sélection du profil lors d’une actualisation des outils.

## Revue MCP explicite

L’ancienne tentative inconnue ne doit pas être effacée pour rendre la recette
verte. Dans **ARCHITECTURE**, cliquer **Examiner le blocage MCP** ouvre
**MCP → Services → Journal** et sélectionne la tentative. Le bouton de revue
existant enregistre uniquement une vérification de l’opérateur ; il ne certifie
pas une mission et ne rejoue aucun appel. Les accès restent autorisés séparément.

## Validation automatique et visuelle

26 contrôles sont ajoutés : 12 reproductions et garde-fous de runtime, 13 tests
du catalogue et des widgets, un contrôle de navigation et de géométrie dans
l’interface complète. Les scénarios de contrôle vérifient aussi le fallback
Cerebras secondaire et la restauration du contexte après erreur fournisseur.

La régression locale examine 1 384 tests : 1 374 réussissent, dix contrôles
spécifiques Windows sont explicitement ignorés, aucun échec ou erreur. Les
46 replays Browser Bridge réussissent. Les nouveaux panneaux ont été rendus
hors écran et inspectés visuellement ; la géométrie minimale est testée à
660 × 500. Ces vérifications n’exécutent pas les missions sur un bureau réel.
La première CI Windows a également détecté un débordement horizontal de 42
pixels dans la nouvelle vue à petite taille. La navigation occupe maintenant
deux colonnes et le diagramme s’adapte à la largeur disponible. Un test de vue
étroite avec police héritée agrandie couvre ce cas ; les barres horizontales ne
sont pas simplement masquées. La CI du commit corrigé doit être observée sous
Windows et Linux.

## Recette ciblée

Après mise à jour de `feature/architecture-console-live-fixes-v13`, conserver la
même commande `scripts/run_p0_acceptance.ps1 -Full -EnvPath ...` et la même
configuration. Le commit utilisé s’affiche au lancement.

1. Consulter **ARCHITECTURE → Agents et outils** ; chercher `browser_write`,
   `msf_query_records` et un outil MCP déjà configuré. Vérifier les statuts
   runtime partagé, rôle déclaré et planifié, puis les préférences OFF.
2. Examiner l’ancien reçu MCP, puis redonner la demande d’inspection du dépôt.
   Ne pas utiliser la revue comme attestation inventée d’un effet externe.
3. Depuis une mission suspendue, donner une demande de mémorisation indépendante.
   Elle doit pouvoir être traitée sans redémarrer. Tester aussi **Nouvelle demande**.
4. Redonner la recherche d’actualité Python avec lecture du premier résultat et
   résumé avec URL ; un clic réussi seul n’est pas le résultat complet.
5. Garder un ancien document non vide ouvert, demander un nouveau document avec
   une phrase Unicode. L’ancien texte doit rester intact ; le nouveau document
   doit être créé, rempli et relu, puis laissé ouvert sans enregistrement.

La [décision d’architecture](JARVIS_ARCHITECTURE_V13.md) précise ce qui est
implémenté, ce qui est déclaré et l’ordre des prochaines étapes. La validation
automatique ne remplace pas cette recette sur le bureau et les comptes réels.
