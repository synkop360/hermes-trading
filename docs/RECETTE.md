# Recette — service `scripts.recette_service`

Point d'entrée de l'environnement Railway `recette` (service `hermes-recette`),
livré par LAT-117 (lot 1, ticket 2). Il initialise le pilote paper huit comptes
(`pilot_paper`, voir `docs/PAPER_PILOT.md`) sur un volume vide, puis le sert en
boucle par sessions bornées. Paper uniquement : aucun secret, aucun modèle,
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
reprise, pour qu'un redéploiement d'`integration` reprenne le même volume.

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
versions), `recette_pass` (raison d'arrêt, cycles, échantillons, entrées,
sorties), `recette_service_stop`.

## Arrêt et codes de sortie

- Fichier `/app/state/.test-tmp-pilot/STOP` : arrêt propre avant la passe suivante ou
  pendant une session, code 0. Le retirer pour reprendre.
- Refus (posture, variable interdite, volume inconnu, bornes invalides) ou
  session terminée hors de ses bornes (`collection_unavailable`,
  `engine_halted`, `error`…) : message `refused: …` sur la sortie d'erreur,
  code 2. Au redémarrage, le volume identifié est repris.
- Interruption : code 130.

## Tests

```sh
PYTHONPATH=$PWD/tests uv run python -m unittest test_recette_service
```

Les tests utilisent des racines `.test-tmp-recette-*` (ou un répertoire
temporaire avec `HERMES_RECETTE=true`), des environnements en dictionnaire et,
pour la session réelle, le double de marché inventé de `test_pilot_paper` avec
une horloge logique : ni réseau, ni modèle, ni secret.

## Hors périmètre

Dockerfile, configuration Railway, checkpoint et volume de production. Le
rapport d'état de la recette relève du ticket 3 du lot.
