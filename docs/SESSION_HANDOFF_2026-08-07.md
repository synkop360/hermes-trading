# Hermes Trading Session Handoff — 2026-08-07 (Cowork)

Ce document sert de point de reprise pour continuer ce travail depuis Claude Code (ou tout autre outil). Il suit le même format que `docs/SESSION_HANDOFF_2026-08-01.md`.

## Repository

- Path: `E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading`
- Branch: `feat/live-hardening` (aucun commit fait cette session — tout est encore dans l'arbre de travail)
- Railway project: `awake-abundance`, service `hermes-trading`, production environment, volume `/app/state`.

## Rien n'a été commité ni déployé

Tous les changements ci-dessous sont dans l'arbre de travail local uniquement. Aucun `git commit`, aucun `railway up`, aucun `railway ssh` n'a été exécuté cette session (le sandbox Cowork n'a pas d'accès réseau vers GitHub/PyPI ni de session Railway authentifiée).

## Ce qui a été fait cette session

### 1. Audit complet du dépôt
- Cartographie stack/architecture/tests/CI.
- `CLAUDE.md` créé à la racine (converti depuis `AGENTS.md`, qui ne contenait que des instructions `graphify`).
- `docs/PROJECT_KNOWLEDGE.md` créé : fiche de connaissance complète du projet, mise à jour au fil des passes suivantes.

### 2. Nettoyage
- 62 répertoires `.test-tmp-*` et le fichier vide `cut]` supprimés (aucun n'était tracké par git).
- `.gitignore` : pattern `.test-tmp/` généralisé en `.test-tmp*/` pour couvrir toutes les variantes.
- Ambiguïté `run.py` vs `colocated.py` clarifiée dans `README.md` et `CLAUDE.md` (ce n'était pas une incohérence : `colocated.py` enveloppe `run.py` et ajoute API de contrôle + bot Discord optionnels — c'est lui que lance le `Dockerfile`).

### 3. Simplification (fonctionnalité inchangée, vérifié par analyse AST)
Fichiers touchés : `loop.py`, `reflect.py`, `funds.py`, `risk.py`, `notifications.py`, `market_data.py`, `reconciliation.py`.
- 4 fonctions mortes supprimées (0 appelant dans tout le dépôt) : `_append_jsonl`, `_legacy_generate_asset_proposal`, `_legacy_normalize_strategy_proposal`, `_run_hermes_prompt`.
- 5 imports inutilisés supprimés.
- 5 fonctions dupliquées mot pour mot entre modules consolidées en une seule implémentation partagée (`_default_exchange`, `_finite_decimal`/`_cancel_and_await`, `_decimal_env`, `_ensure_utc`, `_load_trades`/`_load_jsonl`).
- Résultat : -199 lignes / +18 lignes sur 7 fichiers, aucun changement de logique métier.

### 4. Diagnostic des pertes systématiques (4/4 trades perdants, strategy v104)
Cause racine à plusieurs facteurs, tous vérifiés sur les données réelles (`state/trades.jsonl`, `state/decisions.jsonl`, `state/hypotheses.jsonl`) :
- `exit.take_profit_atr: 2.5` (≈0.6% de mouvement de prix pour BTC) était **plus petit** que le coût d'aller-retour modélisé (frais taker 0.4%×2 + slippage 0.1%×2 = 1.0%) : même un trade gagnant parfait était perdant net.
- Le garde-fou `positions.py::_expected_payoff_covers_costs` censé bloquer ce cas ne vérifiait que `take_profit_pct`, jamais `take_profit_atr` (le mode réellement utilisé) — bug silencieux.
- `exit.max_bars: 1` forçait la clôture après 1h dans 139/182 sorties récentes, contre 1 seule `take_profit_atr` — quasi aucune chance d'atteindre l'objectif.
- `entry.threshold: 80` (RSI ≤ 80, quasi toujours vrai) était non sélectif, multipliant l'exposition aux coûts fixes.
- La dérive de 30→80 vient d'un bug de `reflect.py::run_fallback_reflection` : il **assouplissait** l'entrée (au lieu de la resserrer) à chaque cycle où le rendement était sous l'objectif, même en perdant de l'argent.
- L'auto-correction Hermes était bloquée : `SUPPORTED_STRATEGY_VARIABLES` ne couvrait pas `stop_atr`/`take_profit_atr`/`max_bars` (les vrais leviers), et les tentatives de réduire `position_size_r` butaient sur son plancher (0.1), rejetées en boucle (visible dans `hypotheses.jsonl`).

### 5. Correctifs appliqués (approuvés par l'utilisateur, les 4 options)
- `positions.py::_expected_payoff_covers_costs` couvre maintenant `take_profit_atr` (converti en % via l'ATR courant), plus seulement `take_profit_pct`.
- `reflect.py::run_fallback_reflection` resserre l'entrée quand le rendement est sous l'objectif **et** le score est négatif, au lieu de l'assouplir inconditionnellement.
- `reflect.py::SUPPORTED_STRATEGY_VARIABLES` étendu avec `exit.stop_atr`, `exit.take_profit_atr`, `exit.max_bars` (bornes et pas max définis ; logique de lecture/écriture imbriquée généralisée dans `_apply_strategy_proposal`/`_validate_strategy_change` via `_variable_path`/`_get_nested`/`_set_nested`).
- `state/strategy.yaml` corrigé manuellement en v105 (v104 archivée dans `state/history/v0104.yaml`, changement journalisé dans `state/hypotheses.jsonl` avec `mode: manual_operator_correction`) :
  - `entry.threshold` : 102 global / 80 par symbole → **30** (overrides par symbole supprimés, devenus redondants).
  - `exit.max_bars` : 1 → **12**.
  - `exit.stop_atr` : 1.5 → **2.0**.
  - `exit.take_profit_atr` : 2.5 → **6.0** (nécessaire pour que le nouveau garde-fou de coûts ne bloque pas silencieusement toutes les entrées).

## Vérification effectuée et ses limites

- `python3 -m py_compile` sur les 93 fichiers `.py` du dépôt : aucune erreur, à chaque étape.
- Scan AST maison (fonctions mortes, imports inutilisés, corps de fonction dupliqués) : zéro résultat après la passe de simplification.
- Relecture manuelle de `tests/test_positions.py` et `tests/test_score_reflect.py` pour confirmer que la logique testée reste compatible avec les changements de signature/comportement.
- **La suite `unittest` n'a jamais pu être exécutée dans le sandbox Cowork** : pas de Python 3.11 disponible (le code utilise `datetime.UTC`, requis depuis 3.11), et l'accès réseau (PyPI, GitHub) est bloqué par l'allowlist du sandbox, donc `uv run` ne peut pas télécharger le toolchain.
- Note indépendante : `tests/test_score_reflect.py` porte un diff (39 lignes) qui n'a pas été fait par moi cette session — probablement un reliquat d'une session de travail antérieure (mocks de `subprocess.run` déplacés de `hermes_trading.reflect` vers `hermes_trading.model_runner`, cohérent avec l'architecture `ReflectionModelRunner`/`ProviderRouter` déjà en place dans `reflect.py`). À vérifier/committer séparément si légitime.

## Fichiers modifiés ou créés (non commités)

```
 .gitignore                       |   3 +-
 AGENTS.md                        |  24 ++--   (fins de ligne CRLF, pré-existant, non lié à cette session)
 README.md                        |   5 +-
 docs/GRAPHIFY_OBSIDIAN.md        |  54 ++++----  (idem CRLF)
 hermes_trading/funds.py          |  14 +--
 hermes_trading/loop.py           |   7 --
 hermes_trading/market_data.py    |  23 +---
 hermes_trading/notifications.py  |   7 +-
 hermes_trading/positions.py      |  32 ++++-
 hermes_trading/reconciliation.py |   1 -
 hermes_trading/reflect.py        | 263 +++++++++++----------------------------
 hermes_trading/risk.py           |  35 ++----
 tests/test_score_reflect.py      |  39 ++----  (non fait par cette session, cf. note ci-dessus)

Nouveaux fichiers non trackés (hors state/ et graphify-out/, tous deux ignorés par git) :
 CLAUDE.md
 docs/PROJECT_KNOWLEDGE.md
 docs/SESSION_HANDOFF_2026-08-07.md  (ce fichier)
```

`state/strategy.yaml`, `state/history/v0104.yaml` et `state/hypotheses.jsonl` sont modifiés/créés mais ignorés par git (`state/` est dans `.gitignore`) — ils ne remonteront pas dans `git status`.

## Immediate next work

1. Lancer `uv run python -m unittest discover -s tests` (Python 3.11 requis) et confirmer que tout passe, notamment `tests/test_positions.py` et `tests/test_score_reflect.py`.
2. Relire le diff (`git diff`) et décider si le changement pré-existant sur `tests/test_score_reflect.py` doit être inclus dans le même commit ou traité séparément.
3. Committer (suggestion, à ajuster) :
   ```powershell
   git add hermes_trading/ .gitignore README.md CLAUDE.md docs/PROJECT_KNOWLEDGE.md docs/SESSION_HANDOFF_2026-08-07.md
   git commit -m "fix: cover take_profit_atr in cost guard, stop fallback reflection loosening on losses, expose exit variables to reflection"
   ```
4. Déployer le code :
   ```powershell
   railway status
   railway up --service hermes-trading --environment production
   ```
5. Pousser `state/strategy.yaml` corrigé vers le volume Railway distant (un redeploy seul ne le fait pas — `ensure_state_files()` ne touche jamais un fichier déjà présent sur `/app/state`) :
   ```powershell
   Get-Content .\state\strategy.yaml -Raw | railway ssh "cat > /app/state/strategy.yaml"
   ```
6. Vérifier après coup :
   ```powershell
   railway logs
   railway ssh "cat /app/state/strategy.yaml"
   railway ssh "printenv HERMES_TRADING_MODE"
   railway ssh "printenv HERMES_TRADING_ALLOW_REAL_ORDERS"
   ```
   Confirmer `version: '105'`, `entry.threshold: 30`, `exit.max_bars: 12`, mode `training`, ordres réels toujours désactivés.
7. Surveiller les cycles suivants (`decisions.jsonl`, `trades.jsonl`, résumés Discord si activés) pour confirmer que le taux de perte s'améliore et que le garde-fou `expected_payoff_below_costs` n'empêche pas totalement les entrées (auquel cas `take_profit_atr` devra être encore ajusté par actif selon leur ATR réel).

## Operational cautions

- `railway ssh` échoue parfois une fois avec une erreur temporaire de vérification de clé ; relancer une ou deux fois.
- Les répertoires `.test-tmp-*` qui traînaient à la racine ont été nettoyés cette session — le pattern `.gitignore` (`​.test-tmp*/`) empêche qu'ils reviennent polluer un `git add -A` futur.
- Ne jamais committer `.env`, `.env.key`, `.railway/`, `.home/.ssh/` — déjà dans `.gitignore`, non lus ni affichés cette session.
- `graphify-out/` n'est pas tracké par git malgré ce que suggère `AGENTS.md` ("knowledge graph ... versioned") — reste local, ne pollue pas le dépôt distant.
