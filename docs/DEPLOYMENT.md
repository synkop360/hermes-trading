# Deploiement Railway

## Candidat paper du20octobre2026

Le Dockerfile local lance maintenant `hermes_trading.paper_boot --root /app/state`,
en mode `paper` avec les vrais ordres desactives. Il refuse les drafts et ne
demarre pas le writer historique. Le service Railway observe le20octobre utilise
encore `hermes_trading.colocated`, version327 en training; ce nouveau candidat
n'est pas deploye. Les [six conditions de lancement](PAPER_BETA_READINESS.md) et
le [cutover a approuver](PAPER_BETA_CUTOVER.md) gouvernent cette experimentation.
Les instructions et preuves plus anciennes ci-dessous restent historiques;
elles ne constituent pas une autorisation de trading reel ni des prerequis
supplementaires d'archivage ou de preuve de profit.

## Etat verifie le12octobre2026

Le service actuel utilise encore le deploiement du30aout et le worker historique en training sur des pairesUSDT. Le Dockerfile du depot lance `hermes_trading.colocated`. L'experimentation adaptative apparieeEUR reste locale et non activee. Le paquet candidat, ses empreintes, controles et procedure de reprise figurent dans `research/runs/paired-release-v1/RELEASE_AND_RECOVERY.md`; son Dockerfile isole lance `hermes_trading.paired_worker`. Le buildLinux a ete verifie localement; cela ne prouve pas son deploiementRailway. Ne pas utiliser les instructions live ci-dessous pour cette experimentation: elles decrivent une voie distincte, sans autorisation de vrais ordres. Capacite, couts, supervision et rapport de qualification apparie doivent etre verifies avant activation.

Verification locale ulterieure: le buildLinux du paquet candidat et149 tests cibles ont reussi (`research/runs/paired-container-v1`). Cela ne prouve ni le deploiementRailway ni l'activation ni la rentabilite.

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
HERMES_RAILWAY_MONTHLY_COST_USD=
HERMES_RAILWAY_SUBSCRIPTION_ALLOCATION_PCT=100
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
