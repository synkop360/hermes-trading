# Hermes Trading

[Goal progress — dashboard and remaining milestones](docs/GOAL_PROGRESS.md)

```text
[#############-------] 65% estimated
```

Estimate of overall engineering and forward-evaluation progress; updated at milestones. Paper has passed two complete elapsed days; coverage is incomplete and profitability is unproven. The goal is to launch safe paper trading promptly; archive expansion, full capacity, installed archive recovery and profitability proof are deferred.

**Goal status: active; snapshot repair installed, reporting operating, new entries halted.** Owner-approved source `20eb6af` is installed on Railway and the PC. At October 26 17:17 UTC, the worker is healthy, ordinary scheduled supervision has succeeded, and all eight account reports match one finalized settlement after five new observations. Discord confirms a fresh summary (195 windows total). Original clocks, history, fees, controls and risk limits are preserved; the supervisor entry halt remains. The interruption adds a recorded 10h 7m 44s gap to readiness and one reporting gap/restart. Initial degraded and failed results remain evidence. Primary native value is EUR199.3383 after EUR0.6617 recorded costs, with zero trades or learning outcome. [Execution and limitations](../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/LIVE_CUTOVER_RESULT.md). The next actual new E: daily copy is pending October 27 at00:15UTC; the verified October 26 copy predates this cutover. Overall stays65%.

Historical preparation before deployment: at October 25 19:39 UTC, [read-only diagnosis](../hermes-paper-operational-corrections/research/runs/paper-supervisor-diagnostic-v1/README.md) confirmed one cause of intermittent supervisor snapshot failures: SQLite BUSY (5) during a zero-wait database read. The owner approved disabled local implementation. [Candidate `a79fb93`](../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/README.md) passes 20 exact Windows package checks and 21 Linux checks; the earlier affected run passed 139 with one platform skip. Candidate20eb6af is ready for paper-only deployment review: all1,712correctness catalog cases have passing evidence through the full run, restored-input rerun and Linux platform checks. The original failed full run is retained; this is composed evidence, not one fresh passing suite. No deployed code or entry halt changed. The estimate remains 65%.


Latest October26 06:25UTC [read-only observation](../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/FORWARD_OBSERVATION_20261026.md):187confirmed summary windows, original opening/eight accounts/common settlement verified, no orders/positions,38.5%volume. The supervisor entry halt remains despite fresh successful health. The [cutover proposal](../hermes-paper-operational-corrections/docs/PAPER_SUPERVISOR_SNAPSHOT_REPAIR_CUTOVER.md) awaits approval and would retain that halt.

The [20:24 UTC observation](../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/FORWARD_OBSERVATION_20261025.md) records 84 confirmed Discord summary windows, no reported delivery gaps, original account hashes and no orders/positions in all eight wallets. Storage is 35.7% full. Primary native reported equity is approximately EUR199.54 after estimated costs, with no trading profit. The common journal was busy, so this capture does not qualify an adaptive/control comparison. New entries remain halted.

Historical October 24 deployment `4d691167-362f-422a-8e72-5256ca46775d` replaced the original paper worker after exact owner approval and checked release-binding transitions on both hosts. The only runtime change permits `+` in snapshot names, fixing the `lost+found` daily-backup failure. The original experiment, clocks, eight accounts, fees and risk limits remain intact. The legacy watcher stays paused. PC lifecycle records zero model attempts reserved; the first review is not due before five new closes. The separate local effective-stop correction remains excluded. Credential rotation remains owner-managed.

The cutover stop and an agent-usage interruption left a gap from about10:40 until readiness at16:41UTC. Costs and clocks continued. The first manual successor supervisor failed before later manual and scheduled successes; failures and inactivity remain recorded. One verified backup is not proof of recurring backup reliability or a live restore drill. Profitability and learned improvement remain unproven.

The [actual eight-account report](../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/forward-report-20261024/README.md) confirms journal bindings and records a6h1m15.689-second gap. This exceeds the frozen300-second coverage tolerance, so the original continuous180-day qualification cannot pass that requirement. Operational learning and the original November22 day30 checkpoint continue with incomplete path coverage declared; no clock reset or replacement qualification protocol has been applied. The latest17:01UTC activity sample has51 `entry_condition_not_met` decisions per account and no trades, while entry admission is enabled.

[Discord silence diagnosis](docs/PAPER_DISCORD_NOTIFICATION_DIAGNOSIS.md): the paper migration omitted its reporting sender. The approved repair is deployed as `3cf55164-902f-4493-9a8a-6c97d394c0b8`, with eight-wallet reports, EUR 200 primary, native cost quality and retained safety latches. It passes 164 exact-export Windows checks and52 qualified Linux checks; full discovery has two archive fixture deadline errors and 30 unrun cases, so the full suite is not passing. Startup and first cycle-summary delivery are confirmed locally; remote message IDs/bodies are not retained. No real orders, paid calls or strategy changes were introduced.

