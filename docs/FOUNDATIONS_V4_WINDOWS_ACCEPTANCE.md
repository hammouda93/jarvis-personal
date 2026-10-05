# Tests Windows exacts — fondations V4

Base protégée : `500453228d9e00ae7a820cf60d0a1d73344c61ca`.
Les adapters sont désactivés par défaut. Les scénarios sont déclarés dans
`tests/acceptance_foundations_v4.json`. Un replay synthétique, une fixture OCR
ou une réponse affirmative du modèle ne valide aucun gate desktop.

## Préparation et suite automatisée

Depuis le dépôt dans PowerShell, utiliser un Python fonctionnel :

```powershell
$pythonExe = 'C:\chemin\vers\python.exe'
$nodeExe = 'C:\chemin\vers\node.exe'
& $pythonExe -m pip install -r requirements.txt -r requirements-grounding.txt
.\scripts\run_foundations_v4_validation.ps1 -PythonExe $pythonExe -NodeExe $nodeExe
& $pythonExe scripts\benchmark_grounding.py --runs 5 --output .cache\grounding-benchmark.json
```

Le `.venv` du poste audité pointe vers un Python absent. L'environnement isolé
préparé pendant cette reconstruction est utilisable directement :

```powershell
$pythonExe = 'C:\Users\salah\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$nodeExe = 'C:\Users\salah\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$env:PYTHONPATH = "$PWD\.cache\foundation-ocr-deps;$PWD\.cache\foundation-test-deps;$PWD\.cache\foundation-test-deps\win32;$PWD\.cache\foundation-test-deps\win32\lib"
.\scripts\run_foundations_v4_validation.ps1 -PythonExe $pythonExe -NodeExe $nodeExe
```

Le runner collecte les connexions SQLite inaccessibles avant de supprimer les
bases temporaires sous Windows/Python 3.12 : les context managers du checkpoint
committent mais ne ferment pas leurs connexions. Les requêtes/assertions restent
réelles. Le Memory Core ferme ses propres connexions via un adapter.
L'ancien test STT qui patchait un champ frozen remplace désormais la référence
de module par `dataclasses.replace`, avec les mêmes assertions.

## Memory Core : sous-processus et UI utilisateur

Chaque commande lance un nouveau processus, sans appel au LLM :

```powershell
$memoryDb = "$PWD\.cache\memory-acceptance.sqlite3"
& $pythonExe -m jarvis_agent.memory_core_cli --db $memoryDb 'Mémorise que mon film test est Arrival'
& $pythonExe -m jarvis_agent.memory_core_cli --db $memoryDb 'Quel est le nom de mon film ?'
& $pythonExe -m jarvis_agent.memory_core_cli --db $memoryDb 'Tu te souviens de mon film préféré ?'
& $pythonExe -m jarvis_agent.memory_core_cli --db $memoryDb "garde en tête que j'ai une réunion vendredi"
& $pythonExe -m jarvis_agent.memory_core_cli --db $memoryDb 'Quand est ma prochaine réunion ?'
& $pythonExe -m jarvis_agent.memory_core_cli --db $memoryDb 'Quel est mon restaurant préféré ?'
```

Attendus : `remember_information`, puis `recall_information` et Arrival ;
écriture de la réunion puis vendredi ; clarification pour restaurant.
Tester Inception dans une autre base neuve, puis deux paraphrases après restart.
Arrival ET Inception dans la même base provoquent une clarification du conflit.

Dans Jarvis V3 :

```powershell
$env:JARVIS_MEMORY_CORE_ENABLED = '1'
$env:JARVIS_BROWSER_CORE_ENABLED = '0'
$env:JARVIS_COMPUTER_CORE_ENABLED = '0'
& $pythonExe run_jarvis.py
```

Refaire les phrases, fermer complètement Jarvis, relancer et poser les questions.
Vérifier `[MEMORY_ROUTER] route=...` et les appels outils dans les traces.
Tester aussi `Ne mémorise pas mon film`, une traduction contenant « mémorise »
et `Comment ouvrir mon navigateur ?` : aucun write automatique. Un fait de
session doit gagner sur une donnée persistante. Les connecteurs sont consultés
après miss seulement et nécessitent un reader read-only explicitement enregistré
dans `memory_connectors.py`. Aucun compte distant n'est connecté ici.

## Browser Core : installer dans le vrai profil Chrome

1. Garder deux onglets témoins dans le Chrome habituel ; confirmer son profil
   connecté et ses sessions existantes.
2. `chrome://extensions` → mode développeur → Charger l'extension non empaquetée
   → dossier `extensions/personal-ai-browser-bridge`.
3. Copier l'ID de 32 lettres, puis enregistrer le host pour cet ID uniquement :

