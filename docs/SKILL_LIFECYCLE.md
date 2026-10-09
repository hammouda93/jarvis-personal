# Skills Et Apprentissage Opt-In

Le stockage operationnel reste distinct de la memoire personnelle V5.
Deux interrupteurs du centre de controle sont independants, OFF par defaut :
Utiliser les Skills et Apprentissage operationnel.

| Skills | Apprentissage | Comportement |
| --- | --- | --- |
| OFF | OFF | Aucun contexte operationnel reutilise; aucune ecriture automatique |
| ON | OFF | Reutilisation des Skills/lecons/profils; aucune creation/promotion automatique |
| OFF | ON | Apprentissage possible sous les gardes existantes; aucune reutilisation/injection |
| ON | ON | Reutilisation et apprentissage sous les gardes existantes |

Le consentement est conserve dans `operational_preferences.json`, dans le
repertoire de donnees Jarvis. Il remplace le drapeau combine historique
`JARVIS_OPERATIONAL_LEARNING_ENABLED`; ce dernier ne reactive pas les modes.
Un fichier invalide/inaccessible laisse les deux modes OFF. Les changements sont
appliques par le worker entre executions; ils ne modifient jamais une action en
cours ni ne lancent une nouvelle action. La configuration n'est publiee au runtime
qu'apres une ecriture atomique reussie. Statut visible et journal de flags sans
contenu personnel. Aucun reset V5, de mission ou du contexte conversationnel.

## Cycle De Vie

Le panneau Skills permet consultation, edition, desactivation/reactivation et
restauration. Ces commandes explicites ne sont pas des outils du modele; elles
n'appellent ni application, ni navigateur, ni LLM, ni MCP. Elles restent possibles
quand l'apprentissage automatique est OFF.

Chaque edition remplace les champs relus dans une revision croissante. Le contenu
et son snapshot sont enregistres dans la meme transaction SQLite. Une revision
concurrente impose une actualisation, jamais un ecrasement silencieux. Une
restauration ajoute une revision, conserve l'activation et les compteurs actuels,
et ne reecrit pas l'historique. Les mises a jour du chemin d'apprentissage legacy
conservent leur fusion pour compatibilite mais ne reactivent pas un Skill desactive.

La migration ne garde que le snapshot legacy connu; les anciennes procedures
perdues ne sont pas inventees. Les 100 dernieres revisions sont consultables dans
le panneau; les plus anciennes restent stockees. Le reset operationnel explicite
efface aussi les revisions, jamais V5.

## Limites

La source historique `verified_agent`, la confiance, la restauration ou le numero
de version ne certifient pas une mission complete. La promotion par preuves de
mission independantes et les runs attaches a une revision restent a completer.
Une procedure reste une donnee de contexte, pas une permission ou un script
autonome. Les autorisations natives/MCP continuent de s'appliquer.

La redaction operationnelle existante masque certains identifiants courants,
pas tous les secrets possibles dans un texte libre. Ce stockage n'est pas un coffre.
Les benchmarks d'autonomie et la recette Windows commenceront OFF/OFF.

Tests : `tests/test_skill_lifecycle.py`, `tests/test_operational_preferences.py`.
Recette reelle encore attendue : changement de modes, redemarrage, non-reutilisation
d'un Skill desactive et absence d'apprentissage automatique en OFF/OFF.
