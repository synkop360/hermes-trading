# Kraken

## Ce que le systeme utilise

Le worker utilise Kraken via `ccxt` pour recuperer:

- le dernier prix;
- le bid et ask;
- les bougies `1h`;
- les prix de cloture necessaires au RSI.

Par defaut:

```text
EXCHANGE_ID=kraken
```

## Lier un compte Kraken

Dans Kraken Pro:

1. Ouvrir les parametres API.
2. Creer une nouvelle cle API.
3. Donner uniquement les permissions necessaires a la lecture marche et compte pour le mode actuel.
4. Ne pas activer les retraits.
5. Pour le live validation-only ou reel, creer une cle separee avec permission de trading minimale.
6. Copier la cle et le secret dans Railway:

```text
EXCHANGE_API_KEY
EXCHANGE_API_SECRET
```

## Permissions conseillees

Pour le mode `training` actuel:

- lecture marche publique: pas de secret requis;
- lecture compte privee: optionnelle;
- trading: non necessaire;
- retraits: jamais necessaires.

Pour le mode live:

- utiliser une cle dediee;
- autoriser uniquement `Orders and trades - Create & modify orders` si des ordres doivent etre valides ou soumis;
- ne jamais autoriser les retraits;
- ajouter une restriction IP si disponible et compatible avec l'hebergement.

## Validation-only Kraken

Le premier niveau live utilise Kraken Spot via `ccxt` avec le parametre:

```text
validate=true
```

Ce mode demande a Kraken de valider l'ordre sans le soumettre au carnet. Chaque tentative est journalisee dans:

```text
state/orders.jsonl
```

Le budget par ordre est volontairement petit par defaut:

```text
HERMES_TRADING_MAX_ORDER_USDT=5
```

Avant un vrai ordre, verifier que:

- la paire existe chez Kraken;
- le montant respecte les minimums `amount` et `cost`;
- `orders.jsonl` contient `status: "validated"`;
- la cle API n'a pas de permission de retrait.

## Mode training Kraken

Le mode `training` du projet est interne a Hermes Trading: il simule les decisions et n'envoie pas d'ordre.

Cote Kraken, la documentation officielle indique:

- Spot UAT: disponible sur demande pour certains clients via onboarding ou Account Manager.
- Futures sandbox: environnement demo separe, self-service.

Le worker actuel utilise les paires Spot Kraken via `ccxt`; il ne bascule donc pas automatiquement vers le sandbox Futures.

Sources officielles:

- https://docs.kraken.com/exchange/guides/overview
- https://docs.kraken.com/api-reference/trading/add-order
- https://docs.kraken.com/exchange/guides/rest/authentication
- https://docs.kraken.com/home/guides/quickstart
- https://support.kraken.com/articles/advanced-api-faq
- https://support.kraken.com/articles/account-management-faq

## Test de connectivite

Test public sans secret:

```powershell
uv run python -c "import asyncio; from hermes_trading.adapters.price import fetch; print(asyncio.run(fetch('BTC/USDT'))['source'])"
```

Test multi-actifs:

```powershell
uv run python -c "import asyncio; from hermes_trading.adapters.price import fetch; pairs=['BTC/USDT','ETH/USDT','XRP/USDT','SHIB/USDT']; [print(p, asyncio.run(fetch(p))['last']) for p in pairs]"
```

Si une paire echoue, verifier d'abord qu'elle existe bien chez Kraken sous ce symbole `ccxt`.
