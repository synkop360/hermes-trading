# Notifications

Le systeme de notifications est desactive par defaut et activable par variables d'environnement.

## Canal choisi

Canal retenu: Discord.

Raison:

- activation simple par webhook entrant;
- pas de bot a maintenir;
- pas de numero de telephone;
- compatible avec Railway sans service additionnel.

WhatsApp est plus lourd a exploiter en production. Telegram reste possible plus tard, mais demande un bot et un chat id. Discord est donc le meilleur premier canal pour ce worker.

## Variables

Mode desactive:

```text
HERMES_NOTIFY_ENABLED=false
HERMES_NOTIFY_SERVICE=discord
```

Mode actif:

```text
HERMES_NOTIFY_ENABLED=true
HERMES_NOTIFY_SERVICE=discord
DISCORD_WEBHOOK_URL=<webhook discord>
```

Options:

```text
HERMES_NOTIFY_USERNAME=Hermes Trading
HERMES_NOTIFY_MODE=verbose
HERMES_NOTIFY_ON_TRADES=true
HERMES_NOTIFY_ON_ORDERS=true
HERMES_NOTIFY_ON_ERRORS=true
HERMES_NOTIFY_ON_HEARTBEAT=false
HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=0
HERMES_NOTIFY_SUMMARY_FREQUENCIES=
HERMES_NOTIFY_PROFITABILITY_FREQUENCIES=
HERMES_FUNDS_ALERT_ENABLED=false
HERMES_FUNDS_ALERT_QUOTE=USDT
HERMES_FUNDS_MIN_AVAILABLE_USDT=20
HERMES_FUNDS_MIN_ORDER_MULTIPLE=3
HERMES_FUNDS_BUFFER_PCT=0.10
HERMES_FUNDS_ALERT_COOLDOWN_SECONDS=21600
HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE=1.20
HERMES_FUNDS_ALERT_MENTION=
```

`DISCORD_WEBHOOK_URL` est un secret. Ne pas le committer.

## Evenements notifies

Quand les notifications sont activees:

- trade ferme;
- ordre simule, valide ou soumis;
- erreur worker.

Les heartbeats de routine ne sont pas notifies par defaut pour eviter le bruit.

## Modes de communication

`HERMES_NOTIFY_MODE=verbose`

- publie les trades fermes;
- publie les ordres simules, valides ou soumis;
- publie les erreurs;
- mode le plus complet.

`HERMES_NOTIFY_MODE=orders_only`

- publie uniquement les ordres;
- utile pour verifier le comportement de l'execution sans recevoir chaque PnL.

Alias accepte:

```text
HERMES_NOTIFY_MODE=order_only
```

`HERMES_NOTIFY_MODE=pnl_only`

- publie uniquement les trades avec PnL non nul;
- ignore les trades flat a `0.0`;
- ignore les ordres.

Format:

```text
Hermes Trading | P&L LOSS XRP/USDT -0.0688% (raw=-0.000688) strategy=47 mode=training rsi=12.8656 threshold=17
Hermes Trading | P&L GAIN XRP/USDT 0.1200% (raw=0.0012) strategy=47 mode=training rsi=14.2 threshold=17
```

Alias accepte:

```text
HERMES_NOTIFY_MODE=gain_loss_only
```

## Resume periodique

Le resume periodique recommande est configure avec des frequences temporelles:

```text
HERMES_NOTIFY_SUMMARY_FREQUENCIES=12h
```

Formats acceptes:

- `1h` ou `hourly`: un resume horaire;
- `12h`: un resume toutes les 12 heures;
- `1d` ou `daily`: un resume journalier;
- `1w` ou `weekly`: un resume hebdomadaire;
- `1mo` ou `monthly`: un resume mensuel.

Plusieurs frequences peuvent etre activees ensemble:

```text
HERMES_NOTIFY_SUMMARY_FREQUENCIES=[12h,1w]
```

Dans ce cas, Hermes publie un resume demi-journalier et un resume hebdomadaire. Le raccourci `1m` est refuse volontairement car il est ambigu entre minute et mois; utiliser `1mo`.

Le worker resume la derniere periode terminee et memorise les periodes deja envoyees dans `state/notification_summaries.json`, pour eviter les doublons au redemarrage.

Le message contient:

- fenetre couverte;
- nombre total de decisions;
- decisions sans trade;
- nombre total de trades;
- gains;
- pertes;
- flats;
- PnL total;
- nombre total d'ordres;
- detail des ordres par statut;
- detail par actif.

Exemple:

```text
Hermes Trading | Periodic summary 12h
Window: 2026-07-28T00:00:00+00:00 -> 2026-07-28T12:00:00+00:00
----------------------------------------
Decisions: total=44 no_trade=40
Trades: total=4 wins=1 losses=2 flat=1 total_pnl=-0.0012
Orders: total=4 by_status=simulated:4
----------------------------------------
By symbol:
SHIB/USDT | trades=2 wins=0 losses=1 flat=1 pnl=-0.002
XRP/USDT | trades=2 wins=1 losses=1 flat=0 pnl=0.0008
```

