# Pilote paper - huit comptes sur march� public

Le parcours principal utilise `pilot_paper` : `adaptive` et `control`, chacun avec
50/100/150/200 EUR. Les ordres et les fills sont simul�s. Les prix viennent du
march� public Kraken, sans authentification ; aucun prix, signal ou trade n'est
fabriqu� pour la recette. La fixture `paper_offline` de LAT-23 reste un test
synth�tique du moteur, pas une preuve publique des huit comptes.

## Installation s�par�e

Depuis la racine du d�p�t, sur `integration/lat-26` ou une branche qui inclut
`2e27adf20cfd9cdea5432e0080ea97b991cb1f2d`, avec uv et Python 3.11 :

```bash
uv sync --python 3.11 --locked
```

L'installation peut t�l�charger des d�pendances. Ne charger aucun `.env` ni
identifiant. Les commandes runtime ci-dessous utilisent exclusivement une
nouvelle racine `.test-tmp*/` ; changer le suffixe pour une seconde ex�cution.
`--init-only` refuse une racine existante et doit pr�c�der `--run` avec les m�mes
racine, identifiant et archive �ventuelle. Ne pas utiliser l'�tat extrait d'un
paquet comme racine d'ex�cution.

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

## Observation du march� actuel

```bash
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/public --init-only --experiment-id lat28-public
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/public --run --experiment-id lat28-public \
  --max-cycles 1 --max-runtime-seconds 120 --interval-seconds 60
```

Ce contr�le court v�rifie la collecte et les huit sorties ; il ne garantit
aucune entr�e/cl�ture. Pour une observation plus longue, LAT-34 a utilis�
4 cycles, 540 s maximum et un intervalle de 150 s (environ 9 min 10 s mesur�es).
Seuls les appels Kraken publics non authentifi�s sont autoris�s pendant cette
observation : aucun Railway, Kraken authentifi�, Discord ou fournisseur LLM.
Une erreur de collecte ou un arr�t sur borne doit �tre lu dans le rapport ;
un processus lanc� ou un heartbeat seul ne suffit pas.

## Rejeu de la preuve publique existante

Pour reproduire exactement LAT-34, t�l�charger son
[paquet de preuves](/api/attachments/2201371f-e2a4-4438-a1bd-9c96d11662e5/content)
via la pi�ce jointe du [ticket LAT-34](/LAT/issues/LAT-34).
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
Le t�l�chargement Paperclip est une �tape de pr�paration ; le rejeu suivant
n'appelle pas le march� ni un mod�le :

```bash
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/replay --init-only --experiment-id lat34-replay-proof \
  --replay-archive .test-tmp-lat28-pkg/.test-tmp-lat34/replay/replay_archive.json
uv run --python 3.11 python -m hermes_trading.pilot_paper \
  --root .test-tmp-lat28-repro/replay --run --experiment-id lat34-replay-proof \
  --replay-archive .test-tmp-lat28-pkg/.test-tmp-lat34/replay/replay_archive.json \
  --max-cycles 23 --max-runtime-seconds 604800 --interval-seconds 3600
```

Les 3600 s sont un pas d'horloge simul�e, pas une attente murale d'une heure.
L'archive ETH/EUR 1h contient 255 bougies publiques du 26 octobre au 6 novembre
2026 ; warmup par d�faut 50 bougies. Activation attendue :
`2026-10-29T01:00:00+00:00`. La session rejoue 23 cycles du 29 au 30 octobre.
Dur�e murale LAT-34 : 585 s ; reproduction ind�pendante LAT-51 : 613,5946 s.
Pr�voir plus de 10 minutes, sans timeout de 590 s. Les dates des JSON sont
simul�es et ne mesurent pas cette dur�e murale.

Pour une nouvelle archive publique (r�sultats diff�rents possibles) :

```bash
uv run --python 3.11 python -m hermes_trading.pilot_replay \
  --root .test-tmp-lat28-new-archive
```

Utiliser ensuite son `replay_archive.json` avec une autre racine neuve, au
bootstrap comme au run. Cette collecte exige uniquement Kraken public.

## Lire les huit sorties et le fallback

Sous chaque racine : `manifest.json` lie les comptes et strat�gies ;
`pilot_session.json` et la sortie JSON du run donnent provenance, bornes,
`completed_cycles`, `stop_reason`, `error`, `trade_counts`, `entries`, `exits`
et `public_proof`. Attendre la fin du processus et contr�ler son code de sortie.
Les sous-r�pertoires sont :

| adaptive | control |
|---|---|
| `adaptive_50` | `control_50` |
| `adaptive_100` | `control_100` |
| `adaptive_150` | `control_150` |
| `adaptive_200` | `control_200` |

