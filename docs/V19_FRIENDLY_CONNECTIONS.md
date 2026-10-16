# V19 — des connexions simples pour l’utilisateur

Branche : `feature/friendly-connectors-v19`, basée sur V18 `01754fb`.

## Parcours Gmail

Ouvrir **CONNEXIONS → Gmail**. Le premier écran montre le bouton
**Connecter Gmail avec Google**. Pipedream ouvre son parcours dans le navigateur :
se connecter, sélectionner Gmail, connecter le compte Google souhaité et
examiner les droits demandés. Revenir ensuite dans Jarvis.

Les fonctions réellement découvertes apparaissent en français :

- Lire et rechercher.
- Gérer les brouillons.
- Envoyer et répondre.
- Classer les messages.
- Supprimer des messages.

Choisir les fonctions souhaitées, puis **Enregistrer mes choix**. Les boutons
**Lecture seule** et **Tout sélectionner** remplissent les cases ; ils
n’accordent aucun accès avant l’enregistrement. Les groupes absents du serveur
ne sont pas proposés. Un groupe partiellement coché conserve la sélection
précise déjà enregistrée ; le cocher complètement est une décision explicite.

Si les connexions sont désactivées dans Jarvis, le bouton **Activer mes
connexions dans Jarvis** apparaît. Il active aussi les autres services déjà
autorisés, comme l’indique l’écran. Puis **Vérifier mon compte Gmail** confie à
Jarvis une lecture du compte accessible, sans modifier la boîte. Le résultat
arrive dans la conversation.

Les boutons d’étapes terminées disparaissent. Les envois et modifications
conservent leurs confirmations. Les accès enregistrés et les choix partiels ne
sont pas élargis par l’ouverture de l’écran.

## Réglages avancés

**Réglages avancés** donne accès au serveur MCP déjà configuré, au changement de
compte, à l’actualisation, aux outils individuels, à la préautorisation des
lectures et au centre MCP existant. Cette section est repliée au départ et reste
accessible dans un panneau de 300 px.

Les cases par groupe et les cases individuelles représentent la même sélection.
Si le serveur ou ses fonctions changent pendant une sélection, les choix sont
conservés, l’enregistrement est désactivé et **Recharger les fonctions** apparaît
sur l’écran principal. Recharger demande une nouvelle vérification des droits.

## Est-ce réutilisable pour les autres connecteurs ?

Oui : le transport MCP, la découverte, le coffre, OAuth et l’exécution des
fonctions restent communs. Le nouveau composant `ConnectorFunctionsPanel`
reçoit les fonctions observées et les noms de groupes ; il peut présenter un
agenda, des documents ou une autre application avec leurs propres libellés.
Il ne se connecte pas à un service et n’accorde aucune permission à lui seul.

| Élément | État dans cette version |
|---|---|
| Connexion Gmail guidée | Intégrée, tests automatisés réussis ; compte personnel à connecter |
| Choix des fonctions par groupe | Composant commun, utilisé pour Gmail ; testé aussi avec des données d’agenda simulées |
| Autres serveurs MCP compatibles | Centre MCP avancé conservé, configuration selon leur authentification |
| Même parcours simple pour Calendar, Drive ou d’autres services | Réutilisable, à brancher sur chaque service et à valider |
| Application métier MS Football | Nécessite un serveur ou adaptateur exposant ses fonctions autorisées |

L’expérience peut être commune même si les fournisseurs ont des exigences
différentes : connexion dans le navigateur, clé API, client préenregistré ou
serveur local. Certaines fonctions dépendent de l’offre, des droits accordés ou
des quotas. Chaque connecteur doit être vérifié avec ses outils réels avant de
le présenter comme opérationnel.

Pipedream reste le fournisseur du parcours Gmail actuel : les données y
transitent et ses conditions s’appliquent. L’autorisation Google sur le PC reste
nécessaire. Aucun autre compte n’est connecté par cette modification.

## Installer et essayer sur Windows

Fermer Jarvis et utiliser le checkout de test habituel :

```powershell
$ErrorActionPreference = 'Stop'
cd D:\Django_Projects\jarvis-main\jarvis-mcp-test
if (git status --porcelain --untracked-files=no) { throw 'Modifications locales à préserver.' }
git fetch origin
if ($LASTEXITCODE) { throw 'Échec de récupération GitHub.' }
git switch --detach origin/feature/friendly-connectors-v19
if ($LASTEXITCODE) { throw 'Échec de sélection V19.' }
& 'C:\jv312\Scripts\python.exe' -m pip install -r requirements-mcp.txt
if ($LASTEXITCODE) { throw 'Échec d’installation des dépendances MCP.' }
.\scripts\run_p0_acceptance.ps1 -Full -EnvPath 'D:\Django_Projects\jarvis-main\jarvis-main\.env'
```

1. Ouvrir **CONNEXIONS → Gmail**. Vérifier que les réglages techniques sont
   repliés et que le bouton de connexion est visible.
2. Si Gmail était déjà configuré, retrouver les fonctions autorisées et les
   sélections partielles. L’affichage ne doit rien autoriser de plus.
3. Sinon, connecter le compte, choisir **Lecture seule**, enregistrer et activer
   les connexions si nécessaire. Vérifier le compte et comparer avec Gmail.
4. Demander « Résume mes cinq derniers mails non lus sans rien modifier. »
5. Ouvrir **Réglages avancés** pour contrôler les outils individuels, puis
   réduire la fenêtre et vérifier que tous les contrôles restent accessibles.

L’essai d’un envoi ou d’une modification reste une demande séparée et explicite.
Les transports et comptes des tests automatisés sont simulés ; ils ne prouvent
pas une lecture réelle, une identité Google ni une livraison de mail.

## Validation

Sept nouveaux essais couvrent la réutilisation du composant avec un autre type
de service, les droits partiels, le parcours simplifié utilisant les commandes
existantes, l’absence d’effets avant enregistrement et l’invalidation d’une
sélection ancienne et le retour du bouton de connexion après expiration de
l’autorisation. Les essais Gmail V18 et la géométrie Qt sont conservés.

Les couches OAuth, coffre, quotas, journal des résultats incertains et
confirmation n’ont pas été remplacées. Le parcours continue d’utiliser les
cerveaux et la cascade configurés dans Jarvis.

Références :

- [Pipedream MCP pour les utilisateurs](https://pipedream.com/docs/connect/mcp/users)
- [Modes d’autorisation MCP selon le transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [Détails du connecteur Gmail V18](V18_GMAIL_CONNECTION.md)
