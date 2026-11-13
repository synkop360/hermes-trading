# Reprise du stockage et levée manuelle des verrous d'entrée — 13 novembre 2026

## Incident

- 02:45:56 UTC : l'espace libre du volume `/app/state` (500 Mo) passe sous la réserve (134 180 864 o libres < 134 217 728 o). Le worker passe en `storage_stop` ; `adaptive_200/experiment_halt.json` est écrit avec la raison `storage_stop`.
- Pendant l'arrêt, la sauvegarde quotidienne échoue (`daily_backup_failed`) et le superviseur enregistre des erreurs. Les deux compteurs atteignent 3 et les verrous `worker_error_halt` et `supervisor_error_halt` se posent.
- Le heartbeat affiche `model_ready=false`. L'accès au modèle n'était pas en cause.

## Reprise du stockage (PR #22, déploiement 9566fd58)

- `scripts/paper_compact_history.py` compacte la sauvegarde obsolète `hypotheses.jsonl.pre-repair-39815ad5…` : 15 858 945 o deviennent 820 430 o gzip. L'archive et son reçu sont dans `.legacy-history-compact/`.
- `scripts/paper_cloud_maintenance.py` appelle `recover_storage` avec le runtime installé `33f6c632…` et l'étiquette `after-2026-11-13-reserve`. Le reçu est dans `.paper-storage-recovery-after-2026-11-13-reserve/receipt.json`. Le marqueur d'arrêt est conservé en `retired-halt.json`.
- Résultat à 07:02 UTC : `status=ok`, `model_ready=true`, stockage `ready`, 149 196 800 o libres.

## Levée manuelle des verrous worker et superviseur

Le code ne lève jamais ces verrous sur un succès. `recover_supervisor` exige un worker non verrouillé, et il n'existe aucun chemin audité pour le worker. Le propriétaire a autorisé une levée manuelle le 13/11 à 08:13 (Paris), avec la référence d'approbation `user-2026-11-13T08:13+01:00-manual-latch-release`.

**Méthode**

Un script ponctuel est exécuté au démarrage, via deux variables de service. `HERMES_MANUAL_LATCH_RELEASE_B64` contient le script de levée. `HERMES_MANUAL_LATCH_WRAP_B64` contient une enveloppe qui exécute ce script et consigne son résultat dans `/app/state/.manual-latch-release-attempt.json`. La commande de démarrage est :

```
/bin/sh -c 'export HERMES_TRADING_MODE=paper HERMES_TRADING_ALLOW_REAL_ORDERS=false; cd /app && printenv HERMES_MANUAL_LATCH_WRAP_B64 | base64 -d | /app/.venv/bin/python -u - ; exec /app/.venv/bin/python -u -m scripts.paper_cloud_maintenance'
```

Point d'attention : un `redeploy` Railway ne reprend pas une commande de démarrage ni des variables modifiées entre-temps. Il a fallu un nouveau déploiement depuis la source (déploiement 54ebd37f).

Le script de levée procède ainsi :

- il prend le même verrou de fichier que `record_worker_result` et `record_supervisor_result` ;
- il exige pour chaque ligne `success=true`, `entry_halt=true` et `consecutive_errors=3` ; sinon il refuse ;
- il copie l'état d'origine dans `.manual-latch-release-2026-11-13/before.json` ;
- il passe `entry_halt` à false et `consecutive_errors` à 0 pour les deux lignes, et ne touche à rien d'autre ;
- il relit et valide l'état avec `_state`, puis écrit `receipt.json` avec les hachages avant et après ;
- relancé, il ne fait rien dès que le reçu existe.

**Résultat**

- Levée à 07:19:08 UTC. Le reçu `.manual-latch-release-2026-11-13/receipt.json` contient `before_sha256` 18cf59f2…640cad et `after_sha256` e0b49f23…6aa9. L'état d'origine est conservé dans `before.json`.
- Heartbeat à 07:19:31 UTC : `entry_admission.blocked=false`, aucune raison.
- Le redémarrage suivant (agrandissement du volume) a répondu `already_released`, sans nouvelle modification.

**Volume**

Le volume est passé de 500 Mo à 1 Go à 07:21 UTC (patch 339ab8c9). L'espace libre est de 609 Mo à 07:21:39 UTC.

**Ce qui n'a pas changé**

Portefeuilles, positions (toutes à plat), historiques, stratégies, limites de risque et de stockage, politique `paper_safety.json`. Le mode reste `paper`, avec `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.

## Suites (lot Keola)

- Levée automatique et auditée du verrou worker sur le modèle de `recover_supervisor`, puis suppression de la variable et retour à la commande de démarrage standard.
- Rétention structurelle des sqlite de publication, d'equity et de coûts, projection de croissance et alerte avant la réserve.
