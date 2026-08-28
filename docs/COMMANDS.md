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

Epingler un modele de reflexion fixe (au lieu de la rotation auto-select puis
pool de modeles gratuits) -- les deux variables sont requises ensemble ; sans
elles le comportement est inchange, et en cas d'echec le repli local/OpenAI
reste actif :

```powershell
$env:HERMES_REFLECTION_PINNED_PROVIDER = "openrouter"
$env:HERMES_REFLECTION_PINNED_MODEL = "some-provider/some-model:free"
uv run python -m hermes_trading.reflect --hermes
```

Activer la reflexion deterministe in-process (tourne dans le worker Railway
lui-meme, sans CLI `hermes` ni cle API -- filet de securite si le cron externe
`hermes-trading-watch` s'arrete) :

```powershell
railway.cmd variable set HERMES_REFLECTION_INPROCESS_EVERY_CYCLES=20 --skip-deploys
railway.cmd up
```

Alerte Discord si la reflexion ne tourne plus (active par defaut) :

```text
HERMES_REFLECTION_STALE_ALERT_ENABLED=true          # false pour desactiver
HERMES_REFLECTION_STALE_ALERT_HOURS=36              # seuil avant alerte
HERMES_REFLECTION_STALE_ALERT_COOLDOWN_SECONDS=21600  # 6h entre rappels
```

`score`/`consecutive_losses` (les metriques de jugement de performance utilisees pour
tightener/loosener une entree) sont bornees au "regime" courant -- les trades clotures
avant `reflection-regime-started-at.txt` (state/) n'y comptent plus, meme si `total_trades`
(l'eligibilite no-trade-drought) continue de voir tout l'historique. Sans ca, une longue
serie de pertes sous un indicateur remplace depuis longtemps peut figer `score` au plancher
et pousser reflect a durcir un actif indefiniment sans jamais lui laisser rejouer sa
configuration actuelle. Le marqueur est cree automatiquement (valeur = maintenant) au premier
cycle apres deploiement de cette fonctionnalite -- donc aucune action requise pour l'activer.
Apres tout changement structurel (indicateur entry/exit, direction), reinitialiser le marqueur :

```powershell
uv run python .\scripts\mark_reflection_regime.py
```

Override explicite (prioritaire sur le fichier) :

```text
HERMES_REFLECTION_REGIME_STARTED_AT=2026-08-18T00:00:00+00:00
```

Enrichir le prompt de reflexion par symbole avec l'avis d'un modele de "market analysis"
local (Docker Model Runner, `hermes_trading/adapters/market_analysis.py`) : desactive par
defaut, purement consultatif -- l'avis est ajoute au prompt LLM et journalise dans
`hypotheses.jsonl` (`market_analysis`), mais n'influence jamais la decision d'entree/sortie
live (`loop.py`). Le modele par defaut est petit (~1B parametres) et non verifie ; un premier
test a produit une reponse incoherente (bias en desaccord avec son propre resume), donc a
traiter comme un avis de plus a relire, pas un signal fiable. Ne fonctionne que si Docker
Model Runner tourne sur la meme machine que le processus `reflect.py` qui l'appelle -- donc
uniquement via le cron local `hermes-trading-watch` (`--hermes`), jamais via
`HERMES_REFLECTION_INPROCESS_EVERY_CYCLES` sur Railway (le conteneur n'a pas acces au
Docker Model Runner de la machine locale) :

```text
HERMES_MARKET_ANALYSIS_ENABLED=true
HERMES_MARKET_ANALYSIS_MODEL=huggingface.co/ewinregirgojr/minicpm5-mtp-gguf-stock-analyst:Q8_0  # defaut
HERMES_LOCAL_MODEL_PORT=12434                        # deja utilise par HERMES_LOCAL_MODEL
HERMES_MARKET_ANALYSIS_TIMEOUT_SECONDS=30
```

Chaque appel reellement tente (succes ou erreur) est aussi journalise dans
`state/market_analysis.jsonl` (`{timestamp, asset, status, ...}`), independamment du fait
qu'un changement ait ete applique -- `hypotheses.jsonl` ne porte `market_analysis` que sur les
cycles "changed", donc un appel qui a eu lieu mais n'a rien produit (model_failed,
proposal_rejected) y restait invisible. Verifier rapidement le dernier appel reel :

```powershell
Get-Content .\state\market_analysis.jsonl -Tail 1
```

### Croissance / volume (2026-08-20)

Quatre changements lies au strategy.yaml, deployes ensemble :

- `entry.fee: maker` -- les entrees peuvent desormais etre au tarif maker au lieu de taker,
  reduisant le round-trip cost plancher (`positions.py::_is_maker_entry`, defaut `taker` si absent).
- `entry.require_ema_rising` / `entry.trend_ema_period` -- filtre deja implemente dans
  `strategy.py::_rsi_recovery_matches` mais jamais configure jusqu'ici ; n'accepte une entree
  rsi_recovery que si l'EMA(periode) est en hausse.
- `exit.trailing_atr` -- nouveau : remplace le take_profit_atr fige a l'entree par un stop qui
  ne monte que vers le haut (`PositionEngine._trail_stop`, ratchete a chaque cycle "hold").
  `take_profit_atr` reste configure en parallele mais sert uniquement de proxy pour le
  pre-trade cost gate (`_expected_payoff_covers_costs`), plus de plafond de sortie reel.
- **Compounding** : `simulation.risk_per_trade_pct` -- la taille d'entree en mode
  training/simule suit desormais `equity * risk_per_trade_pct * position_size_r / stop_fraction`
  (meme formule que `risk.py::RiskEngine.authorize_entry` en live), au lieu d'un
  `quote_notional` fixe pour toujours. `equity` = `compute_virtual_wallet_balance` (deja calcule
  une fois par cycle dans `loop.py`), passe a `PositionEngine(..., equity=...)`. Plancher $1 /
  plafond 50% de l'equity par position. `position_size_r` n'est donc plus inerte en training mode.

`target_return_30d` dans `goal.yaml` est a `0.20` (20%/mois) -- ambitieux mais volontairement
pas au niveau de l'objectif $50->$100 puis $100->$500 discute, qui impliquerait un risque de
ruine juge trop eleve pour cette config.

### Gestion du risque de perte (2026-08-26)

Diagnostic d'une serie de pertes le 2026-08-25 (les 5 actifs suivis ont baisse ensemble sur
une fenetre de 4h, declenchant plusieurs stops corrigenes) : deux changements bases sur un
replay des trades reels (candles Kraken via ccxt, logique de `strategy.py`/`positions.py`
rejouee telle quelle) plutot que sur une intuition.

