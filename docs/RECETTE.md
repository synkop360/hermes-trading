# Recette — service `scripts.recette_service`

Point d'entrée de l'environnement Railway `recette` (service `hermes-recette`),
livré par LAT-117 (lot 1, ticket 2). Il initialise le pilote paper à huit comptes
(`pilot_paper`, voir `docs/PAPER_PILOT.md`) sur un volume vide, puis le fait
fonctionner en boucle par sessions bornées. Paper uniquement : aucun secret, aucun modèle,
aucun Discord, aucun checkpoint cloud, seulement le marché public Kraken non
authentifié.

## Commande Railway (déjà configurée, ne pas modifier)

Le service `hermes-recette` est configuré par Thomas, hors dépôt. Pour
référence uniquement (l'équipe n'exécute aucune commande Railway) :

```sh
/bin/sh -c 'export HERMES_TRADING_MODE=paper HERMES_TRADING_ALLOW_REAL_ORDERS=false; exec /app/.venv/bin/python -u -m scripts.recette_service'
```

- volume de 1 Go monté sur `/app/state` (racine par défaut du service) ;
- variables : `HERMES_TRADING_MODE=paper`, `HERMES_TRADING_ALLOW_REAL_ORDERS=false`,
  `HERMES_NOTIFY_ENABLED=false`, `HERMES_RECETTE=true`.

Le raccordement de la source `hermes-recette` à la branche `integration` est
fait par Thomas ou Claude après la fusion de ce ticket ; ensuite chaque push sur
`integration` redéploie la recette.

## Démarrage

À chaque démarrage, dans cet ordre, avant toute écriture :

1. **Posture paper.** Refus si `HERMES_TRADING_MODE` est défini à autre chose
   que `paper`, si `HERMES_TRADING_ALLOW_REAL_ORDERS` est défini à autre chose
   que `false`, ou si `HERMES_NOTIFY_ENABLED=true`. Le service lit ces valeurs
   sans jamais définir ni modifier aucune des quatre variables de garde-fou.
2. **Variables interdites, par leur nom.** Toute variable dont le nom commence
   par `EXCHANGE_`, `HERMES_CLOUD_CHECKPOINT_` ou `HERMES_DISCORD_` est refusée,
   même vide. Seuls les noms sont lus et affichés, jamais les valeurs.
3. **Provenance du volume.**
   - Volume absent ou vide (un `lost+found` vide est toléré) : initialisation
     des huit comptes dans `/app/state/.test-tmp-pilot`, puis écriture du marqueur
     `/app/state/recette_provenance.json`. La racine du volume est un point de
     montage : elle n'est jamais supprimée.
   - Volume non vide : reprise uniquement si le marqueur existe, est lisible, est
     compatible (`schema_version: 1`, `kind: hermes_recette_volume`,
     `service: scripts.recette_service`, `experiment_id: pilot-paper-recette`,
     `pilot_root: .test-tmp-pilot`) et si `.test-tmp-pilot/manifest.json` existe.
     Sinon : refus, le
     volume n'est pas touché.
   - Le marqueur est écrit après le bootstrap : un arrêt brutal entre les deux
     laisse un volume sans marqueur, refusé au redémarrage (jamais à moitié
     reconnu). Il faut alors vider le volume, ce qui relève de Thomas.

Hors recette (`HERMES_RECETTE` absent), la racine du volume doit elle-même
rester sous un répertoire `.test-tmp*` : un lancement local ne peut pas
initialiser un pilote à côté d'un état réel.

Le pilote est placé dans `.test-tmp-pilot` pour que `hermes_trading.pilot_paper`
l'accepte **sans modification** : son confinement `.test-tmp*` est conservé tel
quel. Le paquet `hermes_trading` est épinglé par empreinte dans les releases
(`paper_publication._release_hashes`, `CURRENT_RUNTIME`, cartes de sources) ;
le modifier imposerait de ré-épingler l'identité du runtime de production, hors
périmètre de ce ticket. Le service de recette vit donc entièrement dans
`scripts/`.

Le marqueur porte aussi `code_version` (version du paquet installé) et
`created_at` (UTC). `code_version` est informatif : il n'est pas vérifié à la
reprise, pour qu'un redéploiement d'`integration` reprenne le même volume. Il
vaut `unknown` dans l'image (projet « virtuel » dans `uv.lock`, jamais installé)
et n'identifie de toute façon aucun commit : l'identité du code exécuté est
l'empreinte `runtime` (section suivante).

