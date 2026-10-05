# Audit architectural — 5 octobre 2026

## Périmètre et provenance

Base unique : `500453228d9e00ae7a820cf60d0a1d73344c61ca`,
`origin/checkpoint/runtime-foundation-v2-tested`.
Branche de travail : `codex/personal-agent-foundations-v4`.
Les 859 insertions / 19 suppressions des expériences gates-v3 ne sont pas
importées ni présumées validées. Les logs locaux non suivis sont conservés.
L'inventaire annexe couvre chaque fichier suivi, les symboles Python et les
dépendances internes. Les flux ci-dessous ont été lus et confrontés aux tests.

## Diagnostic racine

- **Memory** : `assistant_v3._process_user_text` → `build_agent_runtime` →
  boucle du provider → `NativeToolRegistry.execute` → `LOCAL_MEMORY`.
  Le runtime vérifie l'autorisation d'un appel choisi par le modèle, mais
  n'impose ni appel d'écriture ni récupération. Le préfiltre omet « en tête ».
  SQLite persiste correctement ; sa recherche LIKE/AND ignore mal les mots
  relationnels (« nom »), accents, fautes et paraphrases. Les tests de mémoire
  interrogent le store directement, pas le routage après restart de processus.
- **Browser** : outils DOM exposés uniquement si CDP activé et URL configurée.
  `BrowserAdapter` est lié à Playwright et un endpoint déjà ouvert.
  `tools._launch_chrome` peut créer un user-data-dir distinct ; le chemin
  historique omnibox utilise le clavier Windows. Ce dernier dépend du focus
  et ne fournit pas une garantie d'isolation des écritures par onglet.
- **Computer** : UIA/CUA produit des refs de snapshot et des contrôles ; la
  couverture insuffisante déclenche la génération Ollama `observe_screen`.
  La génération multimodale (~20 s dans les essais utilisateur) n'est pas un
  parseur local rapide. Une fenêtre native trouvée ne prouve pas que ses
  contrôles custom sont accessibles. Une action exécutée ne prouve pas son
  résultat ; il faut des postconditions précises.
- **Kernel/routing** : capability registry, context injector, scoped knowledge,
  scheduler, gateways et agent factory sont surtout passifs/shadow. Ils ne
  contrôlent pas le chemin live par simple présence. LocalMemory utilisateur,
  connaissance opérationnelle et mémoire de mission sont trois stores séparés.
- **Non-régression** : les modes V1/V2/headless et le fast path V3 coexistent.
  Les installers utilisent les primitives génériques historiques UIA ; aucun
  remplacement de `windows_perception`, `screen_vision`, `tools`, `mission`
  ou du parcours licence/Accepter/Suivant n'est nécessaire.

## Recherche officielle et code étudié