- `exit.stop_atr` : `2.0` -> `1.2`. Teste par balayage (2.0/1.5/1.2/1.0/0.8) sur les 23 trades
  fermes depuis le 2026-08-20 -- resserrer le stop reduit la taille de chaque perte sans jamais
  toucher un trade gagnant (aucun gagnant n'a jamais reapproche son stop dans cet echantillon).
  A l'inverse, relever `exit.threshold` (55 -> 65/70, l'idee etant de "laisser courir" les
  gagnants) a ete teste et rejete : ce sont des rebonds courts, pas des tendances, et laisser
  la position ouverte plus longtemps rendait le gain plutot que l'augmenter.
- `simulation.max_concurrent_positions: 2` (nouveau) -- plafond portefeuille du nombre de
  positions ouvertes simultanement, tous actifs confondus (`positions.py::PositionEngine.
  _exposure_cap_reached`, verifie uniquement sur un signal `enter`, ne bloque jamais un
  exit/stop/trailing). Mirroir training-mode de `risk.py::RiskLimits.max_total_exposure_pct`
  qui n'existait jusqu'ici qu'en mode live. Valeur choisie par replay chronologique (cap
  1/2/3/4/5) des memes 23 trades : le baseline n'a jamais depasse 2 positions simultanees
  (cap=2 ne change rien pour lui), le consultant a atteint 3 (LINK+SOL+ETH) pendant la baisse
  du 25 aout -- cap=2 bloque exactement cette 3e position. cap=1 backteste mieux mais sur un
  seul evenement correle (quasi tout le gain vient du fait d'etre "occupe ailleurs" pendant le
  25 aout) et interdirait toute position simultanee sur les 5 actifs, ce qui va a l'encontre de
  l'objectif de frequence de trade discute le meme jour -- ecarte comme probablement surajuste.
  Raison de decision loggee dans `decisions.jsonl` : `exposure_limit`.