Chaque compte contient `trading.db`, sa strat�gie et les projections de
d�cisions ; `orders.jsonl`, `fills.jsonl` et `trades.jsonl` apparaissent selon
l'activit�. Leur absence sans trade doit �tre distingu�e d'une preuve perdue. Le fallback local automatique
(`--reflect-every 1` par d�faut) est �crit par `deterministic_worker` sur le
primaire adaptive, puis publi� causalement aux autres adaptive. Consulter les
hypoth�ses et publications ; les control gardent leur strat�gie initiale.
Ne pas lancer `reflect --fallback` s�par�ment sur ces comptes poss�d�s par le
worker. Aucun appel LLM n'est n�cessaire (`model_access=not_used`).

La preuve LAT-34 et ses empreintes, dur�es et journaux complets sont d�crits dans
[LAT34_PUBLIC_PROOF_RESULTS.md](LAT34_PUBLIC_PROOF_RESULTS.md).
Sur son archive exacte, r�sultats attendus :

| comptes (adaptive et control) | entr�es / cl�tures par compte | PnL r�alis� EUR | �quit� finale EUR |
|---|---|---|---|
| 50 | 0 / 0 | 0,0000 | 50,0000 |
| 100 | 1 / 1 | -0,1253 | 99,8747 |
| 150 | 1 / 1 | -0,1253 | 149,8747 |
| 200 | 1 / 1 | -0,1253 | 199,8747 |

Soit six entr�es et six cl�tures au total, inventaire final vide. Les comptes
50 EUR ont rencontr� le minimum de la venue ; leurs d�cisions `no_trade`
sont une sortie explicable, pas une preuve d'activit� de trading.

## Limites et crit�res de lecture

- Observation actuelle et rejeu historique sont distincts. Le rejeu utilise des
  donn�es publiques archiv�es, r�horodat�es au cycle ; ce n'est pas une observation
  en temps r�el. Un nouvel appel public ne reproduit pas cette archive.
- LAT-34 a observ� z�ro trade sur le march� actuel : verdict inconclusif.
  Son rejeu porte `candidate_requires_review_public_replay`, soumis � revue,
  et sa fen�tre de 23 cycles a �t� choisie apr�s un essai de 5 cycles.
- Frais configur�s � z�ro dans cette preuve ; PnL non repr�sentatif des frais
  r�els. Les comptes 100/150/200 ont le m�me notionnel (~5 EUR), pas une
  exposition proportionnelle au capital. Adaptive/control sont identiques sur
  cette fen�tre : aucune sup�riorit� adaptive d�montr�e.
- Solde cash, PnL r�alis� et �quit� ne sont pas interchangeables. Avec une
  position ouverte, pr�ciser le prix et l'instant de valorisation ainsi que le
  PnL latent ; ne pas traiter le cash seul comme valeur du portefeuille. Ici
  l'inventaire final est vide. Une valeur absente reste inconnue, jamais z�ro
  ajout� pour compl�ter un tableau.
- Un rendement positif n'est pas requis. Z�ro trade n'autorise ni trade forc�
  ni cl�ture du ticket sur simple existence de fichiers. V�rifier provenance,
  d�cisions, fills, trades et coh�rence comptable des huit comptes.
- Ce parcours local ne prouve ni d�ploiement Railway, ni supervision distante,
  ni ordre r�el. L'acceptation exige la revue Stella puis la d�cision Thomas.

## V�rification LAT-28 (7 novembre 2026)

uv 0.13.6, Python 3.11.18, base `2e27adf`. Installation verrouill�e r�ussie.
Paquet et archive aux empreintes attendues, racines runtime neuves.
Observation courte ci-dessus : code 0, 113,093 s murales, un cycle complet,
huit comptes, z�ro entr�e/cl�ture, `error=null`, `not_eligible_zero_trade`.
Nouvelle collecte d'archive : code 0, 6,586 s, 255 bougies ETH/EUR ; elle
n'est pas substitu�e � l'archive exacte utilis�e pour la preuve historique.

Rejeu exact reproduit : code 0, **23 min 26,106 s murales** dans ce runtime
avec tests concurrents ; 23 cycles, six entr�es/six cl�tures, `error=null`,
collecteur ferm� et `candidate_requires_review_public_replay`. Le manifeste
`4efe77d9c0549979477d73e66ad12dd03fe1ef7c2a37122d9de3a0f640547992`
est identique. Les 184 d�cisions, 12 ordres, 12 fills et six trades sont
identiques octet par octet aux journaux LAT-34, compte par compte (fichiers
sans activit� absents ou vides). La dur�e historique de ~10 min n'est donc
pas une garantie de dur�e sur toute machine.
