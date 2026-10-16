# V20 — connecter Gmail comme un connecteur personnel

Branche : `feature/plugin-connect-ready-v20`, basée sur V19 `34667c0`.

## Parcours normal

1. Ouvrir **CONNEXIONS → Gmail** et cliquer **Connecter Gmail avec Google**.
2. Terminer la connexion dans le navigateur habituel : compte Pipedream MCP,
   sélection de Gmail et autorisation du compte Google souhaité.
3. Revenir dans Jarvis et lui donner directement une demande : rechercher,
   résumer des messages, préparer un brouillon ou envoyer un mail.

La connexion comprend désormais l’accès aux fonctions Gmail découvertes et
l’activation de leur utilisation par Jarvis. Il n’y a plus de sélection fonction
par fonction, d’enregistrement de cases ou d’activation supplémentaire dans ce
parcours. Le contrôle global des connexions est également activé ; les autres
services déjà autorisés redeviennent donc utilisables. Ce comportement est
annoncé à côté du bouton de connexion.

**Ce que Jarvis peut faire** est une liste informative issue des fonctions
réellement découvertes. Le catalogue Gmail ouvre le même parcours. La
vérification du compte est facultative et disponible dans la conversation.
La découverte ne prouve pas l’identité de la boîte ni la réussite d’un envoi.

La connexion n’appelle aucun outil de lecture ou d’envoi de mail. Après celle-ci,
les lectures préautorisées sont utilisables par Jarvis ; les envois et
modifications conservent le circuit de confirmation des actions. L’accès aux
fonctions dépend toujours des droits du compte et de ce que le fournisseur
expose. Une fonction absente n’est pas simulée.

## Ce qui change dans le moteur

Le bouton normal émet une seule commande de connexion avec une politique
explicite `access=plugin`. Après OAuth et découverte, le runtime associe Gmail,
vérifie le compte-rendu d’inventaire puis autorise les fonctions Gmail observées.
Les autorisations et l’activation locale sont enregistrées dans une même
écriture du registre. Les fonctions d’autres applications et les exécuteurs
génériques d’un serveur partagé restent en dehors de cette autorisation.

L’échec d’OAuth, une annulation, l’absence de fonctions Gmail ou une connexion
modifiée avant l’enregistrement empêchent l’activation nouvelle. Les quotas et
le journal des résultats incertains sont conservés : reconnecter Gmail ne rejoue
pas un envoi incertain. Les métadonnées modifiées restent soumises aux contrôles
existants ; aucun effet externe n’est certifié par une simple connexion.

**Réglages avancés** conserve les choix individuels, les groupes, leur
restriction et le parcours manuel de V18/V19. Les anciennes commandes manuelles
gardent leur comportement. Leur ouverture ne modifie pas les accès existants.

## Connexion au compte personnel

Pipedream gère le parcours OAuth et les appels du connecteur. Le navigateur peut
montrer plusieurs écrans de connexion et de consentement, selon le compte.
Les données transitent par ce fournisseur et ses conditions s’appliquent.
Les identifiants restent dans le coffre existant de Jarvis. Une connexion
ChatGPT n’est pas copiée vers Jarvis.

Le compte **Pipedream MCP** est distinct du compte développeur Pipedream.
Son portail d’inscription est <https://mcp.pipedream.com/sign-up> ; la fiche
Gmail se trouve sur <https://mcp.pipedream.com/app/gmail>. Si le formulaire
affiche « Please sign up with your work email address », vérifier le portail
utilisé. Cette erreur d’inscription vient du fournisseur ; la connexion Gmail
n’est pas encore effectuée. Le portail MCP ne garantit pas que le fournisseur
acceptera un compte donné.

Le correctif de veille vocale permet au clic de connexion d’interrompre
l’attente du double clap, la capture audio et l’admission d’une transcription
devenue périmée. La commande s’exécute dans le worker existant ; le mode vocal
reprend ensuite. Aucun clap ni passage préalable en mode texte n’est requis.
Le journal indique `MCP_CONTROL … state=STARTED` au début du traitement.

L’enregistrement dynamique du client utilise les mêmes scopes explicitement
choisis que la demande de consentement, même si les métadonnées du fournisseur
annoncent une liste vide. Le parcours interactif dispose de dix minutes ; les
appels ordinaires gardent leur limite courte et une annulation reste immédiate.
Les étapes apparaissent dans le panneau Gmail et dans les lignes `MCP_CONTROL`.
Un échec affiche son étape et, lorsqu’ils sont disponibles, le statut HTTP et un
code OAuth reconnu. Les corps de réponse, codes d’autorisation, jetons et URL
de retour ne sont pas recopiés dans ces diagnostics.

