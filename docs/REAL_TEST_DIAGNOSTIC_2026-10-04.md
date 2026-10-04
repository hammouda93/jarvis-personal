# Diagnostic du premier essai Windows — 4 octobre 2026

Version observée : `0e961d2`, branche `feature/computer-use-fusion-v2`.
Cette analyse repose sur le log fourni par l'utilisateur, pas sur une exécution de son bureau
par le développeur. Le log personnel n'est pas copié dans le repository.

## Résultats réellement observés

| Essai | Constat | Verdict |
|---|---|---|
| Heure | Réponse via le fast path `system.time` | Fonctionnement observé ; comparer à l'horloge reste un contrôle utilisateur |
| Première salutation écrite | Une ancienne demande vocale de nom de dossier consomme la salutation et lance une ouverture | Défaut de canal/contexte, pas de perception Windows |
| Ouverture de Bloc-notes | Lancement puis observation de la bonne fenêtre avec HWND/PID/process-start et refs opaques | Chemin structuré exploitable |
| Écriture Unicode | Valeur relue exactement et `NATIVE_POSTCONDITION_VERIFIED`, environ 0,46 s | Primitive fiable conservée |
| Fin de la mission ouverture + écriture | `goal=[]` ; le runtime refuse la complétion malgré la preuve d'écriture | Garde contre le faux succès utile ; protocole planner/controller incomplet |
| Remplacement du document | Relecture native exacte, environ 0,52 s ; réponse finale correcte | Écriture fiable, inspections supplémentaires évitables |
| Lecture du document | Structure lisible, mais demande de fallback visuel et mission finale non vérifiée | Ancien signal de couverture priorisé à tort ; objectif non défini |
| « Très bien, merci » | Réponse conversationnelle sans mutation | Comportement correct dans cet essai ; désormais sans appel au planner pour cette formule |
| Copie entre documents | `open_file` dans `Documents` avant inventaire des fenêtres, puis échec | Stratégie insuffisamment fondée ; le log ne prouve pas quels documents étaient ouverts |
| Cerebras | Plusieurs passages vers la clé secondaire | Fallback utilisé ; cause exacte non identifiable dans l'ancien log |
| ElevenLabs | Passage à la voix Windows | Résilience présente ; cause exacte non identifiable dans l'ancien log |
| Mode texte | Recalibration, claps et STT continuent entre les messages ; réponses texte silencieuses | Ne correspond pas au mode demandé |

Le log contient des transcriptions et langues détectées incohérentes avec une session de test
texte. Il ne permet pas de distinguer audio ambiant, faux réveil, mauvaise capture ou erreur STT.
Il ne contient pas d'essai WhatsApp/canvas/VLM ni de preuve d'une mission multi-fenêtres réussie.
Le résultat des tests automatisés Windows n'apparaît pas dans cet extrait.

La correction passe 456 tests locaux, dont 27 nouvelles régressions : vrai bouton Qt,
fermeture d'une capture simulée, STT périmé, changement rapide de mode, suivi par canal,
reprise Cerebras simulée après goal manquant, relecture native, feedback JSON et diagnostics API.
Chromium réel reste couvert par les fixtures indépendantes existantes. Cela ne remplace pas
l'essai d'un microphone Windows, du vrai Cerebras et du modèle de vision sur la machine cible.

## Corrections génériques

1. Le bouton texte modifie réellement l'admission audio. Le worker ferme sa capture par
   sortie du context manager sounddevice, puis attend des messages sans rouvrir le micro.
   Une génération d'entrée invalide les anciens claps, captures et résultats STT.
2. Une entrée texte reçoit une réponse affichée et vocale. La provenance reste `text` dans
   le journal ; activer la sortie audio ne réactive pas l'entrée micro.
3. Une demande directe de précision est limitée à son canal/génération. Une salutation ou
   nouvelle commande la remplace. Décrire une possibilité d'action ne suffit plus au fast path.
