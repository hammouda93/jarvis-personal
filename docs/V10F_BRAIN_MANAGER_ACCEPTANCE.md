# V10F — AI Brain Manager (état et recette)

## Objet
Extension additive du moteur V10E : ordre de fallback **Cerebras Primary → Cerebras Secondary → Groq → fournisseurs supplémentaires**. Les trois premiers restent pilotés par leur configuration historique. Ne pas merger sans tests Windows et revue des permissions.

## Fonctionnel dans le code
- `jarvis_agent/brain_cascade.py` : registre local versionné `brains.json`, configuration de 20 fournisseurs maximum, presets Gemini/OpenAI/xAI/OpenRouter/compatible OpenAI et stockage des secrets via Windows Credential Manager (avec variable d'environnement optionnelle).
- `jarvis_agent/brain_manager_qt.py` : interface pour ajouter, activer, ordonner, supprimer et déclencher un test de function calling (avec confirmation explicite d'une requête potentiellement payante).
- `jarvis_agent/agent_runtime.py` : fallback après les trois fournisseurs historiques, budget de contexte et contrôle de réponse complète. Aucun tool call n'est dispatché par le fallback lui-même.
- Protections récentes : lors de la compaction, le tool explicitement imposé reste disponible ; les fournisseurs suivants repartent d'un contexte initial commun ; une configuration illisible ne peut plus être écrasée depuis le panneau.

## Limites à résoudre avant production
- L'interface ne modifie pas les trois premiers cerveaux historiques ; les modèles additionnels restent dans un ordre postérieur.
- Le routage actuel utilise l'API OpenAI Chat Completions compatible, pas les contrats natifs complets Gemini/xAI ni les modèles locaux.
- Aucun plafond monétaire strict par fournisseur n'est encore garanti ; le budget est essentiellement un préflight de tokens estimés.
- Le mécanisme de consentement doit être étendu à tout contexte sensible transmis à un nouveau fournisseur ; le blocage de `_memory_scope_active` ne couvre pas tous les cas possibles.
- Des appels sous quotas réels peuvent avoir des comportements différents des mocks ; prévoir une recette ciblée sur Windows avec budget limité.

## Recette Windows (branche de développement uniquement)
Depuis `D:\\Django_Projects\\jarvis-main\\jarvis-main` :

```powershell
git status --short
git fetch origin
git switch feature/brain-cascade-configurable-v1
git pull --ff-only origin feature/brain-cascade-configurable-v1
python -m unittest discover -s tests -p 'test_brain_cascade_v10f.py' -v
python -m unittest discover -s tests -p 'test_provider_continuity_v10d.py' -v
python -m unittest discover -s tests -p 'test_provider_recovery_v10e.py' -v
python scripts/run_final_regression.py
```

**Attention** : conserver les changements locaux avant `git switch` et ne pas effacer les fichiers scheduler non commités. Ne pas utiliser `git reset --hard` ou `git clean -fd`.

## Scénarios manuels à valider
1. Sans cerveau supplémentaire, la chaîne historique fonctionne à l'identique.
2. Ajouter une entrée Gemini/Grok/OpenAI, enregistrer une clé, redémarrer Jarvis, tester avec une requête explicitement autorisée.
3. Vérifier `Primary 402 → Secondary 429 → Groq 429 → Extra` sans duplication d'une action modifiante.
4. Vérifier les refus de transfert de mémoire sensible et de mission nécessitant un tool non compatible.
5. Vérifier que réordonner les cerveaux additionnels ne modifie pas les trois premiers et que les clés n'apparaissent jamais dans les logs.
6. Vérifier Chrome, Windows UIA/CUA, saisie vocale et sauvegarde de fichier sur le PC réel.

## Statut
Les modifications de la branche sont des implémentations à valider, pas une preuve de validation Windows en situation réelle. La PR doit rester en brouillon jusqu'à obtention de la CI complète et de la recette utilisateur.
