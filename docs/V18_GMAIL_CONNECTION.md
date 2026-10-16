# V18 — connecter Gmail et choisir ses capacités

Branche : `feature/gmail-connector-onboarding-v18`, basée sur V17 `8b463a6`.

## Ce qui est prêt

Le nouvel onglet **MCP → Gmail** connecte un serveur MCP, découvre ses outils
et permet de choisir les capacités proposées à Jarvis. Il reconnaît les outils
Gmail de lecture, brouillons, envoi, classement et suppression. Une fonction
absente du serveur reste absente de l’interface : aucun nom, paramètre, compte
ou résultat n’est inventé.

Le parcours principal utilise **Pipedream MCP personnel**, avec une connexion
Google gérée dans le navigateur. Il ne demande pas de créer son propre client
OAuth dans Google Cloud. Une autre option réutilise un serveur MCP déjà
configuré sans remplacer son adresse ni son authentification.

La connexion au compte personnel et les opérations réelles restent à tester
sur le PC du propriétaire. La découverte des outils ne prouve pas l’identité
de la boîte ni la réussite d’un envoi.

## Installer dans le checkout de test Windows

Fermer Jarvis avant de changer de version. Dans PowerShell :

```powershell
$ErrorActionPreference = 'Stop'
cd D:\Django_Projects\jarvis-main\jarvis-mcp-test
if (git status --porcelain --untracked-files=no) { throw 'Modifications locales à préserver.' }
git fetch origin
if ($LASTEXITCODE) { throw 'Échec de récupération GitHub.' }
git switch --detach origin/feature/gmail-connector-onboarding-v18
if ($LASTEXITCODE) { throw 'Échec de sélection V18.' }
& 'C:\jv312\Scripts\python.exe' -m pip install -r requirements-mcp.txt
if ($LASTEXITCODE) { throw 'Échec d’installation des dépendances MCP.' }
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath 'D:\Django_Projects\jarvis-main\jarvis-main\.env'
```

La commande pip installe le SDK MCP dans l’environnement utilisé par le lanceur. Les préférences des
modèles, clés existantes, mémoire, profils, activités et travaux V17 restent
dans leurs stockages habituels. Aucune clé n’est copiée depuis ChatGPT.

## Connecter le premier compte

1. Ouvrir **MCP → Gmail**, ou **MCP → Mon espace → Gmail → Préparer la
   configuration**. Garder « Connexion Google gérée par Pipedream » et cliquer
   **Connecter Gmail avec Google**.
2. Dans le navigateur, se connecter ou créer le compte **Pipedream MCP**,
   sélectionner **Gmail**, connecter le bon compte Google et examiner les
   droits proposés. Autoriser ensuite le client Jarvis et revenir dans
   l’application. Le compte Pipedream MCP est distinct du compte développeur
   pipedream.com. Pour ce premier essai, sélectionner uniquement Gmail.
3. Vérifier les outils découverts. Cliquer **Cocher les lectures** pour démarrer,
   ou choisir aussi les fonctions d’écriture souhaitées. Cliquer ensuite
   **Enregistrer les capacités cochées**. Si MCP est désactivé, cliquer
   **Activer MCP dans Jarvis** ; cette activation globale expose également les
   autres outils déjà autorisés dans le centre MCP.
4. Cliquer **Vérifier ma boîte avec Jarvis**. Le résultat arrive dans la
   conversation : compte réellement accessible et nombre de messages non lus
   si les outils le permettent. Cette demande ne doit rien modifier. Si le
   serveur ne permet pas d’obtenir l’identité, Jarvis doit l’indiquer.

« Tout cocher » sélectionne les outils Gmail affichés ; l’enregistrement reste
explicite. Les autres applications présentes sur le même serveur ne sont pas
autorisées par cette sélection. Le bouton d’actualisation recharge uniquement
l’inventaire, sans lire la boîte. Si les outils ou leurs accès changent pendant
la sélection, Jarvis conserve les cases mais exige de recharger avant
d’enregistrer.

**Annuler la connexion** interrompt l’attente OAuth. Cette annulation ne retire
pas un consentement déjà donné chez Google ou Pipedream : la révocation chez le
fournisseur s’effectue dans les réglages de ce compte. Les réglages MCP avancés
permettent de désactiver le serveur, révoquer les outils ou oublier les
identifiants conservés par Jarvis.

## Accès et actions

| Couche | Ce qu’elle autorise |
|---|---|
| Consentement Google chez Pipedream | Les droits réels sur le compte Google choisi |
| Authentification MCP de Jarvis | L’accès au serveur Pipedream ; jetons conservés dans le coffre local existant |
| Inventaire et cases Gmail | Les seuls outils découverts que Jarvis peut proposer au modèle |
| Lectures automatiques | Les lectures cochées dont le schéma et le nom passent la politique existante |
| Confirmation d’un envoi ou d’une modification | L’exécution de cet appel avec ses paramètres observés |
| Résultat de l’outil | Une observation du service, distincte d’une preuve de livraison ou de réussite globale |

