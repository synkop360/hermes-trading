# Pilote paper — huit comptes sur marché public

Le parcours principal utilise `pilot_paper` : `adaptive` et `control`, chacun avec
50/100/150/200 EUR. Les ordres et les fills sont simulés. Les prix viennent du
marché public Kraken, sans authentification ; aucun prix, signal ou trade n'est
fabriqué pour la recette. La fixture `paper_offline` de LAT-23 reste un test
synthétique du moteur, pas une preuve publique des huit comptes.

## Installation séparée

Depuis la racine du dépôt, sur `integration/lat-26` ou une branche qui inclut
`2e27adf20cfd9cdea5432e0080ea97b991cb1f2d`, avec uv et Python 3.11 :

```bash
uv sync --python 3.11 --locked
```

L'installation peut télécharger des dépendances. Ne charger aucun `.env` ni
identifiant. Les commandes runtime ci-dessous utilisent exclusivement une
nouvelle racine `.test-tmp*/` ; changer le suffixe pour une seconde exécution.
`--init-only` refuse une racine existante et doit précéder `--run` avec les mêmes
racine, identifiant et archive éventuelle. Ne pas utiliser l'état extrait d'un
paquet comme racine d'exécution.

## Parcours Windows PowerShell

Depuis la racine du depot, Windows PowerShell 5.1 ou PowerShell 7 :

```powershell
# Premiere execution : installation verrouillee, bootstrap puis observation.
.\scripts\run_paper_pilot.ps1 -Install

# Executions suivantes : nouvelle racine et nouvelle identite automatiques.
.\scripts\run_paper_pilot.ps1
```

Le lanceur utilise Python 3.11 et `uv run --no-sync` : sans `-Install`, il ne
synchronise pas les dependances. Par defaut, il lance un cycle, borne a 120 s,
avec un intervalle de 60 s. Il refuse une racine existante, initialise avant
le run et s'arrete des qu'une commande echoue. Une erreur de run conserve
les traces de la session pour inspection ; une nouvelle execution choisit une
nouvelle racine. Les chemins relatifs de `-Root` sont relatifs au depot.
Ne charger aucun `.env` ni identifiant pour ce parcours public.

Pour un rejeu, preparer le paquet public exact de la section suivante, sans
utiliser son etat extrait comme racine runtime. Avec le ZIP telecharge dans
`.test-tmp-lat28-pkg/proof.zip`, verifier puis extraire sous PowerShell :

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $zip = '.test-tmp-lat28-pkg/proof.zip'
    $expectedZip = 'e505d70bbb06645ef0a071e399a495a289a339f970b209d86c0861993def4381'
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $expectedZip) {
        throw 'Empreinte du paquet public incorrecte'
    }
    Expand-Archive -LiteralPath $zip -DestinationPath '.test-tmp-lat28-pkg/extracted'
    $archive = '.test-tmp-lat28-pkg/extracted/.test-tmp-lat34/replay/replay_archive.json'
    $expectedArchive = 'a75ccec9ae6871499c635f0770282062b885179e55d9f209a476fe7ddaa506ea'
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedArchive) {
        throw 'Empreinte de l''archive publique incorrecte'
    }
    .\scripts\run_paper_pilot.ps1 -ReplayArchive $archive `
        -ExperimentId 'lat34-replay-proof' -MaxCycles 23 `
        -MaxRuntimeSeconds 604800 -IntervalSeconds 3600
}
```

L'intervalle du rejeu avance l'horloge simulee ; ce n'est pas une attente d'une
heure. Les durees et limites historiques ci-dessous restent applicables.
Le lanceur conserve les strategies du pilote, y compris ses frais nuls de
reference ; ce parcours ne remplace pas la beta avec frais declares.
Verifier `error`, `stop_reason`, `trade_counts`, `entries`, `exits` et
`public_proof` dans la sortie JSON et `pilot_session.json`. Un code de sortie
nul ou zero trade ne suffit pas a etablir une preuve financiere.

## Observation du marché actuel

```bash
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/public --init-only --experiment-id lat28-public
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/public --run --experiment-id lat28-public \
  --max-cycles 1 --max-runtime-seconds 120 --interval-seconds 60