**Correction post-implementation (meme jour) :** le replay ci-dessus rejouait seulement les 23
trades reels deja survenus, en ne re-simulant que la sortie avec un stop plus serre -- l'entree
restait figee. Une fois `stop_atr: 1.2` et `max_concurrent_positions: 2` reellement implementes
(`positions.py`), un second replay plus rigoureux a rejoue tout l'historique Kraken 15m
disponible (7.5 jours, plafond reel de l'endpoint OHLC quel que soit le `since` demande) a
travers le vrai `PositionEngine`/`evaluate_strategy` (pas une reimplementation), en regenerant
les entrees depuis zero pour 4 variantes (ancien, stop seul, cap seul, les deux). Resultat
oppose a l'intuition initiale : `max_concurrent_positions: 2` se confirme (seul, la meilleure
variante : -0.50 -> -0.22 de pnl total, taux de reussite 58.5% -> 62.9%), mais `stop_atr: 1.2`
degrade en fait le resultat (seul : -0.50 -> -0.79 ; combine avec le cap : -0.22 -> -0.62). En
figeant les entrees, le premier replay ne pouvait jamais voir les trajectoires de prix qui
retouchent un stop a 1.2 ATR avant de continuer vers ce qui aurait ete un vrai gagnant a 2.0
ATR -- un biais de survivance invisible sur un echantillon deja compose de trades qui avaient
justement survecu au stop d'origine. `exit.stop_atr` revert a `2.0` (version '187' baseline /
'166' consultant) ; `max_concurrent_positions: 2` reste en place. Lecon methodologique : rejouer
uniquement la sortie sur des entrees reelles figees est un test plus faible qu'une simulation
en boucle fermee qui regenere les entrees sous chaque configuration.

Bug annexe decouvert pendant ce second replay (sans lien avec le tuning ci-dessus, mais qui
bloquait le replay lui-meme) : `hermes_trading/loop.py::_load_yaml` et
`hermes_trading/reflect.py::_load_yaml` appelaient `path.read_text()` sans `encoding="utf-8"`
explicite. Sur Windows, `read_text()` retombe sur `locale.getpreferredencoding()` (`cp1252` ici),
qui decode un BOM UTF-8 (ex. introduit par un `Get-Content -Raw | ssh "cat > ..."`) en
mojibake au lieu de le retirer, corrompant silencieusement la toute premiere cle du YAML (`entry`
disparaissait, `resolve_asset_strategy` retombait alors sur le seul override du symbole). Sans
effet sur le service Railway deploye (locale UTF-8 sous Linux, confirme par des trades reels
continus pendant toute la periode), mais un piege reel pour toute execution locale sur cette
machine -- corrige dans les deux fonctions (`encoding="utf-8"` explicite), regression testee
(`tests/test_training_loop.py::LoadYamlTests`, `tests/test_score_reflect.py::
ReflectLoadYamlTests`), et le BOM existant retire de `state/strategy.yaml`.

### Comparaison "baseline vs consultant"

Fait tourner une seconde instance locale, entierement independante de Railway (etat, wallet
virtuel de $50, trades separes), avec le "market analysis" active, pour comparer face-a-face
contre le baseline Railway (marche identique, seul le consultant differe). Mis en place le
2026-08-18 : `state/` = baseline (Railway, wallet reinitialise), `state-consultant/` = variante
consultant (seedee avec la meme `strategy.yaml`/`goal.yaml` au moment du split).

Lancer la boucle de trading de l'instance consultant (terminal a part, doit rester ouvert) :

```powershell
$env:HERMES_STATE_DIR = "state-consultant"
uv run python -m hermes_trading.run
```

Lancer le watcher de reflexion (cron-like) qui declenche `reflect.py --hermes` (le chemin LLM,
pas le trigger deterministe in-process qui ignore le contexte `market_analysis`) -- terminal a
part egalement. Il appelle `reflect.py --hermes` sans condition toutes les 15 minutes par
defaut (meme cadence observee cote Railway : `HERMES_REFLECTION_INPROCESS_EVERY_CYCLES=20`
appelle `run_hermes_reflection()` sans condition elle aussi, en laissant le cooldown interne
par actif (`ReflectionPolicy`) decider quoi changer). Ne pas ajouter de pre-filtre local
base sur un seuil de preuves accumulees : une premiere version de ce script le faisait et
ne reflechissait qu'une fois par heure environ au lieu de 15 min, faussant la comparaison :

