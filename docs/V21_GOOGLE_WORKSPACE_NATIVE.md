# V21 — Un compte Google, cinq services

Branche : `feature/google-workspace-native-v21`, issue de V20. Pas de fusion
de la branche de référence Windows. Pas de transfert des autorisations ChatGPT.

## Ce qui change

**CONNEXIONS → Mon espace → Connecter Google** ouvre une page commune à Gmail,
Calendar, Drive, Sheets et Docs. Les API Google sont appelées directement :
aucun compte Pipedream, processus `gog`/`gws` ou serveur MCP distant nécessaire.
La connexion n’est pas présentée comme un serveur MCP Google : le registre
distingue explicitement le fournisseur natif `kind: google`.

| Service | Fonctions intégrées |
| --- | --- |
| Gmail | Profil, compteurs exacts de messages et fils non lus, recherche paginée, messages/fils/brouillons, création et modification de brouillons, envoi/réponse/envoi d’un brouillon, libellés, corbeille/restauration |
| Calendar | Agendas, recherche/liste/lecture d’événements, création, modification et annulation ; notifications aux invités contrôlées explicitement |
| Drive | Recherche et métadonnées, export texte des fichiers Workspace compatibles, dossiers, copie, renommage/description et corbeille |
| Sheets | Métadonnées du classeur, lecture A1, création, remplacement/ajout/effacement de valeurs ; `RAW` par défaut |
| Docs | Lecture du texte, y compris tableaux et onglets, création, ajout et remplacement de texte avec contrôle de révision |

Il s’agit de **40 opérations compilées**, pas de toutes les fonctions possibles
des API. Pas encore : pièces jointes, import de fichiers locaux, partage Drive,
mise en forme avancée Sheets/Docs, Contacts ou Slides. La corbeille Gmail/Drive
est récupérable : aucune suppression définitive n’est exposée. La liste est
limitée aux droits réellement accordés par Google.

## Configuration initiale — une seule fois

Google exige un **client OAuth propre à l’application**. Une clé API ne donne
pas accès à une boîte Gmail personnelle. OpenClaw/gogcli et Hermes ont aussi
une étape initiale d’identification de leur client OAuth.