```

Ce contrôle court vérifie la collecte et les huit sorties ; il ne garantit
aucune entrée/clôture. Pour une observation plus longue, LAT-34 a utilisé
4 cycles, 540 s maximum et un intervalle de 150 s (environ 9 min 10 s mesurées).
Seuls les appels Kraken publics non authentifiés sont autorisés pendant cette
observation : aucun Railway, Kraken authentifié, Discord ou fournisseur LLM.
Une erreur de collecte ou un arrêt sur borne doit être lu dans le rapport ;
un processus lancé ou un heartbeat seul ne suffit pas.

## Rejeu de la preuve publique existante

Pour reproduire exactement LAT-34, télécharger son
[paquet de preuves](/api/attachments/2201371f-e2a4-4438-a1bd-9c96d11662e5/content)
via la pièce jointe du [ticket LAT-34](/LAT/issues/LAT-34).
Dans un runtime Paperclip, ces commandes normalisent l'URL API sans afficher le jeton :

```bash
mkdir -p .test-tmp-lat28-pkg
PAPERCLIP_API_BASE="${PAPERCLIP_API_URL%/}"
PAPERCLIP_API_BASE="${PAPERCLIP_API_BASE%/api}"
curl -fsS -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -o .test-tmp-lat28-pkg/proof.zip \
  "$PAPERCLIP_API_BASE/api/attachments/2201371f-e2a4-4438-a1bd-9c96d11662e5/content"
sha256sum .test-tmp-lat28-pkg/proof.zip
uv run --python 3.11 python -I -m zipfile -e \
  .test-tmp-lat28-pkg/proof.zip .test-tmp-lat28-pkg
sha256sum .test-tmp-lat28-pkg/.test-tmp-lat34/replay/replay_archive.json
```

Empreintes attendues : ZIP
`e505d70bbb06645ef0a071e399a495a289a339f970b209d86c0861993def4381` ; archive
`a75ccec9ae6871499c635f0770282062b885179e55d9f209a476fe7ddaa506ea`.
Le téléchargement Paperclip est une étape de préparation ; le rejeu suivant
n'appelle pas le marché ni un modèle :

```bash
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/replay --init-only --experiment-id lat34-replay-proof \
  --replay-archive .test-tmp-lat28-pkg/.test-tmp-lat34/replay/replay_archive.json
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/replay --run --experiment-id lat34-replay-proof \
  --replay-archive .test-tmp-lat28-pkg/.test-tmp-lat34/replay/replay_archive.json \
  --max-cycles 23 --max-runtime-seconds 604800 --interval-seconds 3600
```

Les 3600 s sont un pas d'horloge simulée, pas une attente murale d'une heure.
L'archive ETH/EUR 1h contient 255 bougies publiques du 26 octobre au 6 novembre
2026 ; warmup par défaut 50 bougies. Activation attendue :
`2026-10-29T01:00:00+00:00`. La session rejoue 23 cycles du 29 au 30 octobre.
Durée murale LAT-34 : 585 s ; reproduction indépendante LAT-51 : 613,5946 s.
Prévoir plus de 10 minutes, sans timeout de 590 s. Les dates des JSON sont
simulées et ne mesurent pas cette durée murale.

Pour une nouvelle archive publique (résultats différents possibles) :

```bash
uv run --python 3.11 python -m hermes_trading.pilot_replay \
  --root .test-tmp-lat28-new-archive