| Source primaire | Constat / décision |
| --- | --- |
| [Chrome 136](https://developer.chrome.com/blog/remote-debugging-port) | Les flags port/pipe ignorent le dossier par défaut. Déplacer/copier le profil ne satisfait pas le produit demandé. |
| [AutoConnect](https://developer.chrome.com/docs/devtools/agents/use-cases/auto-connect), [MCP code](https://github.com/ChromeDevTools/chrome-devtools-mcp) | Chrome 144+, activation dans chrome://inspect/#remote-debugging, autorisation de session dans Chrome. A reste une alternative utilisable pour le vrai profil. |
| [tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs), [scripting](https://developer.chrome.com/docs/extensions/reference/api/scripting) | Identité tabId/windowId ; injection en monde isolé, documentId/frameId ; restrictions sur pages internes. |
| [debugger](https://developer.chrome.com/docs/extensions/reference/api/debugger) | Transport CDP ciblé tabId ; Input permet des touches fiables sans SendInput Windows ; attachement visible, conflits DevTools possibles. |
| [Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) | Extension initie la connexion ; framing JSON + uint32 natif ; liste d'origines, plafond host→Chrome 1 Mo. Pas de forwarding de commandes de page. |
| [Playwright/CDP](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp) | Chromium seulement ; fidélité inférieure au protocole Playwright ; ne résout pas l'accès au profil par défaut à lui seul. |
| [UFO²](https://arxiv.org/abs/2504.14603), [inspector.py](https://github.com/microsoft/UFO/blob/main/ufo/automator/ui_control/inspector.py), [screenshot.py](https://github.com/microsoft/UFO/blob/main/ufo/automator/ui_control/screenshot.py) | Stratégies backend, HWND natif puis UIA, cache de propriétés, fusion de boxes par IoU. Le repo main contient aussi UFO³ ; l'étude cible les composants UFO². |
| [Agent-S/S3 grounding.py](https://github.com/simular-ai/Agent-S/blob/main/gui_agents/s3/agents/grounding.py) | OCR mots+bbox et modèle de grounding séparé du planner. Ce code utilise pytesseract et un modèle pour produire un point ; pas de garantie de latence locale. |
| [OpenInterpreter](https://github.com/openinterpreter/openinterpreter) | La version actuelle a évolué : compétence QA, agent-browser/trycua. Les anciens chemins Python computer/mouse/display ne sont plus disponibles sur main ; ne pas prétendre avoir étudié ces fichiers obsolètes. |
| [pywinauto](https://pywinauto.readthedocs.io/en/latest/getting_started.html), [Microsoft custom provider](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/WinAuto/uiauto-serversideprovider.md) | UIA dépend du provider custom pour exposer certaines propriétés/patterns ; structure native minimale insuffisante. |
| [Windows OCR](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine?view=winrt-26100) | OCR local natif, boxes mots/lignes ; pas de confiance OCR native, pas de type fonctionnel ni de détection d'icônes. |
| [OmniParser](https://github.com/microsoft/OmniParser), [UI-TARS](https://github.com/bytedance/UI-TARS) | Détection visuelle complémentaire pour icônes/custom ; GPU/modèle et latence à mesurer, pas de téléchargement/activation automatique de poids. |

## Comparaison A / B

| Dimension | A : DevTools MCP AutoConnect | B : MV3 + Native Messaging |
| --- | --- | --- |
| Profil utilisateur | Oui, Chrome récent et autorisation | Oui, extension chargée dans ce profil |
| Mise en place | Node/MCP + opt-in Chrome | Extension + host HKCU + pairing local |
| Primitives | Adapter des outils du MCP | Contrat détenu par Jarvis |
| Focus Windows | Actions CDP ciblées | Actions DOM/Input CDP ciblées tabId |
| Cycle de vie | Connexion/session MCP | Reconnexion explicite ; aucun replay d'action inconnue |
| Limites | Version, autorisation, outil externe | Pages internes, frames sans permission, conflits debugger, installation |

B retenu. A étudié, pas installé ni présenté comme backend implémenté.

Le [launcher Windows Chromium](https://chromium.googlesource.com/chromium/src/+/main/chrome/browser/extensions/api/messaging/launch_context_win.cc)
conserve cmd.exe pour les hosts non-exe. Le script prépare un launcher UTF-8,
avec codepage explicite, et le host force stdin/stdout en mode binaire comme
recommandé par la documentation Native Messaging. Cet enregistrement HKCU
n'a pas été exécuté sur le profil utilisateur pendant les tests automatisés.

## Architecture proposée avant codage

- Adapter runtime mémoire : écritures explicites exécutées avant le LLM ;
  questions personnelles → faits utilisateur de session → SQLite → resolver
  connecteurs explicitement enregistré en lecture seule → clarification.
  Pas de mémorisation automatique du DOM ni de réponses inventées du modèle.
- Adapter registre : nouveaux outils `browser_*` et `computer_*` ; activation
  opt-in seulement. Les primitives historiques et installers gardent leur code.
- Browser Core : RPC locale authentifiée vers le host lancé par Chrome ;
  extension MV3 → tabs/scripting/debugger ; aucune injection clavier OS.
  Refs opaques liées à document et observation ; invalidation à chaque action.
- Computer Grounding : UIA → OCR Windows local → modèle local facultatif pour
  contrôles sans texte ; résultats typés/bbox/confiance/ref ; limites de temps
  par processus, identité HWND/PID, fraîcheur, préconditions et postconditions.
  OCR seul ne prouve jamais qu'un texte est un composer.
- Tests : suites historiques ; subprocess SQLite/restart réels ; replay de
  protocole DOM/grounding ; fixtures adversariales ; runner live distinct.
  Aucune promotion desktop basée uniquement sur mocks ou replay synthétique.

Fichiers de raccordement : `agent_runtime.py` (factory uniquement),
`foundation_tools.py` (proxy opt-in). Nouveaux modules, extension, tests,
scripts et documentation ; aucun patch spécifique à une application.

La revue du raccordement a nécessité deux extensions du contrat de preuve dans
`agent_runtime.py` : reconnaissance des noms `browser_*`/`computer_*`, et preuve
après la dernière mutation. Ces branches ne s'appliquent qu'aux nouveaux outils
exposés opt-in ; les capacités historiques gardent leur logique. Le fast path
V3 browser est contourné uniquement quand Browser Core est activé, pour éviter
la saisie OS. Les quatre fichiers de primitives installer sont identiques au SHA.

Modules supplémentaires : `memory_core_store.py` ferme explicitement les
connexions de l'adapter sans migration de schema ; `grounding_uia_worker.py`
isole l'inspection UIA en processus read-only avec délai dur ; `memory_connectors.py`
autorise uniquement des readers enregistrés par du code de confiance.

## Mesures de grounding sur ce poste

- OCR par PowerShell : dépasse 2 s à froid sur PNG fixture ; conservé comme
  compatibilité explicite, pas promu comme moteur rapide.
- [PyWinRT](https://github.com/pywinrt/pywinrt) 3.2.1 + Windows.Media.Ocr natif :
  cinq appels réels sur PNG fixture, 0,262–0,321 s incluant le sous-processus.
  Labels et bbox réellement reconnus. La confiance 0,75 est une heuristique
  déclarée, pas une probabilité native retournée par Windows.
- Ollama local `gemma3:latest` déjà installé : dépasse le budget de 2 s sur une
  fixture simple (~2,35 s jusqu'à l'erreur). Aucun poids téléchargé ; ce modèle
  n'est pas qualifié pour l'interaction custom. Le texte OCR seul n'identifie
  jamais un composer. L'adapter modèle exige une sortie structurée et une
  confirmation indépendante de focus avant une écriture visuelle.
- Ce benchmark ne mesure pas encore les captures desktop/UIA complètes,
  l'exactitude d'un header, les icônes, ni l'envoi d'un message réel.

## État initial des outils de test

Le `.venv` pointe sur un Python 3.9 disparu et `python` n'est pas dans PATH.
Le Python 3.12 fourni par Codex est disponible, mais ses dépendances Jarvis
manquent. Le premier discover a donc échoué sur imports/stockage hors sandbox,
avant toute modification du code. Dépendances isolées dans `.cache`, données
de test redirigées dans le workspace ; ce défaut d'environnement n'est pas
compté comme régression du checkpoint.

## Limites à qualifier avant promotion

Le contrat Memory est déterministe et testé avec vrais processus/SQLite ; le
lexique fuzzy reste volontairement conservateur. Des paraphrases hors lexique
peuvent clarifier au lieu de retrouver ; aucun index d'embeddings n'est intégré.
Les connecteurs fournissent un point d'intégration read-only, sans compte branché.

Les clics du document browser principal passent par CDP Input. Les sous-frames
gardent DOM.click avec `trusted:false`. Les writes DOM déclenchent input/change
mais leur acceptation dans chaque éditeur custom reste un gate live. L'arbre
observé est borné et ne prouve jamais une absence globale. Le transport consomme
les refs avant l'envoi et bloque une répétition après résultat inconnu.

Le worker UIA ne déduit pas `region=header` depuis une position ou un nom. Cette
région doit provenir d'un provider qui la démontre ; en son absence, le guard
header refuse une écriture. OCR ne résout pas le composer custom opaque. Le
provider local multimodal existe derrière adapter, mais aucun modèle n'est
encore qualifié sur ce poste. Une preuve d'absence requiert un arbre complet.
Le nouveau paste visuel refuse un clipboard riche qu'il ne sait pas restaurer.
