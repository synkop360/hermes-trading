# Commandes utiles

Toutes les commandes ci-dessous partent du dossier projet:

```powershell
E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading
```

## Local

Lancer les tests:

```powershell
uv run python -m unittest discover -s tests
```

Demarrer le worker local en simulation:

```powershell
$env:HERMES_TRADING_MODE = "training"
$env:HERMES_TRADING_ASSETS = "BTC/USDT,ETH/USDT,XRP/USDT,SHIB/USDT"
uv run python -m hermes_trading.run --interval-seconds 60
```

Forcer les actifs sans modifier l'environnement:

```powershell
uv run python -m hermes_trading.run --assets "BTC/USDT,ETH/USDT,XRP/USDT,SHIB/USDT"
```

Lire la derniere sante locale:

```powershell
Get-Content .\state\heartbeat.json
```

Lire les dernieres decisions locales:

```powershell
Get-Content .\state\decisions.jsonl -Tail 20
```

Lire les derniers trades simules locaux:

```powershell
Get-Content .\state\trades.jsonl -Tail 20
```

Lancer une reflexion fallback locale:

```powershell
uv run python -m hermes_trading.reflect --fallback
```

Lancer une reflexion via la CLI Hermes:

```powershell
uv run python -m hermes_trading.reflect --hermes
```

Forcer le fallback OpenAI pour la reflexion:

```powershell
$env:HERMES_REFLECTION_MODELS = "openai:gpt-5.6-luna,openai:gpt-5.6-terra"
uv run python -m hermes_trading.reflect --hermes
```

Regenerer le graphe documentaire local:

```powershell
uv run python .\scripts\build_docs_graph.py
```

Tester les notifications:

```powershell
uv run python -m unittest tests.test_notifications -v
```

## Railway

Verifier le lien projet/service:

```powershell
railway status
```

Voir les logs du worker:

```powershell
railway logs
```

Redeployer:

```powershell
railway up
```

Lister les variables:

```powershell
railway variables
```

Activer les notifications Discord:

```powershell
railway.cmd variable set HERMES_NOTIFY_ENABLED=true --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SERVICE=discord --skip-deploys
railway.cmd variable set HERMES_NOTIFY_MODE=pnl_only --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=30 --skip-deploys
railway.cmd variable set HERMES_NOTIFY_SUMMARY_FREQUENCIES=hourly --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_ENABLED=true --skip-deploys
railway.cmd variable set HERMES_FUNDS_ALERT_MENTION=@here --skip-deploys
railway.cmd variable set DISCORD_WEBHOOK_URL=<webhook discord> --skip-deploys
railway.cmd up
```

Configurer les alertes fonds bas:

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

Le volume Railway persistant est monte sur `/app/state`. Les fichiers du volume doivent etre consultes comme fichiers distants, pas confondus avec le dossier local `state/`.

## Hermes

Lister les crons:

```powershell
hermes cron list
```

Voir la configuration du cron:

```powershell
Get-Content $env:LOCALAPPDATA\hermes\cron\jobs.json
```

Declencher le cron manuellement:

```powershell
hermes cron run 3db46486d61c
```

Verifier le gateway Hermes si necessaire:

```powershell
hermes gateway status
```