```

Utiliser ensuite son `replay_archive.json` avec une autre racine neuve, au
bootstrap comme au run. Cette collecte exige uniquement Kraken public.

## Lire les huit sorties et le fallback

Sous chaque racine : `manifest.json` lie les comptes et stratégies ;
`pilot_session.json` et la sortie JSON du run donnent provenance, bornes,
`completed_cycles`, `stop_reason`, `error`, `trade_counts`, `entries`, `exits`
et `public_proof`. Attendre la fin du processus et contrôler son code de sortie.
Les sous-répertoires sont :

| adaptive | control |
|---|---|
| `adaptive_50` | `control_50` |
| `adaptive_100` | `control_100` |
| `adaptive_150` | `control_150` |
| `adaptive_200` | `control_200` |

Chaque compte contient `trading.db`, sa stratégie et les projections de
décisions ; `orders.jsonl`, `fills.jsonl` et `trades.jsonl` apparaissent selon
l’activité. Leur absence sans trade doit être distinguée d’une preuve perdue. Le fallback local automatique
(`--reflect-every 1` par défaut) est écrit par `deterministic_worker` sur le
primaire adaptive, puis publié causalement aux autres adaptive. Consulter les
hypothèses et publications ; les control gardent leur stratégie initiale.
Ne pas lancer `reflect --fallback` séparément sur ces comptes possédés par le
worker. Aucun appel LLM n'est nécessaire (`model_access=not_used`).

La preuve LAT-34 et ses empreintes, durées et journaux complets sont décrits dans
[LAT34_PUBLIC_PROOF_RESULTS.md](LAT34_PUBLIC_PROOF_RESULTS.md).
Sur son archive exacte, résultats attendus :

| comptes (adaptive et control) | entrées / clôtures par compte | PnL réalisé EUR | équité finale EUR |
|---|---|---|---|
| 50 | 0 / 0 | 0,0000 | 50,0000 |
| 100 | 1 / 1 | -0,1253 | 99,8747 |
| 150 | 1 / 1 | -0,1253 | 149,8747 |
| 200 | 1 / 1 | -0,1253 | 199,8747 |

Soit six entrées et six clôtures au total, inventaire final vide. Les comptes
50 EUR ont rencontré le minimum de la venue ; leurs décisions `no_trade`
sont une sortie explicable, pas une preuve d'activité de trading.

## Limites et critères de lecture

- Observation actuelle et rejeu historique sont distincts. Le rejeu utilise des
  données publiques archivées, réhorodatées au cycle ; ce n'est pas une observation
  en temps réel. Un nouvel appel public ne reproduit pas cette archive.
- LAT-34 a observé zéro trade sur le marché actuel : verdict inconclusif.
  Son rejeu porte `candidate_requires_review_public_replay`, soumis à revue,
  et sa fenêtre de 23 cycles a été choisie après un essai de 5 cycles.
- Frais configurés à zéro dans cette preuve ; PnL non représentatif des frais
  réels. Les comptes 100/150/200 ont le même notionnel (~5 EUR), pas une
  exposition proportionnelle au capital. Adaptive/control sont identiques sur
  cette fenêtre : aucune supériorité adaptive démontrée.
- Solde cash, PnL réalisé et équité ne sont pas interchangeables. Avec une
  position ouverte, préciser le prix et l'instant de valorisation ainsi que le
  PnL latent ; ne pas traiter le cash seul comme valeur du portefeuille. Ici
  l'inventaire final est vide. Une valeur absente reste inconnue, jamais zéro
  ajouté pour compléter un tableau.
- Un rendement positif n'est pas requis. Zéro trade n'autorise ni trade forcé
  ni clôture du ticket sur simple existence de fichiers. Vérifier provenance,
  décisions, fills, trades et cohérence comptable des huit comptes.
- Ce parcours local ne prouve ni déploiement Railway, ni supervision distante,
  ni ordre réel. L'acceptation exige la revue Stella puis la décision Thomas.

## Vérification LAT-28 (7 novembre 2026)

uv 0.13.6, Python 3.11.18, base `2e27adf`. Installation verrouillée réussie.
Paquet et archive aux empreintes attendues, racines runtime neuves.
Observation courte ci-dessus : code 0, 113,093 s murales, un cycle complet,
huit comptes, zéro entrée/clôture, `error=null`, `not_eligible_zero_trade`.
Nouvelle collecte d'archive : code 0, 6,586 s, 255 bougies ETH/EUR ; elle
n'est pas substituée à l'archive exacte utilisée pour la preuve historique.

Rejeu exact reproduit : code 0, **23 min 26,106 s murales** dans ce runtime
avec tests concurrents ; 23 cycles, six entrées/six clôtures, `error=null`,
collecteur fermé et `candidate_requires_review_public_replay`. Le manifeste
`4efe77d9c0549979477d73e66ad12dd03fe1ef7c2a37122d9de3a0f640547992`
est identique. Les 184 décisions, 12 ordres, 12 fills et six trades sont
identiques octet par octet aux journaux LAT-34, compte par compte (fichiers
sans activité absents ou vides). La durée historique de ~10 min n'est donc
pas une garantie de durée sur toute machine.
