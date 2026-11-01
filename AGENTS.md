## Équipe d'agents : règles du pilote (prioritaires)

Ces règles s'appliquent à tout agent de l'équipe Paperclip (chef de projet, développeur, relecteur-testeur). En cas de contradiction avec le reste de ce fichier ou avec `CLAUDE.md`, elles priment.

### Périmètre

- Objectif du pilote : terminer la mise à jour du paper trading et prouver qu'une session paper produit des résultats. La qualité de ces résultats n'est pas un critère.
- Ne travailler que sur le ticket assigné : un ticket, une branche, une pull request vers `feat/live-hardening`. Toute autre amélioration repérée devient une proposition écrite dans le ticket, pas du code.
- La branche `wip/paper-update-codex` contient un travail inachevé et non relu. C'est une source à évaluer, pas une base à fusionner telle quelle.

### Interdits

- Pas de mode `live`. Ne jamais définir ni modifier `HERMES_TRADING_I_ACCEPT_RISK`, `HERMES_TRADING_LIVE_CONFIRM`, `HERMES_TRADING_LIVE_VALIDATE_ONLY` ou `HERMES_TRADING_ALLOW_REAL_ORDERS`, ni les garde-fous qui les lisent.
- Ne pas modifier `docs/SAFETY.md`, `Dockerfile`, `Dockerfile.viewer` ni `.railway/`.
- Aucune commande `railway`, aucun appel réseau à Railway, à Kraken en mode authentifié, à Discord ou à un fournisseur de LLM. Aucune clé n'est fournie : utiliser les doubles de `tests/fakes/` et le mode de repli (`reflect --fallback`).
- Ne pas lire, créer ni committer `.env*`, `state*/`, `trades.jsonl`, `backups/` ou `market_data_cache/`, sauf dans les répertoires temporaires de test (`.test-tmp*/`).
- Ne pas lancer `graphify` et ne pas modifier `graphify-out/`. La section « graphify » plus bas ne s'applique pas aux agents de l'équipe.
- Jamais de `push --force`, de réécriture d'historique ni de fusion : seul Thomas fusionne.

### Tests

- Le framework est `unittest`, pas `pytest`. Pour lancer un module depuis la racine : `PYTHONPATH=tests uv run python -m unittest test_<module>`.
- Chaque ticket nomme ses modules de test : lancer ceux-là, pas la suite complète, qui dure environ une heure. La suite complète est lancée avant la fusion.
- Défaut connu, hors périmètre : `test_quote_checkpoint` échoue sur le délai d'admission des archives (`CapacityUnavailable`). Ne pas le corriger sans ticket dédié.
- Reporter dans la pull request les résultats exacts : la ligne `Ran N tests` et `OK` ou `FAILED`.

### Commits et pull requests

- Messages au format Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`), comme le reste de l'historique.
- Corps de la pull request : le ticket, le poste, les fichiers touchés, les tests lancés avec leur résultat, et ce qui reste à faire.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