```powershell
.\scripts\install_browser_bridge.ps1 -ExtensionId 'ID_REEL_DE_CHROME' -PythonExe $pythonExe
$env:JARVIS_BROWSER_CORE_ENABLED = '1'
$env:JARVIS_BROWSER_ENABLED = '0'
$env:JARVIS_BROWSER_BRIDGE_CONFIG = "$env:LOCALAPPDATA\JarvisPersonal\browser-bridge\bridge.json"
```

4. Cliquer l'icône de l'extension pour connecter le host. Aucun profil distinct,
   flag remote-debugging, copie de profil ni Playwright n'est utilisé. Un autre
   profil nécessite son pairing et son port. Un refus debugger/DevTools provoque
   une erreur explicite, jamais un fallback clavier Windows.

Les clics du document principal utilisent des événements pointer CDP ciblés
sur l'onglet. Les clics de sous-frames utilisent `DOM.click` et renvoient
`trusted:false` ; certaines interfaces peuvent refuser ces événements.
La saisie de valeur utilise le setter DOM et ses événements input/change :
les éditeurs custom doivent passer leur gate réel. Les pages internes et les
shadow roots fermés ne sont pas exposés par cette version.

Lancer la console ; chaque ligne est une commande JSON explicitement exécutée :

```powershell
& $pythonExe -m jarvis_agent.foundation_live_cli --record .cache\live-browser-v4.jsonl
```

Remplacer `TAB_ID` par l'entier renvoyé et `REF` par une ref de l'observation :

```json
{"tool":"browser_list_tabs","arguments":{}}
{"tool":"browser_get_active_tab","arguments":{}}
{"tool":"browser_navigate","arguments":{"url":"https://www.youtube.com/"}}
{"tool":"browser_observe_dom","arguments":{"tab_id":TAB_ID}}
{"tool":"browser_write","arguments":{"tab_id":TAB_ID,"ref":"REF_RECHERCHE","text":"Messi"}}
{"tool":"browser_observe_dom","arguments":{"tab_id":TAB_ID}}
{"tool":"browser_press","arguments":{"tab_id":TAB_ID,"ref":"REF_RECHERCHE_FRAICHE","key":"Enter"}}
{"tool":"browser_verify","arguments":{"tab_id":TAB_ID,"text":"Messi"}}
{"tool":"browser_observe_dom","arguments":{"tab_id":TAB_ID}}
{"tool":"browser_click","arguments":{"tab_id":TAB_ID,"ref":"REF_PREMIERE_VIDEO_OBSERVEE"}}
{"tool":"browser_verify","arguments":{"tab_id":TAB_ID,"url":"URL_EXACTE_VIDEO"}}
{"tool":"browser_back","arguments":{"tab_id":TAB_ID}}
{"tool":"browser_verify","arguments":{"tab_id":TAB_ID,"text":"Messi"}}
{"tool":"browser_close_tab","arguments":{"tab_id":TAB_ID}}
{"tool":"browser_list_tabs","arguments":{}}
```

Attendre la page prête avant de réobserver. Réobserver après chaque action ;
les refs expirent. `dispatched:true, verified:false` demande une preuve, pas une
répétition. Choisir la première vidéo réelle parmi les résultats observés,
sans compter publicité/navigation/menu. Les consentements éventuels sont
traités par les mêmes primitives. Contrôler visuellement la lecture et le retour.
Un résultat `outcome_unknown` bloque une nouvelle mutation du même scope jusqu'à
une vérification explicite. Une création d'onglet dont l'ID est perdu exige une
inspection manuelle et un reset conscient ; aucune création n'est rejouée seule.

Refaire le même parcours sur Google (`interface accessibility`) et Wikipedia
(`Arrival (film)`), en ajoutant `browser_forward` après back. Tester
`browser_find` avec un texte/type réellement observé. Tester un download public
bénin puis `browser_verify` avec son `download_id` : démarrage ≠ terminé.

Gate focus : observer un champ browser, placer le caret dans Notepad, exécuter
`browser_write` avec la ref browser et vérifier sa valeur. Notepad doit rester
strictement inchangé. Tester plusieurs onglets et deux fenêtres Chrome,
`browser_activate_tab`, puis fermer uniquement l'onglet test. Comparer les IDs
des onglets témoins avant/après. Refaire ces objectifs en langage naturel dans
Jarvis V3, pas seulement via console.

## Generic Computer Grounding et communication

```powershell
$env:JARVIS_COMPUTER_CORE_ENABLED = '1'
& $pythonExe -m jarvis_agent.foundation_live_cli --record .cache\live-computer-v4.jsonl
```

Entrer `windows`, relever le handle réel et mettre la cible au premier plan :

