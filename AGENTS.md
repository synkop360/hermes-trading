## Équipe d'agents Paperclip : règles (prioritaires)

Ces règles s'appliquent à tout agent de l'équipe Paperclip (Keola et son équipe : chef de projet, développeurs, relecture, publication). En cas de contradiction avec le reste de ce fichier ou avec `CLAUDE.md`, elles priment. Elles ne s'appliquent pas aux sessions que Thomas conduit lui-même.

### Branches et déploiement

- Railway déploie automatiquement la branche `master` : toute fusion vers `master` est un déploiement en production. **Les agents ne déploient jamais** : fusionner ou pousser vers `master` est réservé à Thomas.
- Un ticket = une branche = une pull request vers la branche d'intégration indiquée dans le ticket (`integration/*`). Seul le chef de projet fusionne dans `integration/*`, après acceptation par la relecture.
- La consigne « Always deploy before stopping » (section ci-dessous) ne vaut que pour les sessions de Thomas, pas pour l'équipe d'agents.

### Interdits

- Pas de mode `live`. Ne jamais définir ni modifier `HERMES_TRADING_I_ACCEPT_RISK`, `HERMES_TRADING_LIVE_CONFIRM`, `HERMES_TRADING_LIVE_VALIDATE_ONLY` ou `HERMES_TRADING_ALLOW_REAL_ORDERS`, ni les garde-fous qui les lisent.
- Ne pas modifier `docs/SAFETY.md`, `Dockerfile`, `Dockerfile.viewer` ni `.railway/`.
- Aucune commande `railway`, aucun appel réseau à Railway, à Kraken en mode authentifié, à Discord ou à un fournisseur de LLM. Aucune clé n'est fournie : utiliser les doubles de `tests/fakes/` et le mode de repli (`reflect --fallback`).
- Ne pas lire, créer ni committer `.env*`, `state*/`, `trades.jsonl`, `backups/` ou `market_data_cache/`, sauf dans les répertoires temporaires de test (`.test-tmp*/`).
- Ne pas lancer `graphify` et ne pas créer ni modifier `graphify-out/` (non versionné) : les sections « graphify » et « Knowledge graph » ne s'appliquent pas aux agents de l'équipe.
- Jamais de `push --force` ni de réécriture d'historique.

### Tests, commits et pull requests

- Le framework est `unittest`, pas `pytest`. Pour lancer un module depuis la racine : `PYTHONPATH=tests uv run python -m unittest test_<module>`. Chaque ticket nomme ses modules de test ; la CI `paper-cloud.yml` tourne sur chaque pull request.
- Reporter dans la pull request la ligne `Ran N tests` et `OK` ou `FAILED`.
- Messages au format Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`). Corps de la pull request : le ticket, le poste, les fichiers touchés, les tests lancés avec leur résultat, et ce qui reste à faire.

## graphify

## Owner deployment preference

The owner instructed on October 27, 2026: "Always deploy before stopping." This applies to sessions the owner runs himself, never to the Paperclip agent team (see the rules above). Finish authorized paper-only changes through verified deployment instead of stopping at a local commit or requesting deployment permission again. Preserve required checks, original experiment/history, risk limits and the single writer. This standing deployment instruction does not itself authorize real trading, paid calls, a live halt reset or unrelated heavy changes. If deployment cannot be completed safely, report the concrete blocker and actual installed state.

## Knowledge graph

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