4. Une mission composée ne peut plus muter le contenu avant définition de tous ses résultats.
   La reprise d'une tentative refusée pour ce prérequis est autorisée après définition du goal.
   Une opération native simple garde sa relecture exacte ; un goal de valeur figé peut réutiliser
   la même preuve. Aucun goal plus faible n'est inventé après l'écriture.
5. Le runtime peut demander au planner de réparer un goal absent en deux continuations bornées,
   sans remettre à zéro l'état de stagnation et sans retransmettre de chaîne de pensée.
6. Le signal de couverture fusionné du Perception Manager prime sur l'ancien conseil visuel
   lié à un arbre partiel. Une cible manquante peut toujours déclencher une escalade ciblée.
7. Les logs conservent diagnostic original d'un outil non JSON, message d'échec, état de fraîcheur,
   statut de perception et causes HTTP des fallbacks. Le feedback des écritures natives reste JSON
   valide même lorsqu'il dépasse la limite historique de 3 500 caractères.

## Rejouer sous Windows

```powershell
git fetch origin
git switch feature/computer-use-fusion-v2
git pull --ff-only origin feature/computer-use-fusion-v2
.\scripts\run_computer_use_validation.ps1
.\scripts\run_jarvis_computer_use.ps1 -EnableVisualActions -LogPath "jarvis_tests_text_mode_fix.log"
```

Sélectionner Conversation texte et vérifier le marqueur `[INPUT_MODE] text microphone=off`.
Pendant 20 secondes, parler ou faire un double clap : aucun réveil, enregistrement ou STT ne
doit suivre ce marqueur tant que le mode reste texte. Si une transcription était déjà en cours
au moment du clic, son résultat doit être ignoré et ne produire aucune action.

Envoyer séparément :

1. « Bonjour Jarvis. » — réponse écrite et vocale, aucune ouverture de dossier.
2. « Quelle heure est-il ? » — réponse écrite et vocale.
3. « Ouvre Bloc-notes et écris exactement : Test Unicode éàç — مرحبا. Vérifie le texte. »
   — écriture native, objectif défini avant mutation, preuve de complétion.
4. « Dans ce document, remplace tout par : Test V2 terminé. Vérifie. »
   — relecture exacte, sans vision si le contrôle et sa valeur sont exposés.
5. « Relis le contenu du document ouvert. » — contenu réel, objectif de lecture vérifié.
6. « Très bien, merci. » — réponse seule, aucun nouvel outil/planner.
7. « Revenir à la voix » via le bouton — le double clap fonctionne à nouveau sans effacer
   le contexte. Réactiver ensuite le texte et refaire la salutation.

Pour la copie, préparer deux fichiers de test dans deux fenêtres séparées. Écrire une référence
unique dans la source et laisser la destination vide. Demander de copier et vérifier les deux :
le planner doit inventorier les fenêtres/onglets et lier les identités existantes avant le goal.
Il ne doit pas supposer qu'un document visible se trouve dans `Documents`.
Ce choix autonome reste à vérifier avec le vrai Cerebras ; les tests de code ne le garantissent pas.

## Sources examinées avant les modifications

- Qt, affinité de thread et livraison des signaux :
  https://doc.qt.io/qtforpython-6/PySide6/QtCore/QObject.html#thread-affinity
- sounddevice, streams et fermeture à la sortie d'un context manager :
  https://python-sounddevice.readthedocs.io/en/0.5.3/api/streams.html
- Python 3.9, `threading.Event` : https://docs.python.org/3.9/library/threading.html#event-objects
- Code OpenClaw, annulation des anciennes transitions et suspension du wake audio :
  https://github.com/openclaw/openclaw/blob/main/apps/macos/Sources/OpenClaw/TalkModeController.swift
- Code/documentation du SDK Cerebras, types d'erreur et `status_code` :
  https://github.com/Cerebras/cerebras-cloud-sdk-python#handling-errors

Les adaptations restent dans le worker et le moteur existants. Aucun raccourci de Bloc-notes,
WhatsApp ou site web ne remplace la découverte des contrôles.