The [original release verification](research/runs/paper-launch-gate-v1/release-final-verification.json) is preserved. The successor full Windows run records1,639passes, five platform skips and one existing disabled-archive deadline error, reproduced on the original approved release. Exact-export native composition and the five selected Linux guards pass; this is not a passing full-suite claim. [Successor verification](../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/VERIFICATION.md).

Six launch gates remain tracked: approved installed candidate; taker execution with declared public fees (maker0.004/taker0.008/slippage0.001); France/EUR markets with EUR 200 primary and EUR50/100/150 comparisons; one writer with reviews after five unique closes, `no_change`, at most one variable, archived hypotheses and fixed control; minimal70%/500MB volume alert, bounded logs and daily backup; net equity, costs, drawdown and daily Sharpe with entry-only safety halts. Deployment, original opening, accounting, scheduled supervision and an actual off-host backup are observed. Forward reliability and profitability require more evidence. Archive/checkpoint expansion stays disabled. Further deployment, paid actions, private exchange credentials and risk-limit changes require user approval; commits are authorized.

[Paper beta readiness — launch gates, first results and profitability evidence](docs/PAPER_BETA_READINESS.md)

[First forward observation — October 23, 20:45 UTC](research/runs/paper-activation-v1/FIRST_FORWARD_OBSERVATION.md): 28 completed common batches, three recorded `entry_condition_not_met` decisions per account, no orders or positions, and EUR 199.994944 primary net equity under the declared expense estimates. Adaptive/control differences are zero; rolling 30-day metrics and daily Sharpe remain incomplete.

[Paper beta operations — verified backup action and all-eight reporting recipe](docs/PAPER_BETA_OPERATIONS.md). A [read-only all-eight operator report](research/runs/paper-operator-report-v1/README.md) produces JSON/Markdown and has16focused passing checks plus independent review. The supervisor and backup schedules are enabled and both have actual successful operation. Progress is65%, with one completed elapsed day, incomplete coverage and an active supervisor entry halt.

[Wallet cost and sizing envelope](docs/PAPER_WALLET_COST_ENVELOPE.md): conditional arithmetic finds €6.90/30days declared hosting and a hosting-only 3% halt after about26.09days for an inactive €200 primary wallet. Stress admission also reduces usable order sizes. These bounds establish no profitable minimum wallet; all approved runtime inputs remain unchanged.

[Dated public minimum-size observation](docs/PAPER_PUBLIC_FEASIBILITY.md): October 21, 21:19 UTC public EUR quotes rule out entries for €50/100 under the frozen stress-risk budget, even before stop distance. €150/200 are not ruled out by these preliminary bounds; actual trade feasibility and profitability remain unproven. All scenarios are retained. Activation used separately refreshed native inputs on October 23; this earlier observation remains historical.

[Frozen strategy and LLM review](docs/PAPER_STRATEGY_ECONOMICS_REVIEW.md): four synthetic checks reproduce an accepted percentage-stop proposal with no effect while ATR stops govern. Combined payoff/minimum-size analysis also narrows wallet feasibility under explicit dated assumptions. This independent analysis changes no approved runtime, starts no trial and establishes no profitable wallet.

A [local correction](research/runs/paper-effective-stop-fix-v1/progress.md) is prepared separately as `3e785e1`: ineffective percentage-stop proposals are rejected at review and publication while genuine percentage stops remain supported. The October23 composed offline regression covers1,622 checks:1,618 passes and4Windows/platform skips. The original failed full run is preserved; an11-test rerun resolves nine setup errors after restoring57unchanged historical fixture files, with no cleanup warnings. This is composed coverage, not a single passing full run. Independent code review has no findings. The branch remains local; approved runtime `6c9d520` and its 364 pins remain unchanged.

The subsequent [four-case Linux verification](research/runs/paper-effective-stop-fix-v1/local-posix-check-20261023/cross-platform-composed-verification.json) passes every previously skipped ID using an already cached image, read-only source, unprivileged execution and no networking. All 1,622 unique catalog IDs have passing evidence across three correctness runs. This does not claim one passing full-suite invocation or full Linux release coverage. That separate local verification made no provider call or trial activation; the approved parent remained unchanged. The later approved deployment is described above.

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

`hermes_trading.run` lance uniquement l'ancienne boucle simple, sans API de controle ni bot Discord. Le `Dockerfile` approuve lance maintenant `hermes_trading.paper_boot`, point d'entree de la beta paper a huit scenarios. Son installation Railway et son ouverture sont verifiees depuis le 23 octobre; le correctif de sauvegarde du24octobre a leve ce blocage lors de la verification native de16:54UTC. Voir [la progression actuelle](docs/GOAL_PROGRESS.md). L'ancien point d'entree `hermes_trading.colocated` reste conserve pour reference historique; il ajoute les integrations optionnelles de controle et Discord a l'ancienne boucle.

## Verification rapide

```powershell
uv run python -m unittest discover -s tests
Get-Content .\state\heartbeat.json
Get-Content .\state\decisions.jsonl -Tail 20
```

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
