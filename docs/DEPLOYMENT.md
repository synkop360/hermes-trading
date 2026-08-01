# Deploiement Railway

## Projet actuel

- Projet Railway: `awake-abundance`
- Project ID: `f78f42d9-20ec-44f6-ae3f-f8bb79ee7000`
- Service: `hermes-trading`
- Service ID: `8b94cbc9-293e-4222-a8a7-698b656985f7`
- Volume: `hermes-trading-volume`
- Volume ID: `0ae9532a-b9cf-4df6-a911-f51d4b9daa6d`
- Mount path: `/app/state`

## Variables attendues

Variables de comportement:

```text
HERMES_TRADING_MODE=training
HERMES_TRADING_ASSETS=BTC/USDT,ETH/USDT,XRP/USDT,SHIB/USDT
HERMES_TRADING_I_ACCEPT_RISK=false
HERMES_TRADING_LIVE_CONFIRM=disabled
HERMES_TRADING_LIVE_VALIDATE_ONLY=true
HERMES_TRADING_ALLOW_REAL_ORDERS=false
HERMES_TRADING_MAX_ORDER_USDT=5
HERMES_NOTIFY_ENABLED=false
HERMES_NOTIFY_SERVICE=discord
HERMES_NOTIFY_MODE=verbose
HERMES_NOTIFY_ON_TRADES=true
HERMES_NOTIFY_ON_ORDERS=true
HERMES_NOTIFY_ON_ERRORS=true
HERMES_NOTIFY_ON_HEARTBEAT=false
HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=30
HERMES_NOTIFY_SUMMARY_FREQUENCIES=hourly
HERMES_NOTIFY_PROFITABILITY_FREQUENCIES=
EXCHANGE_ID=kraken
```

Variables sensibles:

```text
EXCHANGE_API_KEY
EXCHANGE_API_SECRET
DISCORD_WEBHOOK_URL
```

Ne jamais committer les secrets. Ils doivent rester dans Railway ou dans un `.env` local ignore par Git.

## Demarrage du container

Le `Dockerfile` installe `uv`, copie le package et le dossier `state`, puis lance:

```text
uv run python -m hermes_trading.run
```

Le `Dockerfile` definit `HERMES_TRADING_MODE=paper` comme valeur sure par defaut. Sur Railway, la variable `HERMES_TRADING_MODE=training` doit etre configuree explicitement pour correspondre au deploiement actuel.

Le code recree les fichiers manquants dans `/app/state` au demarrage. Si le volume existe deja, son contenu est conserve et masque la copie initiale du dossier `state`.

## Verification apres deploiement

Un demarrage sain affiche dans les logs une ligne de ce type:

```text
Booting hermes-trading worker for BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT in training mode
```

Le heartbeat distant attendu contient:

```json
{
  "status": "ok",
  "assets": ["BTC/USDT", "ETH/USDT", "XRP/USDT", "SHIB/USDT"],
  "mode": "training",
  "execution": "simulated",
  "order_count": 0,
  "live_validate_only": true
}
```

## Promotion live validation-only

Avant vrais ordres, commencer par Railway en validation-only:

```powershell
railway.cmd variables --set "HERMES_TRADING_MODE=live"
railway.cmd variables --set "HERMES_TRADING_I_ACCEPT_RISK=true"
railway.cmd variables --set "HERMES_TRADING_LIVE_CONFIRM=I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS"
railway.cmd variables --set "HERMES_TRADING_LIVE_VALIDATE_ONLY=true"
railway.cmd variables --set "HERMES_TRADING_ALLOW_REAL_ORDERS=false"
railway.cmd variables --set "HERMES_TRADING_MAX_ORDER_USDT=5"
railway.cmd up
```

Inspecter ensuite `/app/state/orders.jsonl`. Un resultat `status: "validated"` indique que Kraken a accepte la forme de l'ordre sans le placer.

En cas d'erreur de cycle, le heartbeat contient plutot `status: "error"`, `error` et `consecutive_failures`. Apres 5 erreurs consecutives, le worker ouvre son circuit breaker et s'arrete.

## Source de verite

Pour le worker deploye, la source de verite est le volume Railway `/app/state`.

Le dossier local `state/` sert de copie de travail et de point de depart. Il peut etre en retard par rapport au volume si Hermes ou le worker ont modifie les fichiers distants.