## Empreinte du code exécuté (LAT-141)

Code dans `scripts/recette_fingerprint.py`. Au démarrage, avant toute session,
le service calcule une fois l'empreinte des sources **importées par le
processus** et la publie dans chaque rapport (`runtime`) et dans la ligne
`recette_service_start` (`runtime_fingerprint`, `runtime_status`).

| Composant        | Fichiers                                              |
| ---------------- | ----------------------------------------------------- |
| `hermes_trading` | tous les `*.py` du paquet importé                     |
| `scripts`        | tous les `*.py` du paquet `scripts` importé           |
| `dependencies`   | `pyproject.toml` et `uv.lock` (à côté de `scripts/`)  |

Ce sont exactement les entrées de l'image (`.dockerignore`). Règle : chemin
relatif POSIX → SHA-256 des octets, la même que
`paper_publication._release_hashes` ; le condensé du composant
`hermes_trading` est donc celui de la carte épinglée des releases. Condensé
d'un composant = SHA-256 du JSON canonique (clés triées, séparateurs `,` `:`)
de sa carte ; `runtime.fingerprint` = SHA-256 du JSON canonique
`{"algorithm": "hermes-recette-runtime-v1", "components": {nom: condensé}}`.
Les répertoires `__pycache__` et `.test-tmp*`, la documentation, les tests et
les fichiers non Python de `scripts/` n'en font pas partie. Un lien
symbolique ou un composant absent donne `runtime.status: unavailable` (avec
`reason`), jamais une empreinte partielle.

Le rapport ne publie que `schema_version`, `status` (`identified` ou
`unavailable`), `algorithm`, `fingerprint`, `short` (12 premiers caractères)
et, par composant, `files` et `sha256` : aucun chemin, aucune valeur de
variable, aucun contenu. Une empreinte mal formée ou incohérente n'est jamais
recopiée (`unavailable`, `reason: unrecognized`).

**Rapprocher la recette d'une livraison.** Sur le commit de la PR (ou le commit
de fusion dans `integration`), sans checkout :

```sh
uv run python -m scripts.recette_fingerprint --git-rev <sha> --expect <runtime.fingerprint>
```

Code 0 et `"matches": true` si le code exécuté est celui du commit ; code 1
sinon. `--source <répertoire>` calcule la même valeur depuis un checkout,
`--files` liste les cartes `{chemin relatif: sha256}` pour localiser un écart.
Sans option, la commande donne l'empreinte des sources locales. L'empreinte
identifie les sources, pas la plateforme : une variable de commit fournie par
Railway n'est pas utilisée (elle relèverait d'une décision de Thomas).

## Absences explicites du parcours pilote

La recette exécute `pilot_paper.run_pilot_session`, jamais
`paired_worker.run_worker` (analyse LAT-140). Le rapport le dit au lieu de se
taire :

- `worker_recovery` : `{"status": "not_applicable", "reason":
  "pilot_has_no_worker_latch", "proven_by": "ci_worker_replay"}` — le pilote
  n'a pas de politique `paper_safety`, aucun verrou worker ne peut être posé ;
- `history_retention` : `{"status": "not_applicable", "reason":
  "retention_runs_in_paired_worker_only", "proven_by": "ci_worker_replay"}` —
  `apply_retention` n'est appelée que par le worker.

Ces deux preuves sont déplacées (décision de Thomas du 20/11, option C) vers
le rejeu CI de `paired_worker` (LAT-142), puis l'observation à leur première
exécution en production. La recette ne les présente jamais comme prouvées.

## Boucle

Chaque passe est une session `pilot_paper.run_pilot_session` bornée :

| Variable                              | Défaut  | Limite (`pilot_paper`) |
| ------------------------------------- | ------- | ---------------------- |
| `HERMES_RECETTE_MAX_CYCLES`           | 24      | 1 à 500                |
| `HERMES_RECETTE_MAX_RUNTIME_SECONDS`  | 86400   | ≤ 7 jours              |
| `HERMES_RECETTE_INTERVAL_SECONDS`     | 3600    | 1 s à 24 h             |
| `HERMES_RECETTE_REFLECT_EVERY`        | 1       | entier ≥ 1             |

Les mêmes réglages existent en options (`--max-cycles`, `--max-runtime-seconds`,
`--interval-seconds`, `--reflect-every`, `--root`). La réflexion est le repli
local déterministe (`reflect --fallback`, `run_fallback_reflection`) sur le
compte adaptatif : aucun fournisseur LLM. Un intervalle sépare deux passes.

