# Jarvis — Étape 9C : génération contrôlée de plans par le même modèle

## Position du projet

- **Étapes 1–8/11** : code intégré + tests automatisés antérieurs ; tests
  complets en conditions réelles Windows volontairement reportés.
- **Étape 9/11 (en développement)** : 9A hub MCP ; 9B propositions
  d'agents et de capacités, selon les preuves et autorisations ;
  **9C : plan sémantique structuré généré sur demande par le modèle actuel.**
  L'exécution autonome multi-agents et les connecteurs OAuth réels restent
  ouverts : **l'étape 9 globale n'est pas terminée**.
- Étapes 10 et 11 : apprentissage/Skills et intégrations planifiées à poursuivre.

## Ce qui est réellement intégré en 9C

Le bouton **« ◈ Générer un plan (même IA · 1 requête) »**, dans le
centre **▤ Supervision**, est un acte utilisateur explicite. Jarvis :

1. exige une mission multi-échanges **active**, appartenant au bon utilisateur ;
2. refuse si la mission est bloquée, terminale, ou contient déjà un plan ;
3. émet **une seule requête supplémentaire** à son fournisseur de modèle
   déjà configuré : Cerebras/Groq via le client Chat Completions existant,
   OpenAI Responses sans outils, ou Ollama local JSON ;
4. ne fournit **aucune définition d'outil** à cette requête, ne modifie pas
   la conversation en cours, ne démarre aucun agent et ne clique nulle part ;
5. admet une réponse JSON **uniquement si** elle satisfait un contrat strict :
   1–6 étapes, identifiants propres, dépendances uniquement antérieures,
   1–3 critères observables par étape, et 0–8 ambiguïtés explicites ;
6. réutilise le store existant via
   `LiveMissionContinuityRuntime.register_semantic_plan` ; aucun statut
   « réussi », aucune preuve, aucune approbation ou autorisation MCP n'est
   créée ;
7. laisse le superviseur de V4 comparer ensuite le plan enregistré aux preuves
   indépendantes, et le planificateur de 9B suggérer les agents/outils
   **déjà autorisés** sans exécution autonome.

## Le coût et les erreurs

Cette action ajoute **une requête LLM** au fournisseur sélectionné. Elle
consomme potentiellement des tokens et les quotas Cerebras ; elle n'est
**jamais** lancée en arrière-plan à chaque message ni pendant la découverte
MCP. En cas de quota, JSON invalide, timeout ou plan non conforme, le
checkpoint reste intact, et aucune tentative supplémentaire n'est lancée
automatiquement.

La requête de planification envoie le texte de l'objectif au même fournisseur
que celui déjà sélectionné par l'utilisateur. Elle ne transmet pas les
identifiants MCP, les secrets de la machine ni l'historique de la conversation.

**Limite connue :** la génération produit une proposition structurée et
enregistrée, **pas** une garantie de validité sémantique et **pas** une
exécution automatique. Le modèle peut proposer des critères imprécis ou
oublier des détails ; leur qualité doit être revue avant les opérations
réelles. Les étapes suivantes ajouteront une validation de plan et une
délégation supervisée sans double exécuteur.

## Gouvernance

- Repo : `hammouda93/jarvis-personal`
- Branche : `feature/semantic-planning-authoring-v9c`
- Parent protégé de travail : `feature/mcp-capability-planner-v9b`
  @ `ad62592d4c87841a8f157155bf26754e659b6f62`.
- Stable historique `fix/browser-live-contracts-v2` préservé, aucune fusion.
- Workflow de preuve : `.github/workflows/semantic-planning-authoring-v9c.yml`;
  tests mockés sans connexion et régressions Windows/Linux/Chrome.
- Tester en conditions réelles plus tard : mode texte (micro OFF, TTS ON),
  mission attachée/reprise, Cerebras (quota), Chrome et onglets, UIA/CUA,
  WhatsApp avec accord utilisateur et un MCP spécifique de bout en bout.

## Nouveau parcours « planifier avant d'agir »

Le bouton **« ＋ Créer mission sans agir (plan d'abord) »** ouvre la même
mission persistante, mais n'appelle **ni modèle, ni outil** et n'émet aucune
instruction Windows/MCP. Il préserve l'ancien bouton **« Démarrer et exécuter »**
pour les parcours directs existants. Le propriétaire peut ensuite :

1. Créer une mission sans exécution.
2. Cliquer explicitement sur **« Générer un plan (même IA · 1 requête) »**.
3. Examiner le contrat et les ambiguïtés via **« Vérifier la progression »**.
4. Consulter les agents et MCP potentiels via **« Proposer agents / capacités »**,
   toujours en lecture seule.
5. Continuer la conversation dans la même mission ; toute action autorisée
   repasse par le moteur et les protections d'origine.

Ce parcours ne permet pas encore de déléguer automatiquement une étape à
un autre agent, et l'approbation d'une action MCP reste requise à chaque appel.
