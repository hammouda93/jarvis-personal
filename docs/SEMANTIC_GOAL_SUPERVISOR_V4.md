# Jarvis Personal — Semantic Goal Supervisor V4

**Étape 8/11 — couche sémantique déterministe.** Cette livraison ne remplace ni
le modèle, ni les outils Windows, ni Chrome Bridge. Référence d'architecture :
Hermes Agent (NousResearch), sans installation ni copie de son code.

## Fonctionnel dans cette branche

- `semantic_goal_supervisor.evaluate_mission` : calcul **sans effet de bord**
  basé sur les checkpoints de mission enregistrés et sur le plan existant.
- Vérification des dépendances entre étapes et détection des preuves absentes,
  des ambiguïtés, des exigences non définies et des situations bloquées.
- États indépendants : `plan_needed`, `evidence_missing`,
  `evidence_spec_needed`, `clarification_required`,
  `manual_review`, `invalid_plan`, `goal_proof_pending`,
  `goal_verified`. Aucun retour « outil OK » ne certifie le but utilisateur.
- Méthode `LiveMissionContinuityRuntime.review_mission`, sécurisée par
  l'identité du propriétaire, réutilisant les stores et le graphe déjà
  présents, sans réexécution, nouveau coût LLM ni écriture SQLite.
- UI réelle PySide6 : onglet **CONTRÔLE SÉMANTIQUE / PREUVES** dans
  **▤ Supervision**, affichant état, prochaine action suggérée, prérequis
  des étapes et liste des preuves manquantes. Lecture SQLite bornée et
  read-only avec les protections de confidentialité existantes.
- Bouton **◇ Vérifier la progression** : transmis au thread `AssistantWorker`
  comme une commande de revue *read-only*, sans effet externe.
- Champ **Critères de preuve** : l'utilisateur peut enregistrer de 1 à 8
  critères explicites pour sa mission (identifiants séparés par virgules).
  Ils deviennent des **conditions à prouver**, jamais des preuves.
  L'enregistrement réutilise `register_semantic_plan` sans moteur secondaire.
  Un plan sémantique existant ne peut être remplacé silencieusement.
- L'observation d'une action « vérifiée » ne déclenche **ni**
  `register_goal_evidence` **ni** `complete_mission`.
  Ces opérations existantes demeurent réservées à une couche de vérification
  indépendante et de confiance; leur vérification en situation réelle reste
  un jalon d'acceptation.

## Limites réelles

- Le superviseur est **déterministe et non autoritaire** : il évalue des
  preuves *enregistrées* mais n'authentifie pas indépendamment un reçu
  externe et ne fabrique pas de témoins d'observation.
- Pas de génération automatique de plan, pas de nouvelle requête LLM, pas
  d'agent spécialisé, pas de dispatch autonome et pas de boucle de
  récupération qui répète des écritures. Ces sujets restent hors périmètre
  de V4 (prochaine étape : planification/délégation après vérifications).
- Les critères peuvent être saisis dans l'interface, mais **les preuves
  correspondantes ne peuvent pas être auto-approuvées par l'interface**.
  Le comportement est volontairement conservateur jusqu'à l'intégration
  d'un vérificateur indépendant compatible avec les outils.
- Les tests GitHub Actions ne certifient pas les comportements réels
  Chrome/Windows/WhatsApp sur l'ordinateur de l'utilisateur.

## Validation de ce jalon

Workflow : `.github/workflows/semantic-goal-supervisor-v4.yml` avec tests
Python critiques Windows, replays Browser Bridge et suite Python Linux.
Tests dédiés : `tests/test_semantic_goal_supervisor.py`.

Cas : plan absent, champs sans preuve, dépendances, ambiguïtés, résultat
outil positif non-probant, checkpoint incertain et blocage des nouvelles
actions, confidentialité des références de preuve dans l'interface,
réouverture par propriétaire, bouton Qt qui ne déclenche aucun outil ni LLM,
critères utilisateur validés et sans possibilité d'injection de commande.

## Position du programme

Les étapes 1–7 sont dans les branches parents, intégrées et passées dans
des suites automatisées. La validation **réelle Windows reste ouverte**.
Étape 8 : cible de cette branche; développement et CI requis avant de la
marquer « intégrée ». Les étapes 9–11 restent à développer.

Branche stable protégée : `fix/browser-live-contracts-v2` @ `aff001fd`.
Aucune fusion sans validation réelle complète.
