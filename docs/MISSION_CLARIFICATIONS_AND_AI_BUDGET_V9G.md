# Jarvis 9G — Clarifications de plan et maîtrise des appels IA

Branche isolée : `feature/mission-clarification-budget-v9g`
Base : `38aa8dd` (9F). PR brouillon #17 vers
`feature/codex-jarvis-final-development`.

## Pourquoi cette branche est isolée

Le dépôt de développement Windows contient des travaux **locaux non
committés** sur les Skills (`agent_knowledge.py`, `skill_control.py`,
`skills_operator_panel.py` et d'autres fichiers). Cette branche ne
les a ni remplacés ni supprimés. **Ne pas faire de reset/clean sur leur copie.**

## Améliorations livrées dans cette branche

1. Le Centre de contrôle présente le plan et les clarifications juste après
   les commandes de mission, au lieu de cacher les questions dans les
   diagnostics avancés. Le texte de chaque question est intégralement
   visible dans une zone défilante (le planificateur borne déjà chaque
   question à 120 caractères). Les étapes, états et questions sont lisibles
   sans parcourir les panneaux techniques.
2. Une zone permet de répondre aux questions avant l'approbation du plan.
   Une demande de clarification envoie **une seule requête séparée** au
   fournisseur configuré, **sans aucun outil**. Le nouveau plan est contrôlé
   par le parseur strict, puis sauvegardé et versionné. L'ancien plan et
   les réponses sont conservés dans le checkpoint propriétaire.
3. Refus explicite de réviser un plan approuvé, déjà exécuté, bloqué
   ou contenant des preuves. Cela évite la perte d'autorisations et
   de preuves, ainsi que les ré-exécutions silencieuses.
4. Une question simple sur la mission active (« C'est quoi notre mission ? »,
   « Où en est le plan ? ») utilise seulement les checkpoints enregistrés,
   **même si l'exécution est bloquée**. Aucune requête LLM ou action
   navigateur/Windows n'est nécessaire.
5. Après un HTTP 429 Cerebras, un coupe-circuit en mémoire évite de rappeler
   le **même fournisseur** pendant 60 secondes. Les deux comptes,
   principal et secondaire, possèdent des états séparés. Pas d'attente
   active et aucune augmentation des quotas.
6. Avant un fallback Groq, une estimation prudente du volume des messages,
   des définitions d'outils et de la réponse probable empêche un appel
   vraisemblablement supérieur à l'enveloppe configurée. Valeur par défaut :
   7 000 tokens estimés (paramètre :
   `JARVIS_GROQ_FALLBACK_ESTIMATED_TPM_BUDGET`). C'est une **estimation**,
   pas un tokenizer officiel ni la limite réelle du compte utilisateur.
   Le fallback n'envoie alors **aucune requête** plutôt que d'insister
   avec un HTTP 413 prévisible.

## Limites et sécurité

- La réponse de clarification **révise le plan** ; elle n'exécute rien.
  Une approbation du superviseur est encore nécessaire avant les effets.
- Le système refuse de réviser une mission qui a déjà produit des actions,
  même seulement en lecture seule. Utiliser un nouveau plan après exécution
  nécessite une conception supplémentaire de réconciliation de preuves.
- Le coupe-circuit est volontairement limité à un processus et aux
  erreurs détectées de type 429. Il ne survit pas encore au redémarrage.
- Une prévalidation Groq conservative peut écarter des requêtes qui auraient
  été acceptées. Elle évite la consommation inutile mais n'optimise pas à
  elle seule les longs contextes.
- La limite actuelle de 6 étapes par plan n'a pas été augmentée : cela
  nécessiterait une vraie stratégie de décomposition hiérarchique.
- La réduction structurelle des requêtes LLM d'une **mission longue**
  demande encore un plan d'exécution durable, des transitions déterministes
  auditables, l'anti-duplication et une supervision sûre.
- Aucun test simulé, même vert, ne prouve le fonctionnement réel de Chrome,
  WhatsApp, Windows, Ollama ou l'exécution Business Agent.
- Ne jamais fusionner automatiquement dans la branche contenant les Skills
  inachevés avant d'en avoir revu les différences.

## Tests d'acceptation Windows prévus

Avec un **worktree séparé** et une mémoire de test isolée :

1. Créer une mission sans agir ; générer un plan comportant `unresolved`.
2. Lire **toutes** les étapes et questions dans la carte de plan.
3. Donner une réponse dans le champ de clarification.
4. Vérifier la révision et le nouvel état, sans action externe.
5. Redémarrer complètement ; reprendre la mission et vérifier la persistance.
6. Vérifier que répondre à « C'est quoi notre mission ? » ne lance pas d'API.
7. En cas de 429, vérifier la réduction des tentatives répétées.
8. Rejouer les parcours YouTube réussis de 9F avec les mêmes phrases, sans
   mission explicite, et comparer les preuves `browser_find`, `browser_write`,
   `browser_press`, `browser_click`, `browser_back`, `browser_close_tab`.
9. Conserver les scénarios mémoire V5, Windows et MS Football comme
   régressions à valider, sans effets sensibles par défaut.

## Travaux ultérieurs — ne pas déclarer le produit terminé

- Réconcilier les Skills locaux déjà modifiés par Codex.
- Développer la révision de plan en cours de mission avec règles de
  conservation/invalidation des preuves et autorisations.
- Réduire le nombre d'appels IA avec des batches déterministes **autorisés**,
  une progression événementielle et des observateurs fiables.
- Gérer les quotas des fournisseurs selon les en-têtes de délai et les
  métriques réellement disponibles, avec visibilité dans l'UI.
- Scheduler durable, intégrations MCP/OAuth, Business Workspaces,
  délégation multi-agent et capacités cloud.
- Campagne d'acceptation Windows réelle, protection contre les régressions,
  packaging et documentation d'exploitation.

## Reprise Codex

Comparer la PR #17 à 9F ; la réconcilier avec les modifications locales des
Skills **sans écraser les fichiers locaux**. Poursuivre la roadmap après ces
correctifs. Les commits ici ne constituent pas une clôture de Jarvis.

## Correctifs post-acceptation du 9 octobre 2026

Un journal réel (YouTube, WhatsApp Web, Instagram, MS Football, Windows Cursor)
a été inspecté après la première validation des tests 9G.

Observations confirmées :
- YouTube : recherche, sélection, retour et fermeture ciblée généralement
  fonctionnels ; une navigation `back` a échoué après navigation par un lien.
- WhatsApp : un `browser_write` vérifié a été présenté comme « Message envoyé »
  avant la vraie soumission ; suivi d'un envoi réel dans une autre conversation.
- Cerebras principal : **HTTP 402 payment_required à chaque tour**, jusqu'à
  la sélection du secondaire. Cette erreur est différente du HTTP 429.
- Cerebras secondaire : parfois HTTP 429. Préflight Groq a correctement
  bloqué un contexte estimé > 7 000 tokens plutôt que d'envoyer une requête
  manifestement trop grosse.
- Mémoire V5 : `semantic_memory_ollama_unavailable:timed out` intermittent.
- MS Football : des réponses sur le nombre de matchs ont été contredites par
  l'interface ; une modification de statut a été amorcée et un **agent
  Performance réel** a été lancé. Cet état de production ne doit pas être
  confondu avec une fixture de test.
- Installer Cursor : étapes UIA franchies, fin d'installation non prouvée.

Modifications complémentaires :
- Les comptes Cerebras renvoyant HTTP 402 sont désormais mis hors circuit
  dans le processus en cours : **ne pas retenter la même clé** à chaque
  tour ; les autres comptes restent autorisés. Recréer le processus après
  toute correction du compte. Les HTTP 429 conservent le cooldown temporaire.
- Un garde-fou de réponse rejette une affirmation « message envoyé » si les
  actions navigateur ne montrent qu'une écriture ou aucune soumission vérifiée.
  Il ne relance jamais automatiquement une action d'envoi.

Limites explicites :
- Le mécanisme n'accorde ni crédit fournisseur ni quota API supplémentaire.
- La preuve de livraison distante d'un message reste plus stricte que la
  simple disparition du texte du champ ; vérifier dans l'interface réelle.
- L'exécution continue d'installer des logiciels n'est pas encore validée.
- Les questions de cardinalité/grands tableaux HTML exigent encore un
  raisonnement ancré dans une liste structurée de fiches, et non une réponse
  extrapolée du texte aplati.
- Ne pas exécuter à nouveau des changements en production pour « tester »
  les validations sans un environnement et des permissions dédiées.
- CI et tests locaux ne remplacent pas les tests Windows sur cette version.
