# Hermes Trading

[Goal progress — dashboard and remaining milestones](docs/GOAL_PROGRESS.md)

```text
[#############-------] 65% estimated
```

October 27: the paper beta retains its October 23 opening, but new entries remain halted after supervisor errors. All eight accounts had zero trades at the latest observation; profitability and learned improvement are unproven. The approved retry/recovery correction and simpler Discord formatting are local only. [Current evidence](research/runs/paper-supervisor-recovery-v1/README.md). Deployment and live acknowledgment need a separately reviewed release and approval.

Historical prelaunch evidence: the [October 21 local release](research/runs/paper-launch-gate-v1/release-final-verification.json) covered 145 test modules and 1,618 checks. Those results predate the installed beta and the current correction; they do not validate the new code.

The experiment retains eight virtual wallets, €200 primary, one adaptive writer, fixed control, reviews after five new closes, original costs and risk limits. Archive expansion stays deferred. The progress estimate measures engineering and prospective evaluation, not probability of profit.

[Paper beta readiness — launch gates, first results and profitability evidence](docs/PAPER_BETA_READINESS.md)

Hermes Trading est un worker de trading automatique en mode simule. Il observe plusieurs paires Kraken, ecrit chaque decision dans un journal local ou Railway, et laisse Hermes proposer des ajustements progressifs de strategie.

Configuration historique documentee (a reverifier avant le nouvel essai controle):

- Mode d'execution Railway: `training`, donc ordres simules uniquement.
- Mode par defaut du code si aucune variable n'est definie: `paper`, egalement simule.
- Actifs suivis: `BTC/USDT`, `ETH/USDT`, `XRP/USDT`, `SHIB/USDT`.
- Exchange par defaut: Kraken via `ccxt`.
- Deploiement: Railway, service `hermes-trading`, volume monte sur `/app/state`.
- Supervision: cron Hermes `hermes-trading-watch`, toutes les 30 minutes.

## Structure

```text
hermes_trading/
  run.py              # point d'entree du worker seul (demarrage local simple)
  paper_boot.py       # nouveau point d'entree Docker, essai paper explicite et configuration validee
  colocated.py        # ancien point d'entree Railway (worker + controle/Discord optionnels)
  loop.py             # boucle multi-actifs et decisions de trading
  reflect.py          # reflexion Hermes ou fallback local
  score.py            # scoring des performances
  adapters/           # prix, news, on-chain, macro
state/
  goal.yaml           # objectif et actifs suivis
  strategy.yaml       # strategie active
  heartbeat.json      # derniere sante du worker
  decisions.jsonl     # toutes les decisions, meme sans trade
  trades.jsonl        # trades simules fermes
  orders.jsonl        # tentatives d'execution simulees, validees ou soumises
  hypotheses.jsonl    # hypotheses de modification de strategie
  history/            # archives des versions de strategie
docs/
  ARCHITECTURE.md
  COMMANDS.md
  DEPLOYMENT.md
  GRAPHIFY_OBSIDIAN.md
  HERMES.md
  KRAKEN.md
  OPERATIONS.md
  PAPER_PILOT.md
  SAFETY.md
  STATE_FILES.md
  TROUBLESHOOTING.md
```

## Demarrage local

Depuis ce dossier:

```powershell
uv run python -m hermes_trading.run --interval-seconds 60
```

Pour forcer une liste d'actifs localement, utiliser la variable d'environnement ou l'option `--assets`:

```powershell
$env:HERMES_TRADING_MODE = "training"
$env:HERMES_TRADING_ASSETS = "BTC/USDT,ETH/USDT,XRP/USDT,SHIB/USDT"
uv run python -m hermes_trading.run
```

```powershell
uv run python -m hermes_trading.run --assets "BTC/USDT,ETH/USDT,XRP/USDT,SHIB/USDT"
```

`hermes_trading.run` lance uniquement la boucle de trading, sans API de controle ni bot Discord: pratique pour un test local rapide. Le `Dockerfile` de production lance `hermes_trading.colocated`, qui reutilise `run.ensure_state_files` et `loop.run_loop`, puis demarre en plus l'API de controle locale (si `HERMES_CONTROL_HMAC_SECRET` est defini) et le bot Discord (si les variables `HERMES_DISCORD_*` sont definies). Pour reproduire fidelement le comportement Railway en local, lancer `hermes_trading.colocated` plutot que `hermes_trading.run`.

## Verification rapide

```powershell
uv run python -m unittest discover -s tests
Get-Content .\state\heartbeat.json
Get-Content .\state\decisions.jsonl -Tail 20
```

## Pilote paper reproductible (huit comptes)

Le parcours principal utilise des prix Kraken publics sans authentification et
huit comptes simulés : adaptive/control × 50/100/150/200 EUR. Depuis la racine :

```bash
uv sync --python 3.11 --locked
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/public --init-only --experiment-id lat28-public
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/public --run --experiment-id lat28-public \
  --max-cycles 1 --max-runtime-seconds 120 --interval-seconds 60
```

Choisir une racine neuve à chaque session. Le fallback adaptive est local et
automatique ; les control restent fixes. Aucun ordre réel ni appel LLM.
Une observation peut finir sans trade et reste alors inconclusive.
Voir [la procédure complète](docs/PAPER_PILOT.md) pour l'installation séparée,
le rejeu hors ligne du paquet public exact, les huit sorties et les limites de
valorisation. La [preuve LAT-34](docs/LAT34_PUBLIC_PROOF_RESULTS.md) rapporte
six entrées/six clôtures en rejeu historique (~10 min), pas en temps réel ;
les deux comptes 50 EUR restent sans trade et les frais sont nuls.
L'ancien `paper_offline` utilise une fixture synthétique et ne constitue pas
cette preuve publique.

## Reflexion

Le module `hermes_trading.reflect` peut mettre a jour `state/strategy.yaml`:

- `--fallback`: logique locale basee sur les trades fermes.
- `--hermes`: proposition JSON demandee a la CLI `hermes`.

Dans les deux cas, la strategie precedente est archivee dans `state/history/` et un changement est ajoute a `state/hypotheses.jsonl`.

## Documents utiles

- `docs/OPERATIONS.md`: gestes quotidiens pour surveiller le flux.
- `docs/NOTIFICATIONS.md`: activation Discord webhook pour alertes.
- `docs/KRAKEN.md`: liaison Kraken, permissions API, mode training.
- `docs/HERMES.md`: cron Hermes et logique de reflexion.
- `docs/DEPLOYMENT.md`: Railway, volume et variables.
- `docs/SAFETY.md`: garde-fous avant tout live trading.
- `docs/GRAPHIFY_OBSIDIAN.md`: liens vers le graphe documentaire genere.
