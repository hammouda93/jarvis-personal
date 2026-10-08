# Delegation controlee 9E

## Architecture

Le superviseur utilise `AgentFactory`, deja present, pour des responsabilites
logiques en processus : recherche, navigateur, Windows, documents/fichiers,
communications, donnees/tableurs, automatisations, MCP, rapports et coordination.
MS Football et developpement conservent leurs manifestes historiques.
Chaque builder est enregistre par le code de confiance. Il appelle une seule
fois `LiveMissionContinuityRuntime._run`, avec le cerveau et les wrappers actuels.
Il n'existe ni nouveau modele de delegation, ni dispatcher, ni processus enfant.

Le registre unique filtre les schemas ET refuse un appel hors perimetre,
meme si un modele ignore ses schemas. Les roles recoupent les outils reellement
exposes. L'operateur peut restreindre ce perimetre par etape. Un outil MCP doit
etre nomme explicitement dans l'affectation, autorise dans le Hub et confirme
pour chaque execution. Une description distante ne donne aucune permission.
Un role sans outil disponible est bloque, pas presente comme fonctionnel.
Le role automatisations attend le scheduler durable du jalon 11.

Les outils herberges par le fournisseur Responses sont exclus en supervision :
ils ne passent pas par le registre local, ses limites ou son journal. Leur voie
historique reste disponible hors supervision. La memoire V5 conserve sa logique ;
ses appels de classification/projection sont maintenant comptes dans le budget
de mission. Un arret du superviseur n'est pas transforme en fallback V5.

## Coordination Et Permissions

`run_supervised_mission(max_steps=24)` avance sequentiellement sur le worker
existant. Il s'arrete sur confirmation, preuve manquante, incertitude, erreur,
limite ou signal d'arret. Il n'attend pas en boucle une preuve et ne redemande
pas un tour deja execute. Les dependances configurees sont reobservees avant
delegation ; toutes les conditions configurees sont reobservees avant completion.

Le signal d'arret passe par un Event thread-safe, sans action Qt sur le runtime.
Il est teste avant chaque requete de modele, action ou observation. Il ne peut
pas interrompre atomiquement un appel natif/de reseau deja en vol. Un effet
possible reste bloque pour revue manuelle. Aucun effet inconnu n'est retente.

Une confirmation est liee a mission, etape, outil et arguments exacts, puis
consommee AVANT dispatch. Une permission ancienne, une autre etape ou des
arguments modifies sont refuses. Une confirmation dont le contexte fournisseur
est perdu lors d'une reprise requiert une revue. Apres reconciliation manuelle
`completed`, la reprise ne fait que verifier ; `not_executed` autorise une
nouvelle delegation explicite sans supprimer les protections natives du ledger.
Une annulation reste possible quand le budget de reprise est epuise.

La fabrique refuse un second execute concurrent d'une instance en cours.
Suspendre un appel en cours puis le remettre a CREATED aurait permis un doublon ;
seules les instances non demarrees peuvent maintenant etre suspendues/reprises.
Terminate est un etat de suivi, pas une garantie d'arret d'un effet en vol.

## Rapports Et Interface

Les rapports structures viennent du code de confiance, pas de JSON produit par
le modele. Ils comprennent mission/etape, responsabilite, processus logique,
objectif/action demandee, actions, observations, permissions, resultat minimal,
references de preuves, verification et risques. Aucun texte de resultat, argument
d'action ou corps d'observation n'est recopie dans ces rapports.
Le graphe durable conserve la responsabilite effective du tour. La telemetrie Qt
projette uniquement des resumes, sans arguments, secrets ou references privees.

Le panneau existant propose affectation par etape, perimetre JSON, tous les
budgets, avance simple, coordination bornee, arret et historique synthetique.
Les commandes et les reponses texte/voix restent celles du worker existant.

## Validation Et Limites

`tests/test_controlled_delegation.py` couvre chaine de dependances, meme cerveau,
assemblage reel des wrappers avec fournisseur simule, scopes de schema/dispatch,
retrait d'outil, confirmations exactes, restart, arret, absence de rejeu, recuperation,
budgets V5, concurrence et commandes Qt. Le ledger conserve les observations de
verification, sans les confondre avec une boucle d'inspection du modele.

Ces tests ne prouvent aucune navigation Chrome, operation UIA/CUA, parole/audio,
integration personnelle ou livraison de message reelle. Les limites de 9D sur
les regles de preuve et le comptage approximatif des sous-requetes de drivers
restent applicables. OAuth et les effets programmes ne sont pas valides ici.
