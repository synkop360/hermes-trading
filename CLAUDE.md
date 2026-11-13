# CLAUDE.md

> Agents de l'équipe Paperclip : les règles prioritaires en tête d'`AGENTS.md` s'appliquent (aucun déploiement, aucune commande Railway, pas de graphify).

Instructions pour Claude Code dans ce dépôt. Converti depuis `AGENTS.md` (instructions Codex) et complété avec les informations vérifiables du dépôt (`README.md`, `docs/`, `pyproject.toml`). Aucun `CLAUDE.md` préexistant n'a été trouvé à la racine — ce fichier est une création, pas une fusion.

## Project Overview

Hermes Trading est un worker Python de trading automatique en mode simulé. Il surveille les paires Kraken `BTC/USDT`, `ETH/USDT`, `XRP/USDT`, `SHIB/USDT`, applique une stratégie RSI par cycle, journalise chaque décision dans `state/`, et laisse un système externe (Hermes, cron + LLM ou fallback local) proposer des ajustements de stratégie une variable à la fois. Déployé sur Railway (service `hermes-trading`, volume sur `/app/state`). Le trading réel est bloqué par défaut derrière plusieurs garde-fous explicites — voir la section Safety ci-dessous avant de toucher à `execution.py`, `exchange.py`, `risk.py` ou aux variables `HERMES_TRADING_*`.

## Build & Test Commands

Gestionnaire de paquets : `uv` (Python ≥3.11, verrouillage dans `uv.lock`).

```powershell
uv sync                                              # installer les dépendances
uv run python -m hermes_trading.run --interval-seconds 60   # lancer le worker localement
uv run python -m unittest discover -s tests          # lancer toute la suite de tests
uv run python -m unittest tests.test_notifications -v  # lancer un seul module de test
```

Réflexion / ajustement de stratégie :

```powershell
uv run python -m hermes_trading.reflect --fallback   # logique locale déterministe
uv run python -m hermes_trading.reflect --hermes      # proposition via LLM/CLI hermes
```

Vérification de préparation live (sans démarrer un cycle complet) :

```powershell
uv run python scripts\check_live_readiness.py
```

Une CI GitHub (`.github/workflows/paper-cloud.yml`) lance sur chaque pull request un sous-ensemble ciblé de tests (supervision cloud, service paper, notifications) en mode paper ; la suite complète reste à lancer manuellement avant tout commit. Déploiement via `Dockerfile` (`CMD` : `scripts.paper_cloud_service`, avec `HERMES_TRADING_MODE=paper` et `HERMES_TRADING_ALLOW_REAL_ORDERS=false` forcés) sur Railway, qui suit la branche `master` : tout ce qui arrive sur `master` est déployé ; voir `docs/DEPLOYMENT.md` et `docs/COMMANDS.md` pour les commandes Railway (`railway status`, `railway logs`, `railway up`, `railway variables`).

## Project Structure

```
hermes_trading/        package principal
  run.py                point d'entrée du worker (documenté comme entrée principale)
  colocated.py           point d'entrée alternatif, utilisé par le Dockerfile en production
  loop.py                boucle multi-actifs et décisions de trading
  reflect.py              réflexion Hermes ou fallback local
  strategy.py, domain.py  modèle de stratégie et entités métier (Decimal obligatoire)
  execution.py, exchange.py  interaction Kraken / exécution d'ordres
  risk.py, funds.py, costs.py, profitability.py  garde-fous économiques
  qualification*.py       pipeline de promotion progressive vers le live, par preuves
  control*.py, discord_bot.py  canal de commande à distance (Discord, auth HMAC/nonce)
  persistence/            repository + migrations SQLite (state/trading.db)
  adapters/                prix (ccxt/Kraken), news, on-chain, macro — payloads schema_version: 1
state/                  fichiers d'état runtime (source de vérité locale ; sur Railway, volume /app/state fait foi)
docs/                   documentation opérationnelle (voir liste ci-dessous)
scripts/                scripts opérationnels (qualification, vérifications, watchdog Hermes)
tests/                  suite de tests unittest, avec doubles dans tests/fakes/
```

