# Pilote paper reproductible — mode opératoire local

Ce document décrit la version livrée du **pilote paper offline**. Il est
volontairement limité au moteur local de simulation : il prouve que le worker
peut produire une session déterministe, bornée et vérifiable sans secret, sans
appel réseau et sans ordre réel. Il ne prétend pas démontrer la reprise du
worker à huit comptes ni de son superviseur ; ce niveau d'acceptation reste à
confirmer par Thomas.

## Ce que ce pilote démontre

- Le moteur de simulation (`hermes_trading.loop.run_cycle`) s'exécute sur une
  fixture de bougies clôturées déterministes.
- La session s'arrête seule après un nombre de cycles fixé en ligne de
  commande.
- Toutes les preuves (journal de décisions, trades, base SQLite,
  `result.json`) restent sous une racine temporaire unique.
- Aucune donnée sensible, aucune clé API, aucun accès Kraken authentifié,
  aucun appel de modèle et aucun socket réseau ne sont nécessaires.

## Prérequis

- Python ≥ 3.11.
- `uv` installé et dans le `PATH`.
- Une copie propre de ce dépôt.
- Aucune variable locale sensible (`.env`, clés API, etc.) n'est chargée par
  les commandes ci-dessous.

## Prérequis du code

Les commandes ci-dessous utilisent le runner `hermes_trading.paper_offline` et
le support de `reflection_owner.json` / `assert_writer_owner(...,
"deterministic_worker")` dans `hermes_trading.reflect`. Ces éléments sont
disponibles à partir du commit `e4945e8` (résolution de [LAT-21] et
[LAT-22]) ; la branche courante les inclut.

[LAT-21]: /LAT/issues/LAT-21

## Commandes reproductibles

Les deux commandes suivantes doivent être exécutées depuis la racine du dépôt,
dans un répertoire ne contenant pas déjà `.test-tmp-paper-pilot/session-001`.

### 1. Session paper offline

```bash
HERMES_TRADING_MODE=paper HERMES_NOTIFY_ENABLED=false \
uv run python -m hermes_trading.paper_offline \
  --root .test-tmp-paper-pilot/session-001 --cycles 6 --interval-seconds 0
```

### 2. Réflexion fallback sur le compte simulé

```bash
uv run python -m hermes_trading.reflect --fallback \
  --state-dir .test-tmp-paper-pilot/session-001/account
```

## Durée mesurée

Mesuré sur l'environnement de développement courant après synchronisation des
dépendances :

- `PYTHONPATH=tests uv run python -m unittest test_paper_offline test_paper_offline_reflection` :
  **~57 s** — `Ran 10 tests ... OK`.
- Les deux commandes documentées ci-dessus, exécutées dans une racine neuve :
  **~30 s** au total (temps réel).

Le budget d'acceptation retenu est de **60 s maximum pour les deux commandes**
après installation des dépendances. La durée peut varier légèrement selon la
machine, mais une exécution nettement supérieure indique un problème
(anomalie de fixture, attente réseau non bloquée, etc.).

## Schéma des preuves

Après la première commande, le répertoire
`.test-tmp-paper-pilot/session-001/account/` contient :

| Fichier | Contenu attendu |
|--------|-----------------|
| `trading.db` | Tables canoniques SQLite avec décisions datées et opérations simulées. |
| `decisions.jsonl` | Projection JSONL de chaque décision (action, prix de référence, raison, horodatage). |
| `trades.jsonl` | Trades fermés avec prix, quantités, frais et P&L réalisé. |
| `result.json` | Synthèse structurée de la session. |
| `reflection_owner.json` | Politique de propriété `deterministic_worker`, identité de session et garde-fous. |
| `hypotheses.jsonl` | (après la seconde commande) Hypothèse `mode=fallback` et version de stratégie. |

### Champs exigés dans `result.json`