Après une autorisation réussie, Jarvis distingue les outils exécutables des
métadonnées facultatives. Un refus JSON-RPC `MethodNotFound` (`-32601`) pour
`resources/list`, `resources/templates/list` ou `prompts/list` ne bloque plus les
outils découverts. L’inventaire correspondant est signalé comme partiel. Un échec
de `tools/list`, d’authentification ou un autre refus reste bloquant : les outils
anciens ne sont pas activés à la place d’une nouvelle vérification.

Si OAuth a enregistré ses identifiants et des outils Gmail avant un échec de
l’inventaire, le bouton reprend cette vérification avec le coffre existant.
Il ne refait pas le consentement dans ce cas précis. Une déconnexion explicite,
une autorisation révoquée ou un changement de fournisseur garde le parcours
normal. Un échec RPC indique maintenant aussi la méthode et son code numérique,
sans recopier le message ni les données du serveur.

Le journal utilisateur du 16 octobre confirme les étapes `credentials_storage`
et `authorized`, suivies d’un échec `MCPError` à `service_inventory`. Il ne donne
pas encore la méthode ni le code : le refus d’une métadonnée facultative est un
cas reproduit et corrigé, pas une attribution certaine de cet échec réel.

La page de retour local distingue un retour accepté d’un retour refusé.
Un retour accepté confirme uniquement la réception de l’autorisation ; le
résultat de connexion se vérifie dans Jarvis après l’échange du code, la session
MCP et la découverte des fonctions. Un ancien lien de retour ne se réutilise
pas pour une nouvelle tentative : recommencer depuis le bouton Gmail.

Le test SDK complet utilise un vrai serveur HTTP local, un navigateur simulé
et des réponses distantes de test. Il vérifie en une fois le callback sans
paramètre `iss`, la cohérence des scopes, l’échange du code, la session MCP,
l’inventaire et l’enregistrement au coffre. Les refus de jeton, l’annulation,
le budget interactif et le maintien de l’interface occupée sont aussi testés.
Le parcours complet couvre aussi les trois listes facultatives non implémentées,
les refus bloquants et la reprise avec les identifiants enregistrés, sans nouvel
échange OAuth ni appel à la boîte. Cela ne certifie pas l’accès à une boîte réelle.

Pour une première utilisation réelle, vérifier le compte puis demander une
lecture sans modification. Un envoi de test doit être demandé explicitement et
contrôlé dans Gmail. Aucun compte personnel n’a été connecté pendant le
développement ; les services et comptes des tests automatisés sont simulés.

## Installer dans le checkout Windows habituel

Fermer Jarvis, puis :

```powershell
$ErrorActionPreference = 'Stop'
cd D:\Django_Projects\jarvis-main\jarvis-mcp-test
if (git status --porcelain --untracked-files=no) { throw 'Modifications locales à préserver.' }
git fetch origin
if ($LASTEXITCODE) { throw 'Échec de récupération GitHub.' }
git switch --detach origin/feature/plugin-connect-ready-v20
if ($LASTEXITCODE) { throw 'Échec de sélection V20.' }
& 'C:\jv312\Scripts\python.exe' -m pip install -r requirements-mcp.txt
if ($LASTEXITCODE) { throw 'Échec d’installation des dépendances MCP.' }
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath 'D:\Django_Projects\jarvis-main\jarvis-main\.env'
```

Les modèles, profils, mémoire, activités et travaux existants sont conservés.

## Autres connecteurs

Le même principe est destiné aux autres services : connexion du compte,
fonctions disponibles puis utilisation par Jarvis. Le centre MCP et son
catalogue sont conservés. Cette version applique le nouveau parcours à Gmail ;
Calendar et les autres services doivent encore être branchés et validés avec
leur authentification et leurs fonctions propres.

Six nouveaux tests vérifient le bouton jusqu’au registre et à la passerelle,
l’absence de commande supplémentaire de configuration, le respect d’un serveur
existant, l’absence de fonctions Gmail, l’échec d’OAuth, le changement de
connexion et le maintien de la protection contre le rejeu d’un envoi incertain.
Ils s’ajoutent à la régression cumulative, aux tests Qt et au contrat OAuth SDK.

Sources : [authentification des plugins OpenAI](https://developers.openai.com/plugins/build/auth)
et [connexion Pipedream MCP](https://pipedream.com/docs/connect/mcp/users).