Chaque événement est une ligne JSON sur la sortie standard :
`recette_service_start` (volume `initialized` ou `recognized`, bornes,
versions, empreinte `runtime_fingerprint`, `storage_limits_source`), `recette_pass` (raison d'arrêt, cycles, échantillons, entrées,
sorties), `recette_service_stop`, et pour le rapport
`recette_report_endpoint` (port d'écoute) et `recette_report_error` (code seul).

## Arrêt et codes de sortie

- Fichier `/app/state/.test-tmp-pilot/STOP` : arrêt propre avant la passe suivante ou
  pendant une session, code 0. Le retirer pour reprendre.
- Refus (posture, variable interdite, volume inconnu, bornes invalides) ou
  session terminée hors de ses bornes (`collection_unavailable`,
  `engine_halted`, `error`…) : message `refused: …` sur la sortie d'erreur,
  code 2. Au redémarrage, le volume identifié est repris.
- Interruption : code 130.

## Rapport de recette (`schema_version: 1`)

Livré par LAT-118 (lot 1, ticket 3), code dans `scripts/recette_status_report.py`.

### Production

Le service écrit `/app/state/recette_status.json` (racine du volume, hors du
pilote) :

| `heartbeat.event` | Moment                                                    |
| ----------------- | --------------------------------------------------------- |
| `start`           | volume reconnu ou initialisé, avant la première session   |
| `cycle`           | après chaque cycle suivi d'une attente dans une session   |
| `tick`            | toutes les 15 min pendant une attente plus longue (LAT-122) |
| `pass`            | après chaque session bornée (couvre son dernier cycle)    |
| `stop`            | fichier `STOP` constaté, arrêt propre                     |
| `error`           | session hors bornes, exception du pilote, volume inconnu  |

Écriture atomique : fichier temporaire voisin, `fsync`, puis `os.replace` ; un
lecteur voit l'ancien rapport complet ou le nouveau, jamais un fichier partiel.
Une cible remplacée par un lien symbolique est refusée. Taille bornée à 64 Kio.
L'échec d'écriture du rapport n'interrompt jamais la session paper : il est
signalé par une ligne `recette_report_error` (code seulement).

### Schéma

Clés de premier niveau, toutes toujours présentes :

- `schema_version` (`1`), `kind` (`hermes_recette_status`), `generated_at` (UTC ISO 8601) ;
- `heartbeat` : `at`, `event`, `interval_seconds`, `stale_after`
  (`at` + intervalle + 900 s), `pass` (numéro de session du processus),
  `cycle` (cycles de la session en cours), `code_version` ;
- `runtime` (LAT-141) : empreinte du code exécuté, voir « Empreinte du code
  exécuté » ; `{"status": "unavailable", "reason": "not_provided"}` hors service ;
- `volume` (LAT-141) : `status` (`initialized` au premier démarrage,
  `recognized` à la reprise, `unknown` hors service), `marker_created_at`
  (`created_at` du marqueur : identité du volume), `service_started_at`
  (démarrage du processus courant) ;
- `bounds` (LAT-141) : par borne de session (`max_cycles`,
  `max_runtime_seconds`, `interval_seconds`, `reflect_every`), `value` et
  `source` (`argument`, `environment` ou `default`) ; `{}` hors service ;
- `worker_recovery`, `history_retention` (LAT-141) : absences explicites, voir
  « Absences explicites du parcours pilote » ;
- `experiment_id`, `manifest_status` (`pilot_bootstrap`, `paper_ready`, `missing` ou `unrecognized`) ;
- `session` : dernière `pilot_session.json` terminée (`status: none` sinon) —
  `started_at`, `finished_at`, `stop_reason`, `error_code`, `completed_cycles`,
  `samples`, `entries`, `exits`, `public_proof` ;
- `entry_admission` : admission d'entrée `paper_safety.entry_status` du pilote —
  `policy` (`absent`, `present`, `unreadable`), `enabled`, `blocked`, `reasons`
  (codes) ; une mesure impossible bloque ;
- `storage` : `status` (`ready`/`blocked`), `reason`, `limits_source`
  (`recette_default` : budget = taille du volume, réserve = max(2 paquets
  maximaux, 1/10 du volume) ; `local_demo` : limites de démonstration locale,
  jamais en recette, voir « Démonstration locale des alertes »), `state_bytes`, `free_bytes`, `reserve_bytes`,
  `max_state_bytes`, `files` — mesure `paired_storage_admission.inspect_storage`
  sur le pilote ;
- `growth_and_projection` : même contrat que `heartbeat['storage_projection']`
  du worker (`hermes_trading.paper_history_retention.storage_projection`, un
  échantillon par jour UTC conservé 31 jours sous `.history-retention/`) —
  `schema_version` 1, `status` (`projected`/`non_positive_growth`/
  `insufficient_data`/`reserve_reached`/`unavailable`), `bytes_per_day`
  (entier), `days_before_reserve` = `headroom_bytes // bytes_per_day` avec
  `headroom_bytes = max(0, min(free_bytes - reserve_bytes, max_state_bytes -
  reserve_bytes - state_bytes))`, `sample_days` ; sous la réserve,
  `reserve_reached` et 0 jour ;
- `daily_summary` : résumés quotidiens permanents lus dans
  `hermes_trading.paper_history_retention.daily_summaries(pilot)` —
  `status` (`ready`/`unavailable`), `accounts` par compte
  `<bras>_<portefeuille>` (`days`, `decisions`, `last_date`, `last_balance`,
  `last_costs`) et `total_decisions` (compteur de cycles préservé après
  archivage) ;
- `locks` : `active` (liste triée) et booléens `stop_requested`,
  `worker_entry_halt`, `supervisor_entry_halt`, `supervisor_recovery_pending`,
  `engine_halted` ;
- `recent_errors` : au plus 10 `{at, source, code}`, les plus récentes d'abord ;
- `accounts` : `count`, `expected` (8) et, par compte `<bras>_<portefeuille>`,
  `arm`, `wallet_eur`, `currency`, `starting_balance`, `status`
  (`not_observed` avant la première mesure), `net_equity` (texte décimal),
  `observed_at`, `error_code`.
- `notifications` (LAT-122, voir « Alertes et cadence ») : `{"status": "disabled"}`
  quand le rapport est construit hors service ; sinon `status` (`ready` ou
  `unavailable` avec `reason` en code), `state` (`fresh`, `resumed`,
  `reset_invalid`), `transport` (`local_outbox`), `alert_delay_seconds` (900),
  `projection_alert_days` (7), `alerts` par type (`active`, `occurrences`,
  `emitted`, `suppressed_total`, `pending_suppressed`, `last_emitted_at`),
  `balances` (`slot_seconds` 900, `proposed`, `last_proposed_at`), `messages`,
  `outbox`, `restarts`, `transport_errors`. Les compteurs correspondent à
  l'évaluation du rapport qui les publie.

Expurgation : aucun chemin absolu, aucune valeur de variable, aucun message
d'exception, aucun contenu de stratégie. Un code n'est publié que s'il figure
dans la liste explicite `KNOWN_CODES` de `scripts/recette_status_report.py` ou
s'il est un nom de classe d'exception de la liste explicite `KNOWN_ERROR_CLASSES`
(exceptions standard de Python et classes des chemins pilote, portefeuille,
stockage et recette), le texte après `:` étant supprimé ; toute autre chaîne,
même de la forme `QuelqueChoseError`, devient `unrecognized`. Aucun autre champ
texte n'est recopié ni tronqué : les horodatages (`started_at`, `finished_at`,
`observed_at`, `recent_errors[].at`) doivent être des dates ISO 8601 avec fuseau
et sont normalisés en UTC, sinon `null` ; `experiment_id`
(`[a-z0-9][a-z0-9_-]{1,63}`), `currency` (trois majuscules) et `code_version`
(version numérique ou `unknown`) sont publiés seulement s'ils respectent leur
motif, sinon `null` ; `manifest_status` hors liste devient `unrecognized` ; les
compteurs et tailles négatifs ou non entiers deviennent `null`. Les fichiers lus doivent
être réguliers : une FIFO, un périphérique ou un lien est refusé sans attente.

