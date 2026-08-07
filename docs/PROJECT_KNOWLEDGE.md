# Fiche de connaissance — Hermes Trading

*Générée le 2026-08-07 par analyse intégrale du dépôt.*

## Résumé

Hermes Trading est un worker Python de trading automatique, actuellement en mode simulé, qui surveille quatre paires Kraken (`BTC/USDT`, `ETH/USDT`, `XRP/USDT`, `SHIB/USDT`), applique une stratégie basée sur le RSI à chaque cycle, journalise chaque décision, et laisse un système externe nommé Hermes (une CLI/cron, avec fallback local déterministe ou LLM) proposer des ajustements progressifs de la stratégie, une variable à la fois. Le projet appartient à Thomas (utilisateur), tourne en continu sur Railway (service `hermes-trading`) et est supervisé par un cron Hermes toutes les 30 minutes. Un système de « qualification » (phases `training` → `validation_ready` → `kraken_validate_only` → `micro_live_btc` → `live_*`) encadre une promotion progressive et auditable vers le trading réel, actuellement bloquée tant qu'aucun adaptateur d'exécution live n'existe.

## Stack technique

Python ≥3.11, géré avec `uv` (verrouillage via `uv.lock`, pas de `requirements.txt`). Dépendances principales déclarées dans `pyproject.toml` : `ccxt` (accès Kraken), `discord-py` (bot de contrôle/notifications), `httpx`, `pandas`/`numpy` (calculs), `pyyaml` (fichiers d'état), `yfinance` (données macro), `rich`, `aiofiles`. Persistance mixte : fichiers plats (`state/*.jsonl`, `*.yaml`, `*.json`) comme source de vérité opérationnelle et une base SQLite (`state/trading.db`, module `hermes_trading/persistence/`) avec migrations versionnées pour positions, ordres et réconciliation. Aucun framework web classique ; `control_api.py` et `control_server.py` exposent un petit serveur HTTP local (`http.server`) pour le contrôle, sans FastAPI/Flask. Aucun linter ni formatter n'est configuré dans `pyproject.toml` (pas de section `[tool.ruff]`, `[tool.black]`, ni dépendances de dev) — les conventions de code sont donc uniquement celles observées dans le code existant, pas imposées par un outil.

## Architecture et organisation

Monolithe Python à un seul package (`hermes_trading/`), sans découpage en microservices. Trois responsabilités séparées documentées dans `docs/ARCHITECTURE.md` : collecte de marché (`adapters/price.py`, `onchain.py`, `news.py`, `macro.py`, chacun retournant un payload versionné `schema_version: 1`), décision (`loop.py`, applique `state/strategy.yaml` avec surcharges optionnelles par symbole dans `symbols.<asset>`), et amélioration (`reflect.py`, propose une modification de stratégie via fallback déterministe local ou LLM externe). Autour de ce cœur : `execution.py`/`exchange.py` pour l'interaction Kraken (mode validation-only avant tout ordre réel), `risk.py`/`funds.py`/`costs.py`/`profitability.py` pour les garde-fous économiques, `qualification.py`/`qualification_runner.py`/`qualification_evidence.py` pour le pipeline de promotion live par preuves, `control.py`/`control_api.py`/`control_auth.py`/`control_client.py`/`control_server.py`/`discord_bot.py` pour un canal de commande à distance via Discord (authentification HMAC/nonce), et `persistence/` (repository + migrations SQLite) pour l'état durable des positions/ordres. `run.py` est le point d'entrée principal du worker ; `colocated.py` est un point d'entrée alternatif (utilisé par le `Dockerfile`) qui semble co-localiser plusieurs services. Le domaine métier (`domain.py`) impose l'usage de `Decimal` pour toute valeur financière et interdit explicitement les `float` binaires.

## Commandes essentielles

Installation et exécution locale (`uv` requis) :

```powershell
uv run python -m hermes_trading.run --interval-seconds 60
```

Tests (unittest standard, pas pytest) :

```powershell
uv run python -m unittest discover -s tests
```

Réflexion/ajustement de stratégie :

```powershell
uv run python -m hermes_trading.reflect --fallback   # logique locale déterministe
uv run python -m hermes_trading.reflect --hermes      # proposition via LLM/CLI hermes
```

Déploiement : `Dockerfile` (`python:3.11-slim`, installe `uv`, lance `uv run python -m hermes_trading.colocated`) déployé sur Railway (projet `awake-abundance`, service `hermes-trading`, volume persistant monté sur `/app/state`). Pas de CI configurée (`.github/workflows` absent). Le détail complet des commandes (Railway, Hermes cron, vérification live-readiness) est dans `docs/COMMANDS.md`.

## Conventions de code observées

Typage systématique avec `from __future__ import annotations`, dataclasses (`@dataclass`, souvent `frozen=True`) et `Enum` pour les états métier (`PositionStatus`, `OrderStatus`, `QualificationPhase`). Usage strict de `decimal.Decimal` pour toute donnée financière, avec rejet explicite des `float` (`_reject_binary_floats`). Pas de framework de validation externe (pydantic absent) ; validation manuelle via fonctions dédiées et exceptions typées (`SchemaError`, `UnsupportedSchemaError`, `OperatorConfigError`). Tests en `unittest` avec doubles de test dans `tests/fakes/` (ex. `kraken.py`) plutôt que des mocks génériques.

## Stratégie de tests

46 fichiers de test dans `tests/` (~8 300 lignes), couvrant chaque module métier (exécution, réconciliation, contrôle, notifications, qualification, persistence, risque, stratégie...) plus les scripts opérationnels (`test_operational_scripts.py`). Framework `unittest` (`python -m unittest discover -s tests`), pas de configuration de couverture visible. Beaucoup de dossiers `.test-tmp*` à la racine (état SQLite/JSON généré par des runs de test antérieurs) suggèrent des tests d'intégration qui écrivent sur disque dans des répertoires temporaires — ces dossiers ne sont pas nettoyés automatiquement et polluent la racine du dépôt (ils sont cependant ignorés par `.gitignore` via `.test-tmp/`, à l'exception de certaines variantes nommées `.test-tmp-*` qui ne matchent pas exactement ce pattern et pourraient être trackées par Git si ajoutées).