Si aucune cloture ni aucun ordre n'a eu lieu, Hermes publie quand meme le bilan:

```text
Hermes Trading | Periodic summary 12h
Window: 2026-07-28T00:00:00+00:00 -> 2026-07-28T12:00:00+00:00
----------------------------------------
Decisions: total=44 no_trade=44
Trades: total=0 wins=0 losses=0 flat=0 total_pnl=0
Orders: total=0 by_status=none
----------------------------------------
By symbol:
No closed trades
```

L'ancien resume par nombre de cycles reste disponible pour compatibilite:

```text
HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=30
```

Les deux systemes peuvent etre actives en meme temps:

```text
HERMES_NOTIFY_SUMMARY_EVERY_CYCLES=30
HERMES_NOTIFY_SUMMARY_FREQUENCIES=hourly
```

Avec cette configuration, Hermes publie un resume toutes les 30 iterations et un resume horaire quand une heure complete vient de se terminer. Si les deux tombent au meme cycle, les deux messages sont envoyes.

## Snapshot de rentabilite

Le bilan economique net est separe du resume des trades. Il comprend le PnL brut, les frais Kraken, le slippage, les couts Railway et les couts IA connus, ainsi que la projection 30 jours et le verdict de la gate economique.

Il reste desactive par defaut. Activez-le avec les memes frequences explicites que les resumes operationnels:

```text
HERMES_NOTIFY_PROFITABILITY_FREQUENCIES=[1d,1w]
```

Chaque periode terminee est publiee une seule fois; l'anti-doublon est conserve dans `state/notification_profitability_summaries.json`. Les couts inconnus restent indiques comme manquants et bloquent la gate economique: Hermes ne les traite jamais comme zero.

## Alertes fonds bas

Les alertes fonds bas sont des notifications de securite. Elles sont desactivees par defaut et passent par Discord quand les notifications sont actives.

Activation:

```text
HERMES_FUNDS_ALERT_ENABLED=true
HERMES_FUNDS_ALERT_QUOTE=USDT
HERMES_FUNDS_MIN_AVAILABLE_USDT=20
HERMES_FUNDS_MIN_ORDER_MULTIPLE=3
HERMES_FUNDS_BUFFER_PCT=0.10
HERMES_FUNDS_ALERT_COOLDOWN_SECONDS=21600
HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE=1.20
HERMES_FUNDS_ALERT_MENTION=@here
```

Calcul de la limite basse:

```text
low_limit = max(
  HERMES_FUNDS_MIN_AVAILABLE_USDT,
  HERMES_TRADING_MAX_ORDER_USDT * HERMES_FUNDS_MIN_ORDER_MULTIPLE * (1 + HERMES_FUNDS_BUFFER_PCT)
)
```

Niveaux:

- `OK`: `available > low_limit`.
- `WARNING`: `critical_limit < available <= low_limit`.
- `CRITICAL`: `available <= critical_limit`.
- `RESOLVED`: apres une alerte, `available >= low_limit * HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE`.

`critical_limit` vaut `max(HERMES_TRADING_MAX_ORDER_USDT, exchange_min_cost)`. Si le minimum exchange n'est pas connu, Hermes utilise `HERMES_TRADING_MAX_ORDER_USDT`.

Exemple warning:

```text
@here
[HERMES WARNING] LOW FUNDS

Quote: USDT
Available: 14.82
Low limit: 20.00
Critical limit: 5.00

Action: add funds or reduce order size
```

Exemple critical:

```text
@here
[HERMES CRITICAL] FUNDS TOO LOW

Quote: USDT
Available: 3.91
Low limit: 20.00
Critical limit: 5.00

Action: pause live trading or add funds now
```

Exemple resolved:

```text
[HERMES RESOLVED] FUNDS BACK ABOVE LIMIT

Quote: USDT
Available: 26.40
Resolve limit: 24.00
```

L'etat anti-spam est stocke dans `state/funds_alert.json`. Une alerte de meme niveau est repetee seulement apres le cooldown. Une montee de `WARNING` vers `CRITICAL` est envoyee immediatement.

## Activation Railway

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

Desactivation immediate:

```powershell
railway.cmd variable set HERMES_NOTIFY_ENABLED=false --skip-deploys
railway.cmd up
```

## Test local sans reseau

Les tests unitaires injectent un faux poster HTTP et ne contactent pas Discord:

```powershell
uv run python -m unittest tests.test_notifications -v
```

## Reference Discord

Documentation officielle:

- https://docs.discord.com/developers/resources/webhook