### Endpoint en lecture seule

Si `PORT` est défini (Railway le fournit au service exposé) ou si l'option
`--http-port` est fournie, le service écoute sur `0.0.0.0:<port>`. Sinon, le
rapport est uniquement disponible dans le fichier local. Une valeur invalide est
refusée avant toute écriture.

- `GET /recette` / `HEAD /recette` : `200`, le rapport plus `freshness`
  (`served_at`, `age_seconds`, `stale`). `stale` est vrai après `stale_after`,
  si l'horodatage est futur ou illisible.
- Rapport absent, illisible, lien symbolique ou d'un autre schéma : `503`,
  `freshness.stale: true`.
- Tout autre chemin : `404` (le chemin de la requête n'atteint jamais le
  système de fichiers : seul le fichier fixe est lu). Toute autre méthode :
  `405`, `Allow: GET, HEAD`. `Cache-Control: no-store`.

L'exposition publique (domaine Railway) relève de Thomas. La preuve de 24 h
de rapports sains se collecte en recette, après les tickets 5, 6 et 7 ; les
tests locaux ne la remplacent pas.

## Alertes et cadence (LAT-122)

Code dans `scripts/recette_notifications.py`, appelé par le service à chaque
publication du rapport. Deux flux indépendants, jamais confondus :