```powershell
uv run python .\scripts\watch_consultant.py
```

Comparer les deux wallets a tout moment (ne necessite pas que les boucles ci-dessus tournent
au moment de la lecture) :

```powershell
MSYS_NO_PATHCONV=1 uv run python .\scripts\sync_railway_state.py  # rafraichir state/ (baseline) d'abord
uv run python .\scripts\compare_wallets.py
```

### Troisieme instance "trailing-exit" (2026-08-26)

Teste en conditions reelles `exit.trailing_overrides_explicit_exit` (nouveau, opt-in,
`strategy.py::evaluate_strategy`) : quand `trailing_atr` est configure, `explicit_exit`
(RSI >= threshold) ne ferme plus jamais la position -- seuls le stop (continuellement
ratchete par le trailing) et `max_bars` peuvent la clore. Motive par un constat sur les vrais
trades : `explicit_exit` gagne toujours la course contre le trailing stop (100% des gains
fermes via `explicit_exit`, 0% via un stop ratchete), plafonnant chaque gagnant a la cible
RSI quelle que soit la valeur de `trailing_atr`. Prototype rejoue contre l'historique Kraken
reel disponible (7.5 jours) via le vrai `PositionEngine`/`evaluate_strategy` (monkeypatch,
pas une reimplementation) : premiere variante du tuning de cette session a passer d'une perte
nette a un gain net (-0.22 -> +0.18 de pnl total), payoff ratio 0.385 -> 1.809 (avg win x3.8,
avg loss plus petit). Cout reel : taux de reussite 62.9% -> 40.6%, plus longue serie de pertes
5 -> 9 d'affilee -- le drawdown max reste petit dans les deux cas (0.53% / 0.72%, wallet et
fenetre trop petits pour vraiment stresser le plafond `max_drawdown: 0.08`), mais la
"streakiness" pourrait faire reagir `reflect.py` (durcissement automatique sur une serie de
pertes) meme si la strategie reste profitable en moyenne -- exactement ce que cette troisieme
instance sert a verifier en conditions reelles, separement du consultant (qui teste le "market
analysis", une variable differente).

`state-trailing-exit/` : seedee avec la meme config que baseline/consultant au moment du split
(`stop_atr: 2.0`, `max_concurrent_positions: 2`) plus `exit.trailing_overrides_explicit_exit:
true`. "Market analysis" desactive explicitement (`HERMES_MARKET_ANALYSIS_ENABLED=false`) pour
isoler cette seule variable -- ne pas la comparer au consultant comme si elles ne differaient
que par cette variable, elles en portent deux (market analysis + trailing override).

```powershell
$env:HERMES_STATE_DIR = "state-trailing-exit"
uv run python -m hermes_trading.run
```

```powershell
$env:HERMES_MARKET_ANALYSIS_ENABLED = "false"
uv run python .\scripts\watch_consultant.py --state-dir state-trailing-exit
```

`compare_wallets.py` inclut desormais `state-trailing-exit` automatiquement des qu'il existe
(meme commande que la comparaison a deux, `--trailing-exit-state-dir` par defaut sur
`state-trailing-exit` -- silencieusement ignore si ce dossier n'existe pas, donc rien ne casse
sur un checkout qui n'a pas cette troisieme instance) :

```powershell
uv run python .\scripts\compare_wallets.py
```

Regenerer le graphe documentaire local:

```powershell
uv run python .\scripts\build_docs_graph.py
```

Tester les notifications:

```powershell
uv run python -m unittest tests.test_notifications -v
```

Lancer le viewer local de positions (marche, indicateurs, positions ouvertes/fermees):

```powershell
uv run python .\scripts\position_viewer.py
```

Ouvre `http://127.0.0.1:8787` (navigateur auto). Selection de symbole(s), timeframe et fenetre
de lookback en direct dans la page ; les bougies viennent de Kraken via ccxt (lecture seule, pas
de cle API requise), le RSI/ATR reutilise exactement `hermes_trading.strategy`, les positions
fermees viennent de `state/trades.jsonl` et la position ouverte de `state/positions.json`. Options :
`--port`, `--host`, `--state-dir`, `--exchange-id`, `--no-browser`.

Le viewer ne fait que lire le repertoire `state/` local — il ne contacte jamais Railway. Le cron
`hermes-trading-watch` (`scripts/hermes_watch.py`) synchronise normalement ces fichiers toutes les
10 min, mais c'est une tache planifiee Windows qui ne se declenche que si l'app qui l'a creee est
ouverte ; si elle ne tourne pas, `state/` reste fige (constate : `positions.json` bloque sur un etat
vide pendant que Railway avait deux positions ouvertes). Pour rafraichir `state/` a la demande ou en
continu, independamment de ce cron :

```powershell
MSYS_NO_PATHCONV=1 uv run python scripts\sync_railway_state.py                        # une seule passe
MSYS_NO_PATHCONV=1 uv run python scripts\sync_railway_state.py --interval-seconds 60  # boucle continue
```

(`MSYS_NO_PATHCONV=1` uniquement necessaire sous Git Bash ; sans effet sous PowerShell.)

### Viewer en conteneur

`Dockerfile.viewer` (racine du depot) empaquette le meme script. Build puis run avec le
repertoire `state/` local monte en lecture seule :

```bash
docker build -f Dockerfile.viewer -t hermes-position-viewer .
MSYS_NO_PATHCONV=1 docker run -d --name hermes-viewer -p 8787:8787 -v "$(pwd)/state:/app/state:ro" hermes-position-viewer
```

`MSYS_NO_PATHCONV=1` est requis sous Git Bash/Windows : sans lui, le chemin du volume `-v` est
altere silencieusement et le conteneur sert un `state/` vide. Le conteneur ne se synchronise pas
lui-meme avec Railway (aucun credential Railway n'est embarque dedans) : faire tourner
`sync_railway_state.py --interval-seconds 60` sur l'hote a cote met a jour `state/` local, et le
conteneur voit les changements immediatement via le bind mount, sans redemarrage.

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

Commandes slash Discord du bot de controle:

```text
/status otp:<code>
/qualification otp:<code>
/pnl_graph otp:<code>
/config otp:<code>
/config_set otp:<code> key:<setting> value:<value>
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

## Donnees historiques pour backtest (2026-08-28)

`scripts/fetch_historical_data.py` telecharge et met en cache des donnees OHLCV
gratuites depuis cryptodatadownload.com (Binance) -- des annees d'historique
par symbole (BTCUSDT confirme : 2019-09-08 -> aujourd'hui), contre le plafond
d'environ 7.5 jours de l'endpoint OHLC de Kraken via ccxt qui a limite tous
les backtests reels de cette session. **Ce n'est pas une donnee Kraken** :
une place de marche differente de celle ou la strategie trade reellement --
le mouvement de prix large est fortement correle entre grandes places pour
ces paires liquides, mais la microstructure differe. A traiter comme "tester
la logique de la strategie sur un long historique", pas "predire les fills
exacts sur Kraken". Licence Creative Commons BY-NC-SA (non-commercial) --
adapte a la recherche interne, a revoir si l'usage devient commercial.

```powershell
# Telecharge/rafraichit les 5 symboles par defaut (BTC/ETH/XRP/LINK/SOL) en granularite minute
# (le fichier BTC seul fait environ 190 Mo -- ne re-telecharge pas si deja en cache local)
uv run python .\scripts\fetch_historical_data.py

# Un seul symbole, et construit aussi un cache reechantillonne a 15 min (le vrai timeframe de la strategie)
uv run python .\scripts\fetch_historical_data.py --symbols BTC/USDT --granularity minute --resample-minutes 15
```

Cache local dans `market_data_cache/` (gitignore, jamais commit). Reutilisable
en Python via `load_candles(symbol, granularity)` (retourne des `Candle`
Decimal-safe, pas de float binaire) et `to_ccxt_like(candles)` pour brancher
directement sur les scripts de backtest deja ecrits cette session contre la
forme `ex.fetch_ohlcv(...)` de ccxt.

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
