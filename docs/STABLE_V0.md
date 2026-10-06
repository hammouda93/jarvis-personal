# StableV0 — Personal AI Agent

Date de validation live : 2026-10-06

Checkpoint code source :
- branche d'origine : `feature/semantic-memory-v5-agentic-reasoning-v1`
- SHA de base validé : `4ca8648b3089f6129ea6789e563f78ef6e10fffe`
- branche checkpoint : `checkpoint/StableV0`

## Principe architectural validé

Le code fournit des capacités génériques, des données, des permissions, de la provenance,
de la vérification et du recovery. Cerebras reste responsable de la compréhension,
du choix des outils et du raisonnement sur les données. Éviter les règles métier
spécifiques quand le modèle peut raisonner à partir de preuves fiables.

## Capacités validées dans StableV0

### Runtime / conversation
- Cerebras principal avec fallback existant.
- Conversation texte opérationnelle.
- Passage en mode texte => microphone coupé.
- Réponse texte + TTS conservée.
- Les confirmations/félicitations ne relancent pas une ancienne mission.

### Semantic Memory V5 agentique
- écriture persistante uniquement sur demande explicite ;
- recall sémantique ;
- lecture de preuves brutes et métadonnées ;
- `semantic_memory_search` read-only accessible au cerveau ;
- second passage de recherche possible si la première projection ne suffit pas ;
- raisonnement sur les preuves plutôt que réponse rigide relation -> value ;
- exemples live validés : réunion du 08/10/2026, films Inception/Avatar,
  projets Atlas/Neptune/Polaris 418, module de paiement d'Atlas,
  objectif 2 h/jour R&D, inspection brute de la mémoire.

### Windows / fichiers / applications
Environnement live Computer Core validé avec Python 3.12.10 :
`C:\jv312\Scripts\python.exe`

Preflight validé :
- pywinauto ;
- pywin32 / COM ;
- Windows Media OCR ;
- Windows Graphics Imaging ;
- Windows Storage Streams ;
- absence de contamination PYTHONPATH ;
- Computer Core activé.

Parcours live validés :
- ouverture d'un fichier Excel dans Téléchargements ;
- fermeture vérifiée du fichier/fenêtre Excel ;
- ouverture générique de VLC Media Player ;
- ouverture générique d'Adobe Premiere Pro ;
- fermeture vérifiée d'Adobe Premiere Pro ;
- ouverture de l'installateur Cursor ;
- observation UIA réelle de la fenêtre Cursor ;
- acceptation de licence par ref observée ;
- navigation par plusieurs boutons `Suivant` avec refs fraîches et
  `post_observation` après mutation ;
- détection d'une boîte de dialogue bloquant la fermeture ;
- recovery par inspection de la boîte de dialogue puis action sur le bouton observé.

## Protections / non-régression

Ne pas modifier les primitives historiques validées sans preuve d'une régression :
- `jarvis_agent/windows_perception.py`
- `jarvis_agent/tools.py`
- `jarvis_agent/screen_vision.py`
- `jarvis_agent/mission.py`
- `jarvis_agent/native_tools.py`
- `jarvis_agent/browser_adapter.py`
- `jarvis_agent/memory.py`

Memory V5 validée ne doit pas être refactorée pendant les phases Browser/Computer
sauf preuve directe qu'elle cause le défaut étudié.

## Limites connues à ne pas confondre avec des validations

### Installer Cursor
Le moteur a correctement manipulé les étapes observées, mais une réponse du modèle
a annoncé l'installation comme terminée avant preuve de fin réelle. Une tentative de
fermeture a ensuite affiché `Quitter l'installation` avec le message indiquant que
l'installation n'était pas terminée. La prochaine amélioration doit donc porter sur
la preuve de postcondition finale, pas sur des règles spécifiques à Cursor.

### Memory V5
La mémoire répond correctement dans plusieurs cas grâce au chemin agentique/raw
fallback, mais les projections structurées peuvent encore être imparfaites :
- certaines requêtes datées ou numériques passent par le raw fallback ;
- certaines relations peuvent être mal alignées avant récupération par le cerveau ;
- une réponse détaillée sur Atlas a extrapolé des éléments non présents dans la
  mémoire brute. Le grounding de réponse devra donc être renforcé plus tard sans
  remettre en cause l'architecture agentique.

### Browser
Browser Core n'est pas encore validé live dans StableV0. Il reste la prochaine phase.

## Séquence de développement après StableV0

1. Browser Control live / non-régression navigateur.
2. Validation YouTube : ouvrir, rechercher Messi, ouvrir le premier résultat observé,
   retour, fermer uniquement l'onglet.
3. Validation Google / Wikipedia / multi-tabs / multi-windows / isolation du focus.
4. Validation du changement de scope Browser <-> Windows.
5. Renforcement générique des preuves de fin de mission / postconditions.
6. Seulement après ces gates : étapes architecturales suivantes.

## Règle de promotion

Une phase n'est promue que si :
- tests automatiques concernés verts ;
- test live réel réussi ;
- logs inspectés ;
- aucune fonctionnalité déjà validée n'est régressée ;
- aucune correction spécifique à une application n'est introduite si une correction
  générique du moteur suffit.
