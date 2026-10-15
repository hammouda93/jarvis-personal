# V12 — Continuité des missions et fiabilité MCP

Date : 2026-10-15. Base vérifiée : `53f7999`, dernière tête de branche au début
de la reprise. Travail cumulatif sur `feature/autonomous-mission-reliability-v12`.
`main` et `feature/native-agent-loop-v2` ne sont pas modifiées.

## Comportements corrigés

| Déclencheur | Comportement V12 |
| --- | --- |
| Timeout / exception après dispatch MCP | Suspend immédiatement le tour et le reste du lot ; explique la revue nécessaire sans demander une nouvelle réponse au modèle |
| Verrou d'une ancienne tentative | Le reçu atomique contient l'identifiant exact, même au-delà des vingt entrées affichées par le journal ; aucune remise à zéro du verrou |
| Cooldown / quota MCP | Motif et délai local précis ; aucun sommeil bloquant ni nouvel appel automatique |
| Approbation au milieu d'un lot | Conserve les réponses déjà obtenues ; répond aux autres IDs comme différés, puis fait replanifier les actions restantes |
| Refus explicite de l'utilisateur | Reçu local `dispatched=false`, sans inventer d'effet incertain |
| Tour suivant après une lecture | Conserve les groupes d'appels/réponses utiles dans leur rôle outil, au lieu d'effacer toutes les sources |
| Historique trop long | Garde au maximum huit groupes d'outils complets dans la fenêtre de conversation ; ne crée pas de réponses orphelines |
| Réponse MCP volumineuse | Signale la troncature ; contexte local relisible par plages, avec provenance externe non vérifiée |
| Répétition d'une sélection déjà visible | Indique les outils déjà exposés ; suspend après deux sélections sans information nouvelle |
| Schéma sans mot-clé `required` | Traite correctement la lecture d'identité comme sans paramètre obligatoire pour le classement initial |
| Outils MCP avec points/tirets | Associe correctement les alias du modèle aux noms natifs dans les reçus et rapports |

Le chemin principal Cerebras/Groq et les cerveaux supplémentaires compatibles
Chat Completions utilisent le même runtime. Les chemins Responses et Ollama
sont également testés hors ligne pour les suspensions de lots ; aucun modèle
Ollama n'a été lancé ou activé.

## Sources, mémoire et confidentialité

L'archive de réponses externes est uniquement en mémoire du processus : au plus
16 entrées et 262 144 caractères au total, 131 072 par entrée. Elle est vidée lors
d'une réinitialisation de conversation et ne modifie pas SQLite V5 ou les Skills.
Le contexte efficient doit être actif pour que la relecture soit proposée.

Le transport conserve jusqu'à 65 536 caractères de texte et autant de données
structurées par réponse. Les dépassements et contenus non représentés sont
signalés. Une pagination de service reste une pagination de service : même une
réponse entièrement conservée ne prouve jamais qu'un inventaire est exhaustif.
L'archive peut être évincée ; Jarvis doit signaler son absence au lieu d'inventer
le passage ou de rejouer automatiquement un effet externe.

Le journal de mission durable conserve seulement des reçus locaux et les motifs
de pause. Ni arguments, réponses brutes, identité de compte ni contenu de
l'archive externe n'y sont ajoutés. Les journaux diagnostiques du runtime ne
copient plus le contenu ou les paramètres MCP.

## Validation

Les cinq nouvelles suites ajoutent 28 tests couvrant les suspensions de lots, l'association des
appels/réponses après confirmation, les lectures volumineuses en français/arabe,
les limites d'archive, l'historique de conversation et les budgets de sélection.

```text
python scripts/run_final_regression.py
node --test tests/browser_bridge_replay.mjs
```

Résultat local Linux : 1 343 tests examinés, 1 335 réussis, huit contrôles Windows
explicitement non exécutés, aucun échec ou erreur. Les 46 replays du navigateur
réussissent également ; compilation Python vérifiée. La CI Windows/Linux doit
encore être observée sur le commit publié. Les tests sont synthétiques et isolés ; aucun compte personnel,
message, installation ou mission réelle Windows n'a été exécuté pendant cette
reprise.

La revue finale a également reproduit puis corrigé la copie de contenu MCP dans
les traces structurées optionnelles. Ces événements ne conservent désormais que
les métadonnées locales de dispatch, de provenance, de tentative et de pause.
La réponse complète reste disponible pour le modèle et l'utilisateur ; seule
sa copie automatique dans les événements techniques est supprimée.

## Recette réelle restant à effectuer

1. Démarrage avec les réglages personnels existants, texte et voix ; microphone
   arrêté en mode texte et voix de réponse disponible.
2. Mémoire explicitement enregistrée puis retrouvée après redémarrage ; aucune
   écriture personnelle déclenchée par une simple consultation.
3. Chrome habituel : recherche, résultat réellement observé, retour et fermeture
   du seul onglet demandé ; conservation des autres onglets.
4. Windows : nouveau document Unicode, lecture de son contenu, découverte et
   inspection de l'installateur existant sans début d'installation.
5. MCP connecté : identité observée, inventaires demandés et réponse utile,
   avec reprise après erreur connue et suspension visible après incertitude.
6. Mission composée navigateur/Windows/MCP, cascade de cerveaux et interruption
   puis reprise avec des observations fraîches.

Le candidat améliore l'autonomie de l'exécuteur existant. Il ne constitue pas
une certification de toutes les fonctions envisagées : agents cloud permanents,
missions multi-services arbitraires et succès métier de bout en bout nécessitent
encore leur implémentation complète et des preuves réelles. Les accès aux comptes
et les autorisations de l'opérateur restent nécessaires.
