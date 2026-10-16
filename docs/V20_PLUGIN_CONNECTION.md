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