- **Alertes de stockage, par type.**
  - `storage_projection_below_7_days` : `growth_and_projection.status` vaut
    `projected` ou `reserve_reached` et `days_before_reserve < 7`, strictement
    (`days_before_reserve` est l'entier `headroom // bytes_per_day` : pour un
    ratio positif, `ratio < 7` équivaut à `floor(ratio) < 7` ; 6 alerte, 7 non).
  - `storage_projection_unavailable` : projection `unavailable` ou mal formée.
    `insufficient_data` et `non_positive_growth` sont silencieux.
  - Regroupement : la première occurrence d'un type est émise ; les suivantes du
    même type moins de 15 min (900 s) après sa dernière émission sont
    supprimées et comptées. L'émission suivante (nouvelle occurrence à 900 s ou
    plus, ou, si la condition a cessé, vidage du compte en attente une fois le
    délai écoulé) porte le compte exact `suppressed`. Chaque type a son propre
    délai et son propre compteur. Une horloge qui recule met fin au délai : une
    alerte peut se répéter, jamais se taire.
- **Soldes, toutes les 15 minutes.** Les soldes des huit comptes (`net_equity`,
  `currency`, déjà expurgés par le rapport) sont proposés une fois par tranche
  UTC de 900 s (`floor(epoch / 900)`), quelle que soit l'activité des alertes.
  Des tranches manquées pendant un arrêt donnent une seule proposition de
  rattrapage. Pour que la cadence tienne avec des cycles d'une heure, les
  attentes du service (dans une session et entre deux sessions) sont découpées
  en tranches d'au plus 900 s, la durée totale étant inchangée ; chaque tranche
  intermédiaire publie un rapport `tick`. Un `session_runner` injecté sans
  paramètre `on_tick` garde son contrat d'origine.

État persistant : `/app/state/recette_notifications.json` (racine du volume,
hors pilote, 128 Kio au plus), écrit par fichier temporaire, `fsync` puis
`os.replace`, **avant** tout appel de transport. Un redémarrage garde les délais,
les comptes en attente et la tranche de soldes courante : il ne répète ni une
alerte dans son délai ni une tranche déjà proposée (`restarts` incrémenté). Un
état illisible, lien ou mal formé repart à zéro avec `state: reset_invalid`
(une alerte peut alors se répéter une fois). Un échec de l'état ou du transport
n'interrompt jamais la session ni le rapport (`notifications.status:
unavailable`, `transport_errors`).

Transport : la seule sortie en recette est la boîte locale bornée (32 derniers
messages) dans ce même fichier, d'où `transport: local_outbox`. Aucun appel
réseau, aucun Discord, aucune variable `HERMES_DISCORD_*` (toujours refusées au
démarrage) ; les credentials Discord et l'activation en production sont hors
périmètre. Les tests injectent `tests/fakes/notifications.RecordingTransport`.
La démonstration réelle en recette (compteurs visibles sur `GET /recette`
pendant la période probante) reste distincte des tests synthétiques et relève
de l'accord de Thomas.

## Tests

```sh
PYTHONPATH=$PWD/tests:$PWD uv run python -m unittest test_recette_status_report test_recette_service \
  test_storage_alerts test_notification_cadence
```

Les tests utilisent des racines `.test-tmp-recette-*` (ou un répertoire
temporaire avec `HERMES_RECETTE=true`), des environnements en dictionnaire et,
pour la session réelle, le double de marché inventé de `test_pilot_paper` avec
une horloge logique : ni réseau, ni modèle, ni secret.

## Hors périmètre

Dockerfile, configuration Railway, checkpoint et volume de production,
exposition publique de l'endpoint, interface du lot 2.
