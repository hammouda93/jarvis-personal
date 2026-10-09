# Supervision active des missions (9D)

## Architecture

Activation explicite : `JARVIS_ACTIVE_SUPERVISOR_ENABLED=1`. Ce mode active
egalement la continuite persistante et l'Action Ledger existants. Il ne
remplace ni Cerebras/Groq, ni Chrome, UIA/CUA, la memoire ou les conversations.
Le registre unique est enveloppe par `SupervisedToolRegistry` ; le modele
selectionne prepare toujours les arguments en utilisant ses outils existants.

Le panneau Supervision contient un formulaire de verification independante,
les budgets et les commandes Valider le plan / Etape suivante. Les commandes
passent par `MissionControlInbox` et le worker existant. Une validation du plan
ne declenche aucune action et ne vaut pas autorisation d'une action sensible.
Les reponses texte/voix d'une mission approuvee restent dans le meme runtime.

## Contrats et preuves

L'approbation est liee a l'identifiant de mission, au proprietaire et a une
empreinte exacte du plan revu. Les ambiguities doivent etre resolues et chaque
etape doit avoir des criteres. Les dependances sont evaluees a partir des preuves.

Une regle revue par l'operateur associe un critere a un outil natif d'observation,
a des arguments precis, un chemin JSON et une valeur attendue exacte. La sortie
de l'action et le texte du modele ne sont jamais des preuves. Les champs
`success` et `verified` d'un outil sont refuses comme predicat de but.

Les observations passent par le registre existant, apres chaque action et a
la fin du tour. Une observation incompatible retire la preuve intermediaire.
Toutes les regles configurees sont reobservees avant de conclure. Les criteres
sans regle restent en attente du verificateur externe de confiance existant.
Aucun outil MCP distant n'est considere comme un observateur fiable par sa
seule description. La configuration de ces verifications demande encore une
revue humaine : leur justesse semantique ne peut pas etre certifiee par le schema.

## Etats et reprise

Etats persistants : PLANNING, READY, ACTING, OBSERVING, VERIFYING,
WAITING_APPROVAL, BLOCKED, RECOVERING, COMPLETED, FAILED.

L'execution prend une etape a la fois. Un tour termine n'est pas repete lorsque
sa preuve manque ou disparait : seule l'observation est reprise. Une exception,
un effet incertain ou un checkpoint interrompu bloque la mission pour revue.
`resolve_recovery` conserve son contrat de preuve explicite et ne rejoue rien.
Une limite de recuperations persistante s'ajoute a ce contrat.

Budgets persistants avant dispatch : actions (observations incluses), requetes
modele, operations reseau, unites totales, temps et recuperations. Les hooks
modele couvrent Ollama, Responses et les requetes Cerebras secondaires/Groq.
Le compteur reseau du superviseur compte les operations provider/outils ;
il ne pretend pas compter les paquets ou toutes les sous-requetes internes
d'un pilote. Les timeouts existants restent necessaires pour une operation
deja commencee. Un budget epuise ne repete aucune action externe.

Le rapport delegue contient objectif, action demandee, responsable propose,
actions, observations, permissions, verification et risques. L'affectation
specialisee actuelle utilise les candidats du routeur ; une ambiguite reste
sous la responsabilite `interaction`. La coordination multi-etapes et le
catalogue de specialisations 9E doivent encore etre completes.

## Verification

Tests dedies : `tests/test_active_mission_supervisor.py` : refus d'un plan non
approuve/modifie, proprietaire, budgets, permissions, observation independante,
preuves perimees, effet inconnu, interruption, absence de rejeu et vrais widgets
Qt hors ecran dont les boutons emettent seulement des commandes worker.

Tests Windows reels, reseau MCP reel et acceptation utilisateur :
EN ATTENTE DE TEST REEL. La CI cumulative de la nouvelle branche doit etre
observee sur chaque commit publie avant de revendiquer sa validation distante.

## Reference Hermes

Code actuel inspecte :
[d94b70f](https://github.com/NousResearch/hermes-agent/commit/d94b70f675205c2c046138997819428772cd2678).
La supervision reutilise les principes de budgets, checkpoints et separation
des transports, mais l'implementation est propre a Jarvis. Aucun code source
Hermes copie, aucun runtime Hermes installe. L'inventaire detaille, les Skills
et le scheduler feront l'objet des prochains jalons.