La sélection locale ne peut pas accorder un droit refusé par Google ou absent
du serveur. Un outil non classé comme lecture reste soumis à confirmation,
même lorsque sa case est cochée. Les quotas, la pause après échec et le journal
des résultats incertains restent actifs. Reconnecter et réautoriser Gmail ne
permet pas de rejouer automatiquement un envoi au résultat inconnu.

Les autorisations OAuth concernent Pipedream MCP (`openid profile email
offline_access mcp`) ; les droits Gmail sont ceux montrés dans le consentement
Google du fournisseur. Jarvis ne récupère pas une connexion Gmail de ChatGPT.
Les appels et les données transitent par Pipedream ; les contenus consultés
peuvent aussi être transmis au cerveau IA configuré dans Jarvis.

## Place dans l’orchestre

Le parcours utilise le transport MCP et le SDK OAuth existants, puis le même
registre d’outils et la même boucle d’exécution que les autres services. Aucun
second moteur Gmail ni client API Google parallèle n’est ajouté. Cerebras,
sa clé secondaire, Groq et les autres secours configurés conservent leur rôle.

L’association locale Gmail est lisible par le spécialiste **Communications**,
y compris si le serveur porte un identifiant personnalisé. Il peut utiliser les
lectures Gmail préautorisées dans son contexte isolé ; les outils étrangers au
service ne lui sont pas attribués par cette association. Les spécialistes
analysent et préparent ; Jarvis garde les actions externes et leurs
confirmations. Les travaux programmés et budgets V17 restent applicables.

## Recette réelle, progressivement

1. Vérifier le bon compte avec le bouton de l’onglet.
2. Demander : « Résume mes cinq derniers mails non lus, sans rien modifier. »
   Comparer avec Gmail et vérifier qu’aucun message n’est marqué comme lu.
3. Autoriser les brouillons, puis demander un brouillon pour un destinataire
   explicitement identifié. Examiner la confirmation et retrouver le brouillon
   dans Gmail. Ne pas assimiler un texte de réponse à un brouillon créé.
4. Une fois l’adresse du propriétaire vérifiée, demander explicitement un mail
   de test à cette adresse, confirmer l’envoi et contrôler sa réception ainsi
   que les éléments envoyés. Aucun envoi n’est inclus dans les tests automatisés.
5. Redémarrer Jarvis et vérifier que les accès choisis restent visibles.
   Contrôler aussi les fonctions quotidiennes déjà utilisées : mémoire récente,
   recherche YouTube, Bloc-notes, profils et rapports des spécialistes.

Une erreur d’authentification, un quota fournisseur ou l’absence d’un outil doit
apparaître comme un blocage identifiable. Conserver le journal local de la
session et l’opération concernée sans partager jeton, clé ou contenu privé.

## Vérification et limites

Les 27 tests ajoutés utilisent le vrai registre, le stockage local des
associations, le contrôle des permissions, la passerelle MCP et les payloads
Groq/Cerebras. Les transports distants et réponses des modèles sont simulés.
Le test OAuth utilise le vrai SDK avec HTTP simulé, découverte de la ressource
protégée, inscription du client, PKCE S256 et stockage des jetons dans un coffre
de test. Les essais Qt couvrent le parcours des boutons, l’annulation, les
capacités modifiées, la file de conversation et un panneau de 300 px.

Les métadonnées publiques du fournisseur ont été vérifiées sans compte :
endpoint protégé par HTTP 401, serveur d’autorisation annoncé par la ressource,
PKCE et inscription dynamique disponibles. Aucun compte personnel n’a été
connecté, aucune boîte réelle lue, aucun envoi réel effectué. La compatibilité
du consentement Google et l’inventaire précis restent à confirmer par la recette.

Le parcours personnel est documenté comme gratuit au moment de cette étude,
le 16 octobre 2026 ; cela ne garantit ni gratuité permanente ni quotas illimités.
L’intégration commerciale de Connect pour les comptes d’utilisateurs d’une
application suit un autre parcours et une tarification distincte. Le serveur
Google Workspace du catalogue avancé reste une alternative séparée, avec ses
prérequis de Developer Preview et ses propres limites.

## Sources du fournisseur

- [Pipedream MCP : connexion pour un utilisateur](https://pipedream.com/docs/connect/mcp/users)
- [Pipedream MCP : fonctionnement, autorisations et usages](https://pipedream.com/docs/connect/mcp)
- [Pipedream Gmail et ses outils](https://mcp.pipedream.com/app/gmail)
- [Modes des outils MCP](https://pipedream.com/docs/connect/mcp/tool-modes)

Endpoint utilisé : `https://mcp.pipedream.net/v2`. Les outils réellement
découverts lors de la connexion font foi. Calendar sera traité après la recette
Gmail, avec sa propre sélection et vérification.
