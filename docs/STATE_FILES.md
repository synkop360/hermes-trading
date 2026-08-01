# Fichiers d'etat

## goal.yaml

Definit l'objectif et les actifs suivis.

Champs principaux:

- `asset`: actif historique principal.
- `assets`: liste multi-actifs active.
- `target_return_30d`: objectif de rendement.
- `max_drawdown`: drawdown maximum tolere.
- `min_sharpe`: Sharpe minimal cible.
- `failure_below`: seuil de performance faible.
- `reflection_every`: nombre de trades fermes avant reflexion.
- `one_variable_only`: force une modification a la fois.

## strategy.yaml

Strategie active.

Exemple actuel:

```yaml
entry:
  indicator: rsi
  direction: long
  threshold: 26
position_size_r: 1.0
stop_loss_pct: 0.5
version: "56"
symbols:
  BTC/USDT:
    entry:
      threshold: 28
    position_size_r: 1.0
  ETH/USDT:
    entry:
      threshold: 27
    position_size_r: 1.0
  XRP/USDT:
    entry:
      threshold: 24
    position_size_r: 0.7
  SHIB/USDT:
    entry:
      threshold: 22
    stop_loss_pct: 0.8
    position_size_r: 0.4
```

Le bloc racine reste la strategie globale par defaut. Le bloc optionnel `symbols` surcharge seulement les champs precises pour un actif donne; les champs absents heritent de la strategie globale.

Variables modifiables aujourd'hui:

- `entry.threshold`
- `stop_loss_pct`
- `position_size_r`

Hermes peut modifier une variable globale ou, si une proposition contient `asset`, une variable dans `symbols.<asset>`. Dans les deux cas, une seule variable doit changer par hypothese.

## heartbeat.json

Dernier etat du worker.

Champs attendus:

- `timestamp`: debut du dernier cycle.
- `assets`: actifs evalues.
- `strategy_version`: version de strategie lue.
- `mode`: `paper` ou `training`.
- `execution`: `simulated`.
- `status`: `ok` ou `error`.
- `decision_count`: decisions produites pendant le cycle.
- `closed_trade_count`: trades fermes pendant le cycle.
- `trade_closed`: `true` si au moins un trade a ete ferme pendant le cycle.
- `economic_gate`: verdict economique courant, avec `status` (`passed` ou
  `blocked`), `reasons`, `net_pnl_usd`, `projected_net_30d_usd` et
  `stressed_net_pnl_usd`. Ce champ est informatif en training et ne peut pas
  activer les ordres reels.

En erreur, le heartbeat garde `timestamp`, `assets`, `mode`, `execution` et ajoute:

- `error`: message d'erreur du cycle.
- `consecutive_failures`: nombre d'erreurs consecutives depuis le dernier cycle sain.

## decisions.jsonl

Journal de toutes les decisions. Chaque ligne contient aussi l'actif, la version de strategie, le mode, l'indicateur d'entree, la valeur RSI observee et le seuil utilise.

Chaque ligne est un JSON autonome. Une decision sans trade contient:

```json
{"event":"decision","trade_closed":false,"reason":"entry_condition_not_met"}
```

Une decision avec trade ferme contient en plus `entry_price`, `realized_pnl_pct`, `equity` et `side`:

```json
{"event":"trade","trade_closed":true,"realized_pnl_pct":0.001,"equity":100.1}
```

## trades.jsonl

Journal des trades simules fermes uniquement. Ce fichier peut rester vide longtemps si la strategie n'entre pas en position.

## orders.jsonl

Journal des tentatives d'execution.

En mode `training`, les ordres restent virtuels:

```json
{"status":"simulated","asset":"BTC/USDT","side":"buy"}
```

En mode `live` validation-only, Kraken valide l'ordre sans le soumettre:

```json
{"status":"validated","asset":"BTC/USDT","validate_only":true}
```

En vrais ordres, le statut attendu devient:

```json
{"status":"submitted","asset":"BTC/USDT","validate_only":false}
```

## notification_summaries.json

Memoire locale des resumes periodiques Discord deja publies.

Le worker y stocke la derniere fenetre envoyee pour chaque frequence active, par exemple:

```json
{
  "12h": "12h:2026-07-28T00:00:00+00:00",
  "1w": "1w:2026-07-20T00:00:00+00:00"
}
```

Ce fichier evite de republier le meme resume apres un redemarrage Railway. Il peut etre supprime pour forcer un nouveau bilan de la derniere periode terminee.

## funds_alert.json

Memoire locale de l'alerte fonds bas Discord.

Etat initial:

```json
{"status":"ok"}
```

Apres une alerte:

```json
{
  "status": "warning",
  "last_alert_at": "2026-07-29T09:00:00+00:00",
  "last_available": 14.82,
  "low_limit": 20.0,
  "critical_limit": 5.0,
  "resolve_limit": 24.0
}
```

Ce fichier evite de repeter la meme alerte avant `HERMES_FUNDS_ALERT_COOLDOWN_SECONDS`. Il garde aussi la derniere severite pour envoyer immediatement une montee de `WARNING` vers `CRITICAL` ou un message `RESOLVED`.

## hypotheses.jsonl

Journal des hypotheses et changements proposes par Hermes ou par le fallback local.

Chaque entree doit expliquer:

- le moment;
- le mode de reflexion;
- la variable changee;
- l'ancienne valeur si connue;
- la nouvelle valeur;
- la raison.

## history/

Archive des anciennes strategies avant modification:

```text
history/v0001.yaml
history/v0002.yaml
```
