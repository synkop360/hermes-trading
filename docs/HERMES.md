# Supervision Hermes

## Role

Hermes surveille les fichiers du volume Railway et propose des ajustements de strategie. Le job actif est:

```text
3db46486d61c
```

Nom:

```text
hermes-trading-watch
```

Frequence:

```text
toutes les 30 minutes
```

Workdir:

```text
E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading
```

Miroir local utilise par le prompt:

```text
C:\Users\thoma\hermes-trading-config
```

## Fichiers surveilles

Hermes doit rapatrier ou lire:

- `/goal.yaml`
- `/strategy.yaml`
- `/heartbeat.json`
- `/trades.jsonl`
- `/decisions.jsonl`
- `/hypotheses.jsonl`

## Watchdog et selection de modele

Le cron actif utilise le script:

```text
scripts/hermes_watch.py
```

Ce watchdog synchronise d'abord le volume Railway `/app/state` vers `state/` et le miroir local, puis lance une reflexion uniquement si elle est justifiee.

Selection de modele pour `hermes_trading.reflect --hermes`:

- `HERMES_REFLECTION_MODELS`: liste prioritaire, par exemple `nous:z-ai/glm-5.2,openai:gpt-5.6-luna`.
- `HERMES_REFLECTION_MODEL` et `HERMES_REFLECTION_PROVIDER`: candidat unique si la liste n'est pas fournie.
- `HERMES_LOCAL_MODEL`: active un fallback local OpenAI-compatible, par exemple `ai/smollm2`.
- `HERMES_LOCAL_MODEL_PORT`: port du modele local, `12434` par defaut pour Docker Model Runner.
- `OPENAI_API_KEY` ou `HERMES_OPENAI_API_KEY`: active le fallback direct vers l'API OpenAI.
- `HERMES_OPENAI_FALLBACK_MODEL`: modele OpenAI de secours, `gpt-5.6-luna` par defaut.

Le fallback local utilise `http://127.0.0.1:<port>/engines/v1/chat/completions` et ne demande pas de cle API. Pour Docker Model Runner, il suffit donc de definir `HERMES_LOCAL_MODEL`; le port peut rester implicite.

Si tous les modeles LLM echouent, le module bascule sur la reflexion fallback deterministe locale et ajoute l'echec de selection dans `hypotheses.jsonl`.

## Conditions de reflexion

Le watchdog declenche une reflexion operationnelle dans deux cas:

- Reflexion de trade: au moins `reflection_every` nouveaux trades fermes.
- Reflexion de non-trade: au moins `HERMES_NO_TRADE_REFLECTION_DECISIONS` decisions sans trade ferme depuis la derniere hypothese, `500` par defaut.

En cas de drought no-trade par symbole, la reflexion donne priorite a l'accessibilite du seuil d'entree. Si toutes les decisions recentes observent un RSI superieur a `entry.threshold`, une proposition qui modifie seulement `position_size_r` ou `stop_loss_pct` ne peut pas debloquer de trade; le garde-fou remplace alors la proposition par une hausse controlee de `entry.threshold`.

Le plafond de cette hausse est configure par:

```text
HERMES_REFLECT_MAX_ENTRY_THRESHOLD=75
```

La regle de prudence reste: changer exactement une variable de strategie a la fois.

Variables supportees par `--hermes`:

- `entry.threshold`
- `stop_loss_pct`
- `position_size_r`

Une proposition Hermes peut cibler la strategie globale:

```json
{"variable":"entry.threshold","value":28,"reason":"..."}
```

ou une surcharge par symbole:

```json
{"asset":"SHIB/USDT","variable":"entry.threshold","value":22,"reason":"..."}
```

Dans le second cas, seule la branche `symbols.SHIB/USDT` est modifiee; le socle global reste intact.

Variables que le fallback local modifie actuellement:

- `entry.threshold`
- `stop_loss_pct`

## Commandes

Lister les crons:

```powershell
hermes cron list
```

Afficher le cron:

```powershell
Get-Content $env:LOCALAPPDATA\hermes\cron\jobs.json
```

Executer immediatement:

```powershell
hermes cron run 3db46486d61c
```

## A verifier regulierement

- Le cron est actif.
- Le prochain run est dans le futur.
- Le workdir pointe bien vers le dossier dans le vault.
- Le prompt mentionne les quatre actifs actuels.
- `strategy.yaml` distant evolue quand les conditions de reflexion sont atteintes.
- `hypotheses.jsonl` documente chaque changement.
