# Operations

## Routine de surveillance

Verifier dans cet ordre:

1. Le worker tourne.
2. Le heartbeat est recent.
3. Les decisions augmentent.
4. Les trades fermes apparaissent si la condition RSI se declenche.
5. Hermes modifie la strategie seulement quand les seuils de reflexion sont atteints.

## Worker

Localement:

```powershell
Get-Content .\state\heartbeat.json
Get-Content .\state\decisions.jsonl -Tail 20
Get-Content .\state\trades.jsonl -Tail 20
Get-Content .\state\orders.jsonl -Tail 20
```

Sur Railway:

```powershell
railway logs
railway status
railway variables
```

Le signal minimal attendu dans les logs est:

```text
Booting hermes-trading worker for BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT in training mode
```

## Decisions sans trade

Une decision sans trade est normale quand le RSI ne passe pas sous le seuil `entry.threshold`.

Elle est ecrite dans `decisions.jsonl` avec:

```json
{
  "event": "decision",
  "trade_closed": false,
  "reason": "entry_condition_not_met"
}
```

Ce journal permet a Hermes de voir qu'une strategie ne trade pas assez, meme si `trades.jsonl` reste vide.

## Strategy.yaml local vs distant

Le fichier local:

```text
state/strategy.yaml
```

peut etre different du fichier distant:

```text
/app/state/strategy.yaml
```

Sur Railway, le volume distant est prioritaire. Si le fichier local semble ancien, verifier d'abord le volume distant ou les logs Hermes.

## Quand intervenir

Intervenir si:

- `heartbeat.json` ne change plus;
- `status` vaut `error`;
- `consecutive_failures` augmente;
- `decisions.jsonl` ne grandit plus;
- `orders.jsonl` contient une erreur de validation Kraken;
- Hermes ne voit pas les nouveaux fichiers du volume;
- `strategy.yaml` change sans entree correspondante dans `hypotheses.jsonl`.

## Live readiness

Verifier la configuration live sans demarrer un cycle complet:

```powershell
$env:HERMES_TRADING_MODE = "live"
$env:HERMES_TRADING_I_ACCEPT_RISK = "true"
$env:HERMES_TRADING_LIVE_CONFIRM = "I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS"
$env:HERMES_TRADING_LIVE_VALIDATE_ONLY = "true"
$env:HERMES_TRADING_ALLOW_REAL_ORDERS = "false"
uv run python scripts\check_live_readiness.py
```

Resultat attendu:

```text
mode=live executor=kraken validate_only=True
```

Ne passer a `HERMES_TRADING_ALLOW_REAL_ORDERS=true` qu'apres au moins un cycle validation-only avec `orders.jsonl` sain.

## Notifications

Les notifications sont desactivees par defaut.

Canal retenu: Discord webhook.

Activation:

```powershell
railway.cmd variable set HERMES_NOTIFY_ENABLED=true --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SERVICE=discord --skip-deploys
railway.cmd variable set HERMES_NOTIFY_MODE=pnl_only --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=30 --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SUMMARY_FREQUENCIES=hourly --skip-deploys
railway.cmd variable set HERMES_NOTIFY_PROFITABILITY_FREQUENCIES=[1d,1w] --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_ENABLED=true --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_MENTION=@here --skip-deploys
railway.cmd variable set DISCORD_WEBHOOK_URL=<webhook discord> --skip-deploys
railway.cmd up
```

Modes disponibles:

- `verbose`: trades, ordres et erreurs.
- `orders_only`: ordres seulement.
- `pnl_only`: gains/pertes seulement, sans trades flat.

Resumes periodiques disponibles:

- `HERMES_NOTIFY_SUMMARY_FREQUENCIES=hourly`: horaire.
- `HERMES_NOTIFY_SUMMARY_FREQUENCIES=12h`: demi-journalier.
- `HERMES_NOTIFY_SUMMARY_FREQUENCIES=1d`: journalier.
- `HERMES_NOTIFY_SUMMARY_FREQUENCIES=1w`: hebdomadaire.
- `HERMES_NOTIFY_SUMMARY_FREQUENCIES=1mo`: mensuel.
- `HERMES_NOTIFY_SUMMARY_FREQUENCIES=[12h,1w]`: plusieurs cadences en parallele.
- `HERMES_NOTIFY_PROFITABILITY_FREQUENCIES=[1d,1w]`: snapshots economiques net journaliers et hebdomadaires, distincts des resumes de trades.

Le resume par cycles et le resume horaire peuvent etre actifs ensemble:

```powershell
railway.cmd variable set HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=30 --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SUMMARY_FREQUENCIES=hourly --skip-deploys
railway.cmd up
```

Alertes fonds bas:

```powershell
railway.cmd variable set HERMES_FUNDS_ALERT_ENABLED=true --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_QUOTE=USDT --skip-deploys
railway.cmd variable set HERMES_FUNDS_MIN_AVAILABLE_USDT=20 --skip-deploys
railway.cmd variable set HERMES_FUNDS_MIN_ORDER_MULTIPLE=3 --skip-deploys
railway.cmd variable set HERMES_FUNDS_BUFFER_PCT=0.10 --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_COOLDOWN_SECONDS=21600 --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE=1.20 --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_MENTION=@here --skip-deploys
railway.cmd up
```

Verifier `state/funds_alert.json` si une alerte semble se repeter ou ne pas se resoudre.

Desactivation:

```powershell
railway.cmd variable set HERMES_NOTIFY_ENABLED=false --skip-deploys
railway.cmd up
```

Voir `docs/NOTIFICATIONS.md`.

## Redemarrage propre

Pour relancer le service Railway:

```powershell
railway up
```

Pour relancer localement:

```powershell
uv run python -m hermes_trading.run --interval-seconds 60
```

Rollback Railway immediat:

```powershell
railway.cmd variables --set "HERMES_TRADING_MODE=training"
railway.cmd variables --set "HERMES_TRADING_I_ACCEPT_RISK=false"
railway.cmd variables --set "HERMES_TRADING_LIVE_CONFIRM=disabled"
railway.cmd variables --set "HERMES_TRADING_LIVE_VALIDATE_ONLY=true"
railway.cmd variables --set "HERMES_TRADING_ALLOW_REAL_ORDERS=false"
railway.cmd up
```
