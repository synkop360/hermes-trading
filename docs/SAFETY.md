# Securite trading

## Principe actuel

Le systeme est concu pour apprendre en simulation avant toute execution reelle.

Modes simules:

- `paper`
- `training`
- `train`
- `demo`

Ces modes produisent des decisions et des trades simules, mais ne placent pas d'ordre Kraken.

## Garde-fous live

Le mode `live` exige toujours deux confirmations:

```text
HERMES_TRADING_I_ACCEPT_RISK=true
HERMES_TRADING_LIVE_CONFIRM=I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS
```

Par defaut, le live reste en validation-only:

```text
HERMES_TRADING_LIVE_VALIDATE_ONLY=true
HERMES_TRADING_ALLOW_REAL_ORDERS=false
```

Dans ce mode, le worker appelle Kraken avec `validate=true` et ecrit le resultat dans `orders.jsonl`, mais ne soumet pas d'ordre reel.

Pour autoriser de vrais ordres, il faut une promotion manuelle explicite:

```text
HERMES_TRADING_LIVE_VALIDATE_ONLY=false
HERMES_TRADING_ALLOW_REAL_ORDERS=true
```

## Promotion vers live

Ordre obligatoire:

1. Garder `HERMES_TRADING_MODE=training`.
2. Deployer le code et verifier les tests.
3. Configurer une cle Kraken dediee avec `Orders and trades - Create & modify orders`, sans retraits.
4. Definir `HERMES_TRADING_MODE=live`.
5. Definir `HERMES_TRADING_I_ACCEPT_RISK=true`.
6. Definir `HERMES_TRADING_LIVE_CONFIRM=I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS`.
7. Definir `HERMES_TRADING_LIVE_VALIDATE_ONLY=true`.
8. Activer `HERMES_FUNDS_ALERT_ENABLED=true` avec un seuil adapte au budget d'ordre.
9. Lancer au moins un cycle validation-only et inspecter `orders.jsonl`.
10. Seulement apres approbation manuelle, definir `HERMES_TRADING_ALLOW_REAL_ORDERS=true` et `HERMES_TRADING_LIVE_VALIDATE_ONLY=false`.

## Regles avant vrais ordres

Avant d'activer `HERMES_TRADING_ALLOW_REAL_ORDERS=true`:

- limiter taille de position, perte journaliere et nombre d'ordres;
- ajouter un kill switch;
- utiliser une cle Kraken dediee sans retrait;
- commencer avec des montants minimaux;
- activer les alertes fonds bas Discord;
- journaliser chaque ordre envoye et chaque reponse exchange.

## Rollback immediat

```powershell
railway.cmd variables --set "HERMES_TRADING_MODE=training"
railway.cmd variables --set "HERMES_TRADING_I_ACCEPT_RISK=false"
railway.cmd variables --set "HERMES_TRADING_LIVE_CONFIRM=disabled"
railway.cmd variables --set "HERMES_TRADING_LIVE_VALIDATE_ONLY=true"
railway.cmd variables --set "HERMES_TRADING_ALLOW_REAL_ORDERS=false"
railway.cmd up
```

## Secrets

Ne jamais committer:

- `.env`;
- cle API;
- secret API;
- token Railway;
- cle SSH.

Les secrets doivent rester dans:

- Railway variables pour la production;
- `.env` local ignore par Git pour les tests locaux.
