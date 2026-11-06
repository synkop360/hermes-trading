# LAT-34 — Preuve publique bornée des huit comptes paper

Date d'exécution : 2026-11-06 (UTC). Base de code : `origin/integration/lat-26` @ `d77c070`
(LAT-32 et LAT-33 fusionnés). Aucune modification de code : ce document est le seul livrable versionné.
Les artefacts runtime sont sous `.test-tmp-lat34/` (ignoré par git) dans le worktree LAT-34. Un paquet durable
est joint au ticket LAT-34 (voir « Paquet de preuves »).

Mode `paper` uniquement, données Kraken **publiques non authentifiées**, `model_access=not_used`,
réflexion `deterministic_worker`. Aucun live, Railway, Discord ni LLM. Aucune retouche manuelle de seuil, de signal ni de config ;
les évolutions automatiques du fallback adaptive (publications `entry.threshold` 30→28→…, enregistrées
dans `live-run1.json` et `replay-proof.run.json`) sont conservées telles quelles.

## Verdict

| volet | verdict |
|---|---|
| 1. Observation publique actuelle (9 min) | **INCONCLUSIF** — 0 entrée / 0 clôture (`not_eligible_zero_trade`) |
| 2. Rejeu chronologique d'un historique public | **PASS candidat** — 6 entrées / 6 clôtures sur 6 comptes ; `public_proof=candidate_requires_review_public_replay` |

Le PASS du rejeu est un **candidat soumis à revue** (Stella puis Thomas). Il ne valide pas à lui seul LAT-26.

## 1. Observation publique actuelle

```
uv run python -m hermes_trading.pilot_paper --root .test-tmp-lat34/live --run \
  --experiment-id lat34-live --max-cycles 4 --max-runtime-seconds 540 --interval-seconds 150
```

- 2026-11-06T14:24:12Z → 14:33:16Z (≈ 9 min), 4 cycles, `stop_reason=max_cycles_reached`.
- 4 échantillons `kraken_public_unauthenticated`, 0 entrée / 0 clôture sur les 8 comptes.
- manifest `d91ce4dbcd5a14b55e3e3909cdae111f01d52a35a58ca2f74d3ba867df53aa59`.
- Sortie : `.test-tmp-lat34/live-run1.json`.
- Une seconde observation (`live-run2.*`, fichiers vides) a été interrompue par l'expiration du run agent : **non utilisée**.

## 2. Rejeu chronologique public

### Archive (provenance)

```
uv run python -m hermes_trading.pilot_replay --root .test-tmp-lat34/replay
```

- Source `kraken_public_unauthenticated`, ETH/EUR, 1h, 255 bougies,
  2026-10-26T23:00Z → 2026-11-06T14:00Z, requête 2026-11-06T14:43:49Z (≈ 4,6 s).
- sha256 fichier `replay_archive.json` : `a75ccec9ae6871499c635f0770282062b885179e55d9f209a476fe7ddaa506ea`
- fingerprint : `7a99c69eeacbfea196887ae560b3f93569dc707a9bd139986462d5620b1e5fc7`
- série ETH/EUR : `2b694cbb3775f4ab7950502f9ed972b9535a8b522fe012800d24151d7f04251b`

### Session de preuve

```
uv run python -m hermes_trading.pilot_paper --root .test-tmp-lat34/replay-proof --run \
  --experiment-id lat34-replay-proof --replay-archive .test-tmp-lat34/replay/replay_archive.json \
  --max-cycles 23 --max-runtime-seconds 604800 --interval-seconds 3600
```

- Durée mesurée : 2026-11-06T14:57:02Z → 15:06:47Z (585 s).
- Temps simulé : 2026-10-29T02:00:01Z → 2026-10-30T00:00:01Z, 23 cycles, warmup 50 bougies,
  lag 1 s, exécution au close de la bougie courante, `stop_reason=max_cycles_reached`, `error=null`.
- manifest `4efe77d9c0549979477d73e66ad12dd03fe1ef7c2a37122d9de3a0f640547992`.
- Événements : achat 2026-10-29T02:00:01Z @ 2071.78 (`rsi_entry`), vente 2026-10-29T21:00:01Z
  @ 2019.86 (`stop_loss`), identiques sur les 6 comptes 100/150/200.

| compte | décisions enter/exit/hold/no_trade | fills | frais EUR | PnL réalisé EUR | équité nette / initiale | inventaire final |
|---|---|---|---|---|---|---|
| adaptive_50 | 0/0/0/23 | 0 | 0.0000 | 0.0000 | 50.0000 / 50 | vide |
| adaptive_100 | 1/1/18/3 | 2 | 0.0000 | -0.1253 | 99.8747 / 100 | vide |
| adaptive_150 | 1/1/18/3 | 2 | 0.0000 | -0.1253 | 149.8747 / 150 | vide |
| adaptive_200 | 1/1/18/3 | 2 | 0.0000 | -0.1253 | 199.8747 / 200 | vide |
| control_50 | 0/0/0/23 | 0 | 0.0000 | 0.0000 | 50.0000 / 50 | vide |
| control_100 | 1/1/18/3 | 2 | 0.0000 | -0.1253 | 99.8747 / 100 | vide |
| control_150 | 1/1/18/3 | 2 | 0.0000 | -0.1253 | 149.8747 / 150 | vide |
| control_200 | 1/1/18/3 | 2 | 0.0000 | -0.1253 | 199.8747 / 200 | vide |