## Points d'attention / dette technique

**Corrigé le 2026-08-07** : les 62 répertoires `.test-tmp-*` (task3, task4, task5, task6, artifact-lock...) qui jonchaient la racine ont été supprimés, ainsi que le fichier vide `cut]`. Le pattern `.gitignore` a été généralisé de `.test-tmp/` à `.test-tmp*/` pour couvrir toutes les variantes de noms générées par les sessions de test (l'ancien pattern ne couvrait que le dossier exact `.test-tmp/`, laissant les ~60 autres répertoriés comme fichiers non suivis mais commitables par erreur via `git add -A`). L'ambiguïté `run.py`/`colocated.py` a été clarifiée dans `README.md` et `CLAUDE.md` : `run.py` est un point d'entrée minimal (boucle seule, pratique en local), `colocated.py` l'enveloppe et ajoute l'API de contrôle et le bot Discord optionnels — c'est ce dernier que lance le `Dockerfile` de production ; ce n'est pas une incohérence mais un empilement de responsabilités volontaire.

Restant à surveiller : aucun `TODO`/`FIXME` trouvé dans le code, ce qui est positif, mais aucun linter/formatter/mypy n'est configuré — la cohérence de style dépend entièrement de la discipline manuelle. Le dossier `graphify-out/` contient un graphe de connaissance généré avec historique quotidien (2026-07-28 à 2026-08-02) mais n'est pas suivi par Git (`git ls-files graphify-out` ne retourne rien) : il reste local malgré ce que suggère `AGENTS.md` ("knowledge graph ... versioned"), donc il ne pollue pas le dépôt distant, seulement le disque local. Plusieurs fichiers sensibles sont présents en clair à la racine du filesystem monté (`.env`, `.env.key`, `.env.control-discord.generated`, `.railway/id_ed25519`, `.home/.ssh/id_ed25519`) — ils sont bien listés dans `.gitignore` et n'ont pas été lus ni modifiés durant cette revue ; ils appellent une vigilance particulière si le dépôt est un jour partagé ou archivé tel quel. `AGENTS.md` et `docs/GRAPHIFY_OBSIDIAN.md` apparaissent modifiés dans `git status` sans changement de contenu réel (uniquement fins de ligne CRLF au lieu de LF, probablement dû à `core.autocrlf` sur le checkout Windows) ; non corrigé ici pour éviter un diff de fin de ligne massif non demandé — à trancher avec l'équipe si `git diff` doit rester silencieux sur ces fichiers.

## Simplification de la base de code (2026-08-07)

Passe de simplification à fonctionnalité inchangée, appuyée sur une analyse AST (pas de suppression au jugé) : détection des fonctions privées de niveau module jamais appelées ailleurs dans le dépôt, des imports jamais référencés, et des fonctions au corps strictement identique (comparaison par hash de l'AST) dupliquées entre fichiers.

Supprimé (code mort confirmé, zéro appelant) : `_append_jsonl` (`loop.py`), `_legacy_generate_asset_proposal` et `_legacy_normalize_strategy_proposal` (`reflect.py`, ancien format de proposition Hermes superseded), puis `_run_hermes_prompt` (`reflect.py`, devenu orphelin après la suppression de `_legacy_generate_asset_proposal`, son seul appelant — appel CLI `hermes` en subprocess, remplacé depuis par `ReflectionModelRunner`/`ProviderRouter`). Imports inutilisés supprimés : `Path` (`reconciliation.py`), `timedelta`, `subprocess` (`reflect.py`), `InvalidOperation` (`risk.py`, `market_data.py`), `ccxt` direct (`funds.py`).

Consolidé (fonctions au corps identique dupliqué entre modules, remplacées par un import partagé pour qu'elles ne puissent plus diverger silencieusement) : `_default_exchange` (gardé dans `adapters/execution.py`, importé par `funds.py`), `_finite_decimal`/`_cancel_and_await` (gardés dans `adapters/price.py`, réutilisés par `market_data.py` via alias `_decimal`/`_cancel_and_await`), `_decimal_env`/`_env_decimal` (gardé dans `config.py`, importé par `risk.py`), `_ensure_utc` (gardé dans `funds.py`, importé par `notifications.py`), `_load_trades`/`_load_jsonl` (doublon interne à `reflect.py`, fusionné en une seule fonction). Chaque direction d'import a été vérifiée statiquement pour exclure tout risque de cycle avant modification.

Résultat : 7 fichiers modifiés, 199 lignes supprimées pour 18 ajoutées (déclarations d'import), aucun changement de logique métier. `python3 -m py_compile` sur les 93 fichiers du dépôt : aucune erreur, et un nouveau passage du scan AST confirme zéro fonction privée orpheline, zéro import inutilisé et zéro corps de fonction dupliqué restant. Limite : comme lors de l'audit précédent, la suite `unittest` n'a pas pu être exécutée dans ce sandbox (pas de Python 3.11, réseau bloqué) — à relancer avec `uv run python -m unittest discover -s tests` depuis un terminal Windows local avant de committer, pour confirmer qu'aucun test ne dépendait du nom exact d'une fonction supprimée ou déplacée (recherché mais non garanti à 100% par un simple grep).

## Vérification de la base de code (2026-08-07)

`python3 -m py_compile` sur les 93 fichiers `.py` du dépôt (`hermes_trading/`, `scripts/`, `tests/`) ne remonte aucune erreur de syntaxe. En revanche, la suite `uv run python -m unittest discover -s tests` n'a pas pu être exécutée dans cet environnement : `uv` tente de télécharger un toolchain Python 3.11 (le projet exige `>=3.11` et utilise `datetime.UTC`, disponible seulement depuis 3.11) mais l'accès réseau à `github.com`/`pypi.org` est bloqué par l'allowlist du sandbox, et le seul Python système disponible est 3.10.12, insuffisant. Le `.venv` déjà présent dans le dépôt est un environnement Windows (`Scripts/`, pas `bin/`) inutilisable depuis ce sandbox Linux. Recommandation : lancer `uv run python -m unittest discover -s tests` depuis un terminal Windows local (le `.venv` existant devrait fonctionner directement) pour confirmer que la suite passe réellement après ce nettoyage.

## Intégrations externes

Kraken (via `ccxt`) pour les prix et, à terme, l'exécution d'ordres réels — actuellement bridée par un double garde-fou (`HERMES_TRADING_I_ACCEPT_RISK` + `HERMES_TRADING_LIVE_CONFIRM`) et un mode `validate_only` par défaut. Discord (webhook + bot `discord-py`) pour les notifications et un canal de commande à distance authentifié. Glassnode (on-chain, optionnel selon `GLASSNODE_API_KEY`) avec repli sur `blockchain.info`. NewsAPI (optionnel) avec repli sur le flux RSS CoinDesk. Yahoo Finance (`yfinance`) pour les indicateurs macro (`SPY`, `DX-Y.NYB`). Un ou plusieurs fournisseurs LLM pour la réflexion Hermes (`HERMES_REFLECTION_MODELS`, fallback OpenAI, fallback local type Docker Model Runner).

## Questions ouvertes / zones à clarifier

L'absence de linter/formatter configuré est-elle un choix assumé ou un oubli ? La présence de `.codex/hooks.json` référençant un exécutable Windows local (`graphify.EXE`) suggère un usage combiné Codex CLI + outil `graphify` propriétaire dont la disponibilité pour Claude Code reste à vérifier (voir section Notes & Gotchas du `CLAUDE.md`). Enfin, la suite de tests devrait être relancée depuis un environnement disposant de Python 3.11 (terminal Windows local avec le `.venv` existant, par exemple) pour confirmer qu'elle passe intégralement après le nettoyage effectué le 2026-08-07 — voir la section Vérification ci-dessus.
