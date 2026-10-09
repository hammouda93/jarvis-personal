# Continuation de mission 9F

## Contrat

La fin d'une reponse du modele n'est pas la fin du but utilisateur.
Le plan semantique approuve, son objectif d'origine et ses predicates de preuve
restent dans le checkpoint de `LiveMissionContinuityRuntime`.

`mission_checkpoint` est un controle local, disponible uniquement dans une
execution supervisee. Il n'appelle ni Windows, ni MCP, ni un autre modele.
Il accepte uniquement `continue`, `awaiting_verification`, `blocked`.
Il ne peut ni attribuer une preuve, ni approuver une mutation, ni terminer la mission.
Le controle est invalide apres une nouvelle execution d'outil : un ancien
compte rendu ne peut pas conclure un etat modifie ensuite.

Cerebras/Groq demandent une disposition structuree avant un retour purement
textuel en mode supervise. Le meme fournisseur et le meme historique sont
utilises. Les reparations DOM/UIA existantes restent actives.

Le superviseur autorise la coordination d'un nouveau tour si du travail
ordinaire reste, que des outils ont effectivement reussi, et qu'aucune action
n'est incertaine, en echec ou soumise a approbation. Une limite de tour signalee
par le runtime peut egalement rendre la main a la coordination, sans relever
la limite de tours. Les budgets durables de mission ne sont jamais reinitialises.

Quand les predicates d'objectif sont satisfaits, ils sont reobserves avant
cloture. Le texte du modele n'est pas une preuve. Si une verification est
attendue, on reobserve seulement; on ne rejoue pas le tour d'action.

## Arret, Reprise Et Permissions

La coordination est sequentielle sur le worker existant. Les rapports Qt sont
non bloquants et n'entrainent pas de synthese vocale intermediaire.
Une conversation rattachee a un plan approuve utilise cette coordination,
y compris apres une confirmation. L'arret hors bande est verifie avant toute
nouvelle requete/outillage et observation supervisee. Une requete reseau deja
partie ne peut pas etre annulee retroactivement.

Les confirmations restent liees au digest mission/etape/outil/arguments.
Les digests des mutations reussies sont conserves par etape; une continuation
ne redispatche pas un appel identique. Pour une UI modifiee, il faut des cibles
fraiches, pas une ancienne reference rejouee. Un effet inconnu, une interruption
ou une erreur de stockage imposent une revision; aucune reprise automatique
apres redemarrage ne relance les effets externes.

## V5 Et Compatibilite

V5 expose maintenant `run_with_context`, au lieu d'une delegation implicite
qui sautait toutes les operations memoire. Les operations explicites passent
toujours par V5, avec la preparation `begin_turn` compatible Hermes.
Un tour interne d'un role non-memoire ne reclassifie pas chaque etape Windows
via Ollama. Les erreurs de classification non-memoire conservent le contexte
et passent au cerveau principal, sans inventer des faits persistants.

## Limites

- Activation via les flags existants de supervision; pas de remplacement du runtime.
- Le plan doit etre approuve; la transformation automatique de toute demande
  naturelle en mission executable/verifiable reste ouverte.
- L'approbation de plan ne donne pas carte blanche sur les mutations.
  Les transitions UI courantes restent conservatrices et peuvent demander
  trop de confirmations. Une politique plus precise reste necessaire.
- Les reponses sans disposition ni budget-yield ne declenchent aucune relance.
- Les captures Qt et les fixtures ne prouvent pas une installation Windows,
  une livraison de message ou une mutation metier reelle.