- `mode`: `"paper"`
- `execution`: `"simulated"`
- `origin`: `"synthetic_offline_fixture"`
- `session_id`: identifiant unique de la session
- `cycles_completed`: nombre de cycles terminés (doit être 6)
- `simulated_periods`: liste des horodatages de chaque cycle
- `assets`: actif simulé (`BTC/USDT` avec la fixture par défaut)
- `financials.start_equity`, `financials.final_balance`,
  `financials.realized_pnl`, `financials.fees`,
  `financials.trade_count`, `financials.win_count`,
  `financials.loss_count`, `financials.open_position_count`
- `activity`: au moins une entrée et une clôture simulées avec prix,
  quantités, frais et P&L
- `costs`: taux de frais explicites, coût IA `0`, coût données `0`,
  origine des données
- `synthetic_origin`: fixture utilisée, absence d'appel de modèle, absence
  d'ordre réel
- `paths`: chemins absolus de toutes les preuves

Aucune donnée manquante ne doit être transformée en zéro : les champs
financiers absent ou non calculable doivent être `null` ou omis, pas `0`.

### Après la seconde commande

- `hypotheses.jsonl` contient une entrée avec `mode: fallback`.
- `strategy.yaml` passe de la version `01` à `02`.
- La variable modifiée est `entry.threshold` (la stratégie de repli peut aussi
  modifier `stop_loss_pct` si le drawdown dépasse le maximum configuré).
- Si la stratégie est gelée (`strategy_is_frozen`), le résultat est
  `status: frozen` avec `changes: []` ; ce cas est un non-changement
  justifié, pas un échec.

## Fixture choisie

La fixture par défaut est `tests/fixtures/paper_offline_btcusdt_1h.json`.
Elle contient six bougies d'une heure ; les deux premières sont construites
pour observer au moins une entrée longue RSI et une clôture par take-profit,
sans changer les règles économiques du moteur. Les cycles suivants produisent
des décisions `no_trade` car la condition d'entrée n'est plus remplie.

## Interpréter les résultats négatifs ou l'absence d'activité

Ce pilote n'exige aucun rendement positif. Les résultats sont interprétables
ainsi :

- `realized_pnl` négatif ou nul : le moteur a simulé des trades, les coûts
  sont explicités ; c'est une preuve d'activité valide.
- `trade_count == 0` avec des décisions `no_trade` : la session a tourné, la
  stratégie n'a pas trouvé d'entrée ; la fixture ou la configuration explique
  la raison dans `activity[].reason`.
- Un seul `heartbeat.json` vide, un checkpoint vide ou un processus encore
  actif ne constitue pas une preuve acceptable.

## Limites explicites

- **Local uniquement** : pas de collecteur Kraken, pas de volume de marché
  réel, pas de latence réseau.
- **Sans modèle** : aucun appel à OpenAI, à un modèle local, à Discord, à
  Railway ou à un fournisseur de LLM.
- **Sans ordre réel** : le mode `paper`/`training` force une exécution
  simulée ; les garde-fous live restent intacts.
- **Pas le worker à huit comptes** : ce pilote ne prouve pas le déploiement
  Railway, la supervision multi-compte, la reprise après panne ni la
  publication Discord. Voir les documents historiques ci-dessous pour le
  contexte ; leurs affirmations ne sont pas traitées comme des preuves
  actuelles de ce pilote.

## Liens vers documents historiques

- `docs/PAPER_BETA_READINESS.md` : critères de lancement paper historiques.
- `docs/OPERATIONS.md` : routine de surveillance du worker installé.
- `docs/HERMES.md` : cron Hermes et logique de réflexion.
- `docs/SAFETY.md` : garde-fous avant tout passage en live.
- `docs/STATE_FILES.md` : format des fichiers d'état.

## Test négatif d'acceptation

L'acceptation échoue si l'une des conditions suivantes est constatée :

- une preuve listée ci-dessus est absente ou incohérente ;
- un socket réseau est créé pendant l'exécution (les commandes lèvent
  `NetworkBlockedError`) ;
- un appel sortant vers Kraken, OpenAI, Discord, Railway ou un modèle local
  est tenté ;
- le processus ne s'arrête pas seul ou retourne un code non nul ;
- `result.json` transforme une valeur manquante en `0`.

[LAT-21]: /LAT/issues/LAT-21
[LAT-22]: /LAT/issues/LAT-22