Comptes 50 EUR (raisons `no_trade` dans `replay-proof/<compte>/decisions.jsonl`, minimum de la venue
non atteint) :
- `adaptive_50` : 22 `entry_condition_not_met` + 1 `below_current_venue_minimum` (2026-10-29T02:00:01Z).
- `control_50` : 21 `entry_condition_not_met` + 2 `below_current_venue_minimum` (2026-10-29T02:00:01Z et 10:00:01Z).

Sorties : `.test-tmp-lat34/replay-proof.run.json`, `replay-proof.init.json`, `replay-proof.accounts.md`,
état complet sous `.test-tmp-lat34/replay-proof/`.

### Essais préalables (transparence)

- `replay-trial` (même archive, `--max-cycles 5`) : 6 entrées / 0 clôture → `not_eligible_zero_trade`,
  manifest `f8d2aabd…2ea5d`. Il a servi à vérifier la chaîne avant la session de preuve.
- `replay-run` : lancement interrompu, fichiers vides, non utilisé.
- La longueur de 23 cycles a été choisie **après** cet essai : la fenêtre n'est pas pré-enregistrée.
  Le point de départ (premier cycle après le warmup de 50 bougies) est déterminé par l'archive.

## Durées murales (traces de chronométrage)

Mesures issues des horodatages système des fichiers (`TIMING_TRACES.txt` dans le paquet) : début = création
des fichiers de sortie redirigés au lancement, fin = dernier fichier écrit. Les JSON du rejeu portent une
horloge simulée et ne mesurent pas la durée murale.

| exécution | début (UTC) | fin (UTC) | durée | statut |
|---|---|---|---|---|
| live-run1 (observation) | 14:24:06 | 14:33:16 | ≈ 9 min 10 s | utilisé |
| live-run2 (observation) | 14:33:22 | ≥ 14:42:07 (dernier fichier écrit) | ≈ 8 min 45 s | interrompu, non utilisé |
| replay-fetch (archive) | 14:43:48 | 14:43:52 | ≈ 4 s | utilisé |
| replay-trial (5 cycles) | 14:44:08 | 14:46:22 | ≈ 2 min 14 s | essai, non utilisé comme preuve |
| replay-run | 14:46:29 | ≥ 14:56:41 (dernier fichier écrit) | ≈ 10 min 12 s | interrompu, non utilisé |
| replay-proof (23 cycles) | 14:57:02 | 15:06:47 | ≈ 9 min 45 s | utilisé |

Observation publique cumulée ≈ 18 min (≤ 30 min) ; rejeu cumulé (archive + essais + preuve) ≈ 22 min
(≤ 60 min) ; total ≈ 40 min sur le budget de 90 min. Les fins des runs interrompus sont des bornes basses
(dernier fichier écrit), pas l'instant exact de l'arrêt.

## Paquet de preuves

Joint au ticket LAT-34 (pièce jointe Paperclip `2201371f-e2a4-4438-a1bd-9c96d11662e5`,
`/api/attachments/2201371f-e2a4-4438-a1bd-9c96d11662e5/content`) :
`lat34-proof-artifacts.zip`, 1 890 263 octets, sha256
`e505d70bbb06645ef0a071e399a495a289a339f970b209d86c0861993def4381`.

Contenu : copie intégrale de `.test-tmp-lat34/` — archive publique exacte `replay/replay_archive.json`
(sha256 `a75ccec9…a506ea`), `replay-fetch.json`, manifestes, sorties `*.init.json`/`*.run.json`,
`replay-proof.accounts.md`, état des 8 comptes (décisions, ordres, fills, trades, positions, coûts,
bases SQLite) pour `live`, `replay-trial`, `replay-run` et `replay-proof`, `TIMING_TRACES.txt` et
`SHA256SUMS` (sha256 de chaque fichier). Aucun secret : aucun identifiant ni jeton (recherche
`api_key|secret|token|password|Authorization` sans résultat). Le rejeu est reproductible hors ligne avec
l'archive du paquet (`--replay-archive`) ; un nouvel appel public ne garantit pas la même archive.

## Réserves

1. **Frais nuls** : la configuration pilote déclare `maker_fee_rate`/`taker_fee_rate = '0'`. Non modifiée
   (hors périmètre) ; le PnL réel serait plus négatif.
2. **adaptive = control** sur cette fenêtre : aucune différenciation observée entre les deux familles.
3. Notionnel identique (~5 EUR) sur 100/150/200 : le PnL ne croît pas avec le capital.
4. Rejeu : `venue_timestamp_basis=restamped_at_cycle_instant` ; les données sont publiques mais rejouées,
   pas observées en temps réel.
5. Comptes 50 EUR jamais exposés : la preuve ne couvre pas ce palier.

## Tests

```
PYTHONPATH=tests uv run python -m unittest test_pilot_replay   # Ran 20 tests in 106.807s — OK
PYTHONPATH=tests uv run python -m unittest test_pilot_paper    # Ran 29 tests in 637.138s — OK
```

`test_pilot_paper` dépasse 10 min (637 s) ; à découper via `-k` lors des prochaines exécutions.