Documents utiles à lire selon le besoin : `docs/ARCHITECTURE.md` (flux et composants), `docs/COMMANDS.md` (commandes), `docs/SAFETY.md` (garde-fous live), `docs/DEPLOYMENT.md` (Railway), `docs/KRAKEN.md` (intégration exchange), `docs/STATE_FILES.md` (format de chaque fichier d'état), `docs/OPERATIONS.md` (routine de surveillance), `docs/TROUBLESHOOTING.md` (pannes connues), `docs/NOTIFICATIONS.md` (Discord).

## Code Style

Aucun linter ni formatter n'est configuré dans `pyproject.toml` (pas de `ruff`/`black`/`mypy`, pas de dépendances de dev) : respecte le style déjà en place plutôt qu'un outil externe.

- `from __future__ import annotations` en tête de chaque module.
- Dataclasses (`@dataclass`, souvent `frozen=True`) et `Enum` pour les états métier (`PositionStatus`, `OrderStatus`, `QualificationPhase`).
- Toute valeur financière doit être un `decimal.Decimal`. Les `float` binaires sont explicitement interdits pour l'argent (`decimal_from`, `_reject_binary_floats` dans `domain.py`) — ne jamais introduire de `float` pour un prix, un montant ou un PnL.
- Erreurs typées dédiées plutôt que des exceptions génériques (`SchemaError`, `UnsupportedSchemaError`, `OperatorConfigError`).
- Les adapters de marché retournent un payload avec `schema_version: 1` ; une version inattendue doit lever une erreur plutôt que d'écrire une donnée ambiguë.

## Testing

Framework : `unittest` de la stdlib (pas pytest). 46 fichiers de test sous `tests/`, un par module métier, plus `tests/test_operational_scripts.py` pour les scripts. Les doubles de test (ex. Kraken) vivent dans `tests/fakes/`. Lancer la suite complète avant tout commit :

```powershell
uv run python -m unittest discover -s tests
```

Certains tests écrivent des fichiers temporaires (SQLite, JSON) sous des répertoires `.test-tmp*` à la racine du dépôt. Ne pas committer ces répertoires.

## Git / PR Workflow

Aucune règle de PR/commit écrite n'existe dans le dépôt. L'historique observé suit toutefois le format Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`) — respecte ce format pour rester cohérent avec l'historique existant. CI ciblée sur les pull requests (voir plus haut), pas de hook de pre-commit ; lancer la suite de tests manuellement avant de committer.

## Safety — trading réel

Ce projet manipule potentiellement de l'argent réel via Kraken. Avant toute modification touchant à l'exécution d'ordres, au mode `live`, ou aux variables `HERMES_TRADING_*` :

- Les modes simulés sont `paper`, `training`, `train`, `demo` ; aucun n'envoie d'ordre réel.
- Le mode `live` exige deux confirmations explicites : `HERMES_TRADING_I_ACCEPT_RISK=true` et `HERMES_TRADING_LIVE_CONFIRM=I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS`.
- Par défaut le live reste `validation_only` (Kraken valide l'ordre sans le soumettre) ; passer à de vrais ordres exige en plus `HERMES_TRADING_LIVE_VALIDATE_ONLY=false` et `HERMES_TRADING_ALLOW_REAL_ORDERS=true`, changement qui ne doit être fait qu'après une promotion manuelle et un cycle validation-only sain.
- Ne jamais contourner ces garde-fous pour « tester rapidement » — voir `docs/SAFETY.md` pour la procédure de promotion complète et le rollback.
- Ne jamais committer de secret : `.env`, clés API, tokens Railway, clés SSH. Le dépôt contient déjà des fichiers sensibles en clair sur le disque (`.env`, `.env.key`, `.home/.ssh/`, `.railway/`) — ils sont dans `.gitignore`, ne pas les lire ni les afficher dans une réponse, ni les inclure dans un diff ou un commit.

## Notes & Gotchas

- **Graphify (converti depuis AGENTS.md)** : ce projet a un graphe de connaissance versionné dans `graphify-out/` (nœuds, structure de communauté, relations inter-fichiers). Pour toute question sur la structure ou les relations du code, préférer une requête `graphify` à une exploration manuelle par `grep`/lecture de fichiers quand `graphify-out/graph.json` existe :
  - `graphify query "<question>"` pour une question générale sur le code.
  - `graphify path "<A>" "<B>"` pour une relation entre deux éléments.
  - `graphify explain "<concept>"` pour un concept ciblé.
  - Ces commandes renvoient un sous-graphe ciblé, généralement plus petit que `GRAPH_REPORT.md` ou un grep brut.
  - Si `graphify-out/wiki/index.md` existe, l'utiliser pour la navigation large plutôt que de parcourir les sources brutes.
  - Ne lire `graphify-out/GRAPH_REPORT.md` que pour une revue d'architecture large, ou si `query`/`path`/`explain` ne suffisent pas.
  - Un `graphify-out/` « sale » (modifié par des hooks ou mises à jour incrémentales) est normal et n'est pas une raison d'ignorer graphify. Ne l'ignorer que si la tâche porte explicitement sur un graphe obsolète/incorrect, ou si l'utilisateur le demande.
  - Après toute modification de code, lancer `graphify update .` pour garder le graphe à jour (analyse AST uniquement, sans coût d'API).
  - Le hook Codex `.codex/hooks.json` invoque `graphify.EXE hook-check` avant chaque commande Bash sur la configuration Windows locale de l'utilisateur ; ce hook est spécifique à Codex CLI et n'a pas d'équivalent direct ici — traiter les instructions `graphify` ci-dessus comme une pratique à suivre manuellement plutôt que comme un hook automatique.
- Points d'entrée : `hermes_trading.run` lance uniquement la boucle de trading (pratique pour un test local rapide). `hermes_trading.colocated` réutilise `run.ensure_state_files` et `loop.run_loop`, puis ajoute l'API de contrôle locale (si `HERMES_CONTROL_HMAC_SECRET` est défini) et le bot Discord (si les variables `HERMES_DISCORD_*` sont définies) ; en production, le `Dockerfile` lance désormais `scripts.paper_cloud_service` (service paper supervisé).
- `.gitignore` ignore `.test-tmp*/` (tous les répertoires de test temporaires, pas seulement `.test-tmp/`) : ne pas committer de nouveaux répertoires de ce type, et vérifier que le pattern reste `.test-tmp*/` s'il est modifié.
- L'état local (`state/`) peut être en retard par rapport au volume Railway `/app/state`, qui fait foi pour le service déployé.
- Aucun Python ≥3.11 ni accès réseau PyPI/GitHub n'est disponible dans certains environnements d'exécution sandboxés : `uv run` peut échouer à télécharger le toolchain. Dans ce cas, une vérification statique (`python3 -m py_compile` sur les fichiers du dépôt) reste possible mais ne remplace pas `uv run python -m unittest discover -s tests`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