```json
{"tool":"computer_observe","arguments":{"window_id":"HWND_REEL"}}
{"tool":"computer_shortcut","arguments":{"window_id":"HWND_REEL","key":"Alt+K"}}
{"tool":"computer_observe","arguments":{"window_id":"HWND_REEL"}}
{"tool":"computer_find","arguments":{"type":"edit"}}
```

Dans WhatsApp : vérifier la recherche ouverte ; écrire le nom dans un champ
éditable observé ; réobserver ; identifier le résultat exact, sans confondre
un préfixe avec un autre contact. Le paramètre demandé est Hamza Mathlouthi.
Les modules runtime ne contiennent ni ce nom ni un workflow WhatsApp.

```json
{"tool":"computer_write","arguments":{"ref":"REF_RECHERCHE","text":"CONTACT_EXACT"}}
{"tool":"computer_find","arguments":{"text":"CONTACT_EXACT","exact":true}}
{"tool":"computer_click","arguments":{"ref":"REF_RESULTAT_EXACT"}}
{"tool":"computer_observe","arguments":{"window_id":"HWND_REEL"}}
{"tool":"computer_verify","arguments":{"window_id":"HWND_REEL","condition":{"text":"CONTACT_EXACT","region":"header"}}}
{"tool":"computer_find","arguments":{"type":"edit"}}
{"tool":"computer_write","arguments":{"ref":"REF_COMPOSER_PROUVE","text":"MESSAGE_TEST_AUTORISE","context":{"text":"CONTACT_EXACT","region":"header"}}}
{"tool":"computer_verify","arguments":{"window_id":"HWND_REEL","condition":{"value":"MESSAGE_TEST_AUTORISE","type":"edit"}}}
{"tool":"computer_find","arguments":{"type":"edit"}}
{"tool":"computer_press","arguments":{"ref":"REF_COMPOSER_FRAICHE","key":"Enter","context":{"text":"CONTACT_EXACT","region":"header"}}}
```

Le rôle/type exact du composer doit provenir de l'observation (`edit` pour UIA,
`textbox` pour le modèle). Le header doit être prouvé dans la région de la
conversation ; le nom dans la liste de contacts ne suffit pas. Si le provider
ne peut pas établir `region=header`, le gate échoue et l'envoi reste interdit
par ce scénario. Après envoi, vérifier le message **sortant** dans la conversation
et l'absence du brouillon ; le texte uniquement dans le composer ne prouve rien.
La console enregistre captures et JSONL. Refaire avec un deuxième contact choisi
par l'utilisateur et un message distinct. Les refs desktop expirent après 5 s ;
réobserver si la préparation manuelle a pris plus longtemps.

Un label OCR reste `type=text`, sans droit d'écriture. Un composer complètement
opaque nécessite un modèle local qui prouve type/bbox/focus ; sans preuve le
moteur refuse l'écriture. Le modèle est optionnel et doit déjà être installé :

```powershell
$env:JARVIS_GROUNDING_MODEL = 'MODELE_LOCAL_QUALIFIE'
$env:JARVIS_GROUNDING_ENDPOINT = 'http://127.0.0.1:11434/api/chat'
$env:JARVIS_GROUNDING_BUDGET_S = '3'
```

Le modèle Gemma installé a dépassé le budget de 2 s sur fixture pendant l'audit :
il n'est pas qualifié. OmniParser/UI-TARS ont été étudiés mais leurs poids et
adapters spécifiques ne sont pas intégrés. OCR natif est le chemin texte rapide.
Tester une autre application, fenêtre déplacée et cible occluse : anciennes
refs refusées avant action. Mesurer p50/p95 UIA+OCR sur captures réelles ; le
benchmark fixture ne prouve ni les icônes ni les types fonctionnels d'un custom UI.

## Non-régression installer et promotion

Avec les trois flags à `0`, rejouer le même EXE Cursor validé : ouvrir → inspecter
licence → Accepter → Suivant → continuer. Refaire avec les adapters activés en
gardant les primitives historiques pour l'installer. Tester aussi Chrome →
installer pour vérifier le changement de scope. Contrôler chaque fenêtre réelle.

```powershell
git diff --exit-code 500453228d9e00ae7a820cf60d0a1d73344c61ca -- jarvis_agent/windows_perception.py jarvis_agent/tools.py jarvis_agent/screen_vision.py jarvis_agent/mission.py jarvis_agent/native_tools.py jarvis_agent/browser_adapter.py jarvis_agent/memory.py
git show-ref --verify refs/remotes/origin/checkpoint/runtime-foundation-v2-tested
```

La ref doit toujours afficher `500453228d9e00ae7a820cf60d0a1d73344c61ca`.
Les gates Chrome, WhatsApp et installer restent **non validés** sans leurs preuves
desktop réelles. Ne pas activer les flags par défaut ni fusionner sur ce seul
résultat automatisé.
