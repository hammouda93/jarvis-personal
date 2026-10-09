# MCP : Transports, Secrets Et Consentement

Checkpoint implementation : `420179b`, conserve dans la branche de developpement.
Un catalogue, une configuration et une decouverte ne prouvent pas une connexion
a un compte personnel. Aucun compte personnel n'a ete connecte par ces tests.

## Transports

Le SDK officiel MCP gere les sessions HTTP et stdio. Les sessions sont courtes;
`connected_now=false` apres fermeture est normal, pas un echec silencieux.
La decouverte reconcilie les schemas et revoque les permissions modifiees.
Chaque outil doit etre autorise explicitement; l'approbation du serveur ne
donne pas acces a tous ses outils.

Le stdio generique exige un executable local et une confiance explicite.
Le processus a les droits du compte utilisateur : ce n'est PAS un sandbox OS.
Les shells/evaluations implicites sont refuses, l'environnement est reduit,
les credentials doivent etre des references `JARVIS_MCP_*`, pas des valeurs
inscrites dans la configuration. Le profil historique `hermes mcp serve` reste
disponible; enregistrer ce profil n'installe ni ne lance Hermes.

HTTP refuse les redirections implicites de l'endpoint d'outil. OAuth utilise
les controles d'URL/callback et PKCE/state du SDK; le consentement navigateur
est une commande explicite de l'operateur, jamais un effet cache du modele.

## Credentials Et OAuth

Windows : coffre DPAPI lie au compte utilisateur, fichiers chiffres atomiques.
Linux : backend SecretService officiel via keyring, pas de fallback plaintext.
Les tests Windows incluent une vraie operation DPAPI avec des donnees synthetiques.
Un runner Linux sans service de coffre doit refuser le stockage. Le coffre
SecretService d'un vrai desktop Linux reste a valider.

L'interface n'affiche ni ne recharge les secrets. Les champs sensibles sont
vides apres soumission. Supprimer/revoquer purge les credentials et bloque
les outils; une panne de purge ne doit pas faire croire a une suppression.

OAuth supporte l'enregistrement dynamique ou un client preenregistre
(public, secret Basic/POST), des scopes explicites et un callback loopback.
Les tokens/client/metadata sont lies au serveur et a sa cible; la rotation
du refresh token preserve un token omis. L'expiration est absolue et survive
au redemarrage. Les ecritures sont validees apres decouverte reussie.
Un modele utilisant un credential existant ne peut ni declencher un consentement
ni augmenter les scopes. Les diagnostics du SDK masquent les valeurs sensibles.

## Quotas Et Resultats

SQLite reserve la consommation avant chaque tentative, avec verrou interprocessus.
Valeurs par defaut : 100 tentatives de session et 60 appels par heure UTC,
configurables dans les limites locales. Ce sont des unites logiques, PAS des
paquets HTTP ni une estimation de facture du fournisseur.

Les tentatives se conservent apres redemarrage; un crash laisse un resultat
pending, pas un succes invente. Les echecs entrainent un cooldown de 15 secondes.
Une action envoyee au resultat inconnu n'est pas retentee automatiquement.
Le journal minimal contient serveur, outil, type de tentative, dates et resultat,
sans arguments, URL ni contenu utilisateur. Un journal corrompu bloque MCP,
sans bloquer les outils natifs Chrome/Windows.

## Integrations Restantes

Les services personnels doivent etre relies avec leurs pre-requis officiels,
client OAuth/scopes et couts documentes, puis testes sur comptes de test.
Le catalogue seul ne vaut pas authentification. Gmail, Calendar, Drive et
GitHub restent a connecter reellement. Aucune API personnelle WhatsApp MCP
n'est inventee; le parcours web reste generique dans Chrome habituel.
Le support d'une cle API HTTP supplementaire, le catalogue officiel detaille,
et les scenarios d'expiration/revocation de comptes reels restent ouverts.
