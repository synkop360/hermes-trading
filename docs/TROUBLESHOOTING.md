# Depannage

## `strategy.yaml` local ne change pas

Cause probable: le worker deploye utilise le volume Railway `/app/state`, pas forcement le dossier local `state/`.

Verifier:

- le `strategy.yaml` du volume distant;
- `hypotheses.jsonl`;
- les logs du cron Hermes;
- le dernier `heartbeat.json`.

## Le worker tourne mais aucun trade n'apparait

C'est possible et normal si le RSI ne franchit pas le seuil.

Verifier plutot `decisions.jsonl`. Si les decisions augmentent avec `trade_closed=false`, le worker fonctionne.

Actions possibles:

- attendre plus de cycles;
- verifier `entry.threshold`;
- laisser la reflexion no-trade ajuster une seule variable;
- comparer les RSI observes dans `entry_value`.

## Cron Hermes en erreur `Model not found`

Cause probable: le job cron a garde un snapshot de modele devenu indisponible.

Verifier:

```powershell
hermes cron list
hermes cron runs 3db46486d61c
```

Le projet contient un watchdog sans agent a:

```text
scripts/hermes_watch.py
```

Ce watchdog evite la panne du modele Hermes global. Pour la reflexion LLM, il essaye les modeles declares dans `HERMES_REFLECTION_MODELS`, puis bascule vers l'API OpenAI si `OPENAI_API_KEY` ou `HERMES_OPENAI_API_KEY` est disponible. Si aucun LLM ne passe, il utilise la reflexion fallback locale.

## `heartbeat.json` reste sur `not_started`

Verifier que vous regardez le bon dossier:

- local: `state/heartbeat.json`;
- Railway: `/app/state/heartbeat.json`.

Si le local reste `not_started` mais Railway est `ok`, c'est simplement que le worker local n'a pas ete lance.

## Erreur Kraken sur une paire

Verifier que la paire existe chez Kraken via `ccxt`.

Exemple:

```powershell
uv run python -c "import asyncio; from hermes_trading.adapters.price import fetch; print(asyncio.run(fetch('SHIB/USDT'))['last'])"
```

Si la paire n'existe pas ou a un nom different, remplacer le symbole dans:

- `state/goal.yaml`;
- `HERMES_TRADING_ASSETS` sur Railway;
- le prompt Hermes si necessaire.

## Erreur adapter schema mismatch

Chaque adapter doit retourner `schema_version: 1`. Si le worker affiche `adapter schema mismatch`, verifier le code de l'adapter modifie ou la forme de la reponse externe avant de relancer.

## Circuit breaker apres 5 erreurs

Le worker incremente `consecutive_failures` dans `heartbeat.json` apres chaque cycle en erreur. A 5 echecs consecutifs, il s'arrete avec:

```text
circuit breaker opened after 5 consecutive failures
```

Corriger la cause dans le heartbeat ou les logs, puis redeployer ou relancer le worker.

## Mode live refuse

C'est attendu. Le code bloque le live tant qu'aucun adaptateur d'execution reelle n'est implemente.

Ne pas contourner cette securite en modifiant directement les garde-fous. Ajouter d'abord l'adaptateur live, les tests, les limites de risque et le kill switch.

## Ancien dossier source

Le projet actif est:

```text
E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading
```

Un ancien dossier vide peut rester a:

```text
C:\Users\thoma\hermes-trading
```

Il ne doit pas etre utilise comme source de verite.