1. Dans [Google Cloud](https://console.cloud.google.com/), créer ou choisir un
   projet dédié à Jarvis.
2. Dans **API et services → Bibliothèque**, activer : **Gmail API**, **Google
   Calendar API**, **Google Drive API**, **Google Sheets API** et **Google Docs API**.
3. Dans **Google Auth Platform**, configurer le nom de l’application et son
   audience. Pour un compte Gmail personnel en mode **Test**, ajouter son
   adresse dans les utilisateurs tests. Pour un compte Workspace, respecter
   aussi les restrictions de l’administrateur.
4. Créer un client OAuth de type **Application de bureau** puis télécharger son
   fichier JSON. Ne pas choisir Application Web ni Compte de service.
5. Dans Jarvis : **CONNEXIONS → Google → Importer la configuration Google**.
   Choisir le JSON téléchargé. Le client est conservé dans le coffre du système,
   pas dans `.env` ni dans `mcp_servers.json`.
6. Cliquer **Connecter Google**, choisir le compte puis accepter les droits
   souhaités sur la page Google. Attendre le résultat **dans Jarvis** : le simple
   retour du navigateur ne certifie pas que la connexion est terminée.

Le navigateur par défaut s’ouvre (Chrome si Chrome est défini par défaut).
Le retour écoute uniquement sur `127.0.0.1`, avec port aléatoire, état OAuth
aléatoire, PKCE S256, code à usage unique et délai/annulation. Les échanges
distants passent exclusivement par les points d’accès Google compilés.

En mode Test, les autorisations hors ligne utilisant ces scopes peuvent expirer
après **sept jours** : il faut alors reconnecter Google. Une application
distribuée à d’autres utilisateurs peut nécessiter la vérification Google et,
selon les scopes et le traitement des données restreintes, des exigences
supplémentaires. Cette branche ne prétend pas disposer d’un client public validé
par Google. Pour une distribution réellement prête à connecter, l’éditeur doit
fournir/configurer ce client et satisfaire ces exigences.

## Lancer sur Windows

Fermer Jarvis avant de changer de version. Coller uniquement les commandes,
sans `PS ...>` ni les invites `>>`. Si PowerShell attend une commande incomplète,
faire `Ctrl+C` avant de recommencer.

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'D:\Django_Projects\jarvis-main\jarvis-mcp-test'
if (git status --porcelain) { throw 'Modifications locales à préserver : ne pas changer de version.' }
git fetch origin
if ($LASTEXITCODE) { throw 'Échec GitHub.' }
git switch --detach origin/feature/google-workspace-native-v21
if ($LASTEXITCODE) { throw 'Échec de sélection V21.' }
& 'C:\jv312\Scripts\python.exe' -m pip install -r requirements-mcp.txt
if ($LASTEXITCODE) { throw 'Échec des dépendances connecteurs.' }
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath 'D:\Django_Projects\jarvis-main\jarvis-main\.env'
```

Les préférences de modèles, la mémoire, l’univers spatial, les profils métier
et la configuration Chrome ne sont pas remplacés. Au lancement, aucune
connexion Google ni lecture de boîte/document n’est déclenchée.

## Vérifier avant de demander une mission

1. Ouvrir la page **Google**, importer le client si nécessaire puis connecter
   le compte. Une autorisation enregistrée ne signifie pas que toutes les API
   ont déjà été testées : l’interface l’indique séparément.
2. Cliquer **Vérifier Gmail · lecture seule**. Ce bouton utilise le vrai gateway
   et ses permissions, sans LLM ni mission antérieure. Résultat attendu : adresse
   retournée par Gmail, nombre exact de **messages** non lus au total et dans
   INBOX. Aucun message n’est marqué lu. Ne pas confondre ces nombres avec les
   compteurs de **fils/conversations** affichés par certaines vues Gmail.
3. Dans la conversation, utiliser **Nouvelle demande** si une ancienne mission
   reste volontairement suspendue, puis essayer séparément :

   - « Liste mes agendas accessibles et mes rendez-vous de demain, sans rien modifier. »
   - « Recherche dans Drive les fichiers dont le nom contient suivi, sans rien modifier. »
   - « Lis les cellules A1:C10 de ce classeur [ID réellement obtenu dans Drive]. »
   - « Lis le texte de ce document [ID réellement obtenu dans Drive]. »

4. Pour tester les écritures, utiliser des ressources de test : créer un
   brouillon **sans l’envoyer**, un événement sans invité, un classeur vide et un
   document de test. Vérifier le contenu de la confirmation avant de l’accepter,
   puis vérifier le résultat dans Google. Aucun envoi réel n’est nécessaire à
   ce premier essai.

Les paramètres sont validés localement. Les messages et documents longs sont
lus par fragments ; les listes conservent leurs curseurs de pagination. Les
opérations ne promettent jamais la réalisation d’un objectif à partir d’un
simple succès de transport.

## Droits, stockage et agents

Les scopes demandés sont : identité Google, `gmail.modify`, `calendar`, `drive`,
`spreadsheets`, `documents`. Ce sont des accès étendus : lire le consentement
Google avant de l’accepter. Les scopes obtenus restreignent effectivement les
outils activés. Les permissions ne sont jamais étendues en arrière-plan.

Le coffre utilise **DPAPI sous Windows** ou **Secret Service sous Linux**.
Pas de stockage de jetons en clair ni de repli en clair. Les arguments et
contenus Google ne sont pas écrits dans le journal technique minimal des appels.
Les contenus demandés à Jarvis peuvent cependant entrer dans sa conversation,
ses checkpoints/contextes locaux et les requêtes au fournisseur IA configuré.
Ce coffre ne protège pas d’un programme déjà exécuté comme le même utilisateur.

Le gateway existant conserve : permissions locales, schémas épinglés pour les
lectures automatiques, confirmations des mutations, quotas et reçus persistants.
Les spécialistes ne gagnent que des lectures correspondant à leur rôle et aux
associations actives : Communications → Gmail/Calendar ; Documents → Drive/Docs ;
Données → Drive/Sheets ; Automatisations → Calendar. Leurs profils peuvent encore
réduire ce périmètre. Les modifications externes restent confiées au coordinateur
et à ses confirmations habituelles.

L’identifiant de connexion dépend du sujet Google, pas seulement de l’adresse :
changer de compte ne transfère pas les verrous d’anciens envois incertains.
Une ancienne connexion n’est mise en pause qu’après activation réussie et si
elle ne sert pas à un autre service non remplacé. Ses réglages, autorisations
et historiques restent conservés. Le client, le compte et les cinq associations
sont revérifiés avant la validation finale ; une modification concurrente
interrompt la connexion au lieu d’écraser ces changements.

**Déconnecter Google de Jarvis** désactive les fonctions et efface les jetons
locaux de ce compte, en conservant la configuration de l’application. Cela ne
supprime aucun mail/document. Pour révoquer aussi le consentement chez Google,
utiliser [les connexions du compte Google](https://myaccount.google.com/connections).

## Correction des échecs observés en V20

- Le client HTTP du MCP avait un délai implicite de cinq secondes, inférieur
  au délai global de Jarvis. Le délai HTTP est maintenant explicite, sans
  agrandir les limites externes ni ajouter de répétition automatique.
- Après la revue d’un ancien appel, une mission qui n’avait **pas envoyé** sa
  lecture pouvait rester bloquée. Le worker transmet désormais cette revue
  exacte à la mission : uniquement garde locale de lecture préautorisée,
  tentative correspondante et aucun autre effet incertain. L’étape est annulée,
  non certifiée ; la prochaine demande explicite peut poursuivre. Aucun LLM ni
  appel n’est relancé par le bouton Examiné.
- Les lectures **GET compilées du nouveau fournisseur Google** ont un résultat
  d’échec connu si la réponse est perdue : elles ne changent pas les données.
  Ce traitement ne s’applique pas à un outil MCP distant simplement nommé
  « read ». Un envoi ou une modification incertaine reste verrouillé.
- Une mission supervisée ou un appel réellement envoyé avec résultat inconnu
  conserve sa procédure de récupération indépendante. Ne pas effacer les bases
  de données pour contourner ce contrôle.

## Vérification automatique

Les nouveaux tests couvrent le vrai retour HTTP local OAuth avec Google simulé,
PKCE/état/Host/annulation, scopes partiels, coffre indisponible, renouvellement,
changement de compte, transactions et associations, 40 routes REST, lectures
versus écritures, données MIME/chunks/révisions, vrais gateways et payloads
Cerebras/Groq simulés, rôles spécialisés et Qt à 300 px. La régression cumulative
et Browser Bridge restent nécessaires. **Ce n’est pas une connexion à ton compte
ni un test Windows utilisateur avec Google réel.**

```powershell
& 'C:\jv312\Scripts\python.exe' scripts\run_final_regression.py --verbose --pattern '*v21.py'
& 'C:\jv312\Scripts\python.exe' scripts\run_final_regression.py --verbose
node --test tests/browser_bridge_replay.mjs
```

## Retour à V20

Avant de revenir à V20, qui ne connaît pas `kind: google`, **déconnecter Google**
puis ouvrir **Services → Capacités** dans V21 et retirer uniquement les
nouvelles configurations `google_<identifiant>` avec le bouton de suppression
de configuration (icône corbeille « Retirer le serveur MCP »). Cette suppression
retire leurs jetons locaux, pas les données
Google ni leur historique d’appels. Ne pas supprimer les anciens serveurs.
La mémoire personnelle n’a pas besoin d’être effacée. L’ancien parcours
Gmail/Pipedream est toujours accessible dans V21 ;
ses réglages avancés permettent de réactiver volontairement sa configuration.
Ne pas réessayer un envoi incertain sans vérifier son effet dans le service.

## Références de conception

Implémentation Jarvis originale, sans copie ni exécution de scripts tiers.
Les références ont servi à comparer le flux OAuth et les fonctions utiles :

- [OpenClaw — compétence gog](https://github.com/openclaw/openclaw/blob/main/skills/gog/SKILL.md)
- [gogcli — démarrage](https://github.com/openclaw/gogcli/blob/main/docs/quickstart.md)
- [Hermes — Google Workspace](https://github.com/NousResearch/hermes-agent/blob/main/skills/productivity/google-workspace/SKILL.md)
- [Google — OAuth pour applications de bureau](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google — expiration des autorisations](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Gmail — scopes et vérification](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Gmail — compteurs de libellés](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/get)
- [Calendar — événements](https://developers.google.com/workspace/calendar/api/v3/reference/events)
- [Drive — fichiers](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
- [Sheets — valeurs](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values)
- [Docs — requêtes et onglets](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request)
