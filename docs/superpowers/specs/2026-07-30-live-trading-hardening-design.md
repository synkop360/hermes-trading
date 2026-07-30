# Hermes Trading Live Hardening Design

## 1. Objectif

Faire evoluer Hermes Trading vers un systeme Spot Kraken exploitable en reel,
sans levier, sans ralentir le cycle de decision et sans rendre le diagnostic
dependant d'un logiciel specialise.

Le systeme doit:

- executer un cycle complet entree, position ouverte, sortie et PnL realise;
- proteger chaque position par un stop heberge chez Kraken;
- empecher les ordres dupliques et reprendre correctement apres redemarrage;
- appliquer des limites de risque independantes de la strategie et de l'IA;
- evaluer automatiquement les conditions de passage entre les phases;
- demander une confirmation Discord avant toute augmentation d'exposition;
- utiliser en priorite des modeles gratuits decouverts par Hermes;
- utiliser OpenAI uniquement comme fallback ponctuel;
- calculer la rentabilite nette apres frais de marche, Railway et IA;
- conserver des fichiers JSON, JSONL et Markdown lisibles sans SQLite.

## 2. Principes non negociables

1. SQLite WAL est la source de verite des ordres, positions, controles de
   risque, qualifications et approbations.
2. Chaque transition importante est egalement inscrite dans un journal JSONL
   append-only.
3. Des instantanes JSON atomiques permettent de comprendre le dernier etat
   connu avec un simple editeur de texte.
4. Une panne d'IA ne bloque jamais le trading avec la derniere strategie
   validee.
5. Une panne ou une ambiguite Kraken bloque toute nouvelle entree.
6. Hermes ne peut modifier ni les limites de risque, ni le kill switch, ni les
   criteres de promotion.
7. Une strategie generee par IA ne devient jamais automatiquement la strategie
   reelle.
8. Aucune action Discord ne contourne les controles internes du worker.
9. Un cout inconnu n'est jamais suppose nul.
10. Les traitements lourds sont executes hors du chemin critique.

## 3. Perimetre du premier live

Le premier live est limite a Kraken Spot:

- positions longues uniquement;
- aucun levier;
- aucun short ou produit Futures;
- une position maximum par symbole;
- aucun pyramiding;
- entree au marche apres controles de fraicheur, spread et slippage;
- stop-loss place chez Kraken apres confirmation de l'entree;
- sortie par stop, signal de strategie ou kill switch;
- reconciliation par API REST;
- polling accelere uniquement pendant un ordre transitoire.

Les symboles sont promus dans cet ordre:

1. `BTC/USDT`;
2. `ETH/USDT`;
3. `XRP/USDT`;
4. `SHIB/USDT`.

## 4. Architecture

### 4.1 Worker trading

Le worker reste l'unique composant autorise a:

- lire les secrets Kraken;
- ecrire dans SQLite;
- prendre une decision financiere;
- accepter ou refuser une commande de controle;
- executer un ordre;
- modifier l'etat d'une position.

Il est compose des unites suivantes.

#### MarketDataCoordinator

- recupere une seule fois les donnees partagees macro, news et on-chain par
  cycle;
- recupere les donnees propres a chaque symbole avec une concurrence limitee;
- rejette les donnees trop anciennes ou incompletes;
- identifie chaque bougie par symbole, timeframe et timestamp de cloture;
- ne produit qu'une decision par bougie close;
- conserve un cache court pour reduire les appels externes.

#### StrategyEngine

- charge la strategie champion validee;
- charge eventuellement une strategie challenger;
- applique les surcharges par symbole;
- produit les signaux d'entree et de sortie;
- execute le challenger en simulation sur les memes donnees;
- ne transmet jamais les signaux challenger a l'execution reelle.

#### PositionEngine

- gere les etats `opening`, `open`, `closing`, `closed` et `error`;
- calcule quantite, prix moyen, frais, stop, PnL realise et PnL latent;
- interdit une nouvelle entree lorsqu'une position existe deja;
- gere les remplissages partiels;
- lie chaque position a sa strategie, ses ordres et sa bougie d'origine.

#### RiskEngine

- calcule la taille depuis le risque monetaire au stop;
- applique les plafonds par ordre, symbole, portefeuille et fonds disponibles;
- applique les limites de perte journaliere et de drawdown;
- bloque les ordres lorsque les couts, soldes ou etats sont inconnus;
- persiste le kill switch;
- fournit une raison humaine pour chaque autorisation ou refus.

#### KrakenExecutor

- persiste l'intention avant tout appel prive;
- genere un `cl_ord_id` unique et deterministe;
- soumet l'ordre avec les contraintes de precision Kraken;
- confirme les volumes executes et les frais;
- place le stop protecteur apres l'entree;
- reconcilie les ordres locaux et distants;
- ne renvoie jamais aveuglement une requete ambigue.

#### StateRepository

- initialise et migre SQLite;
- utilise des transactions courtes en mode WAL;
- expose des operations metier, pas des requetes SQL aux autres composants;
- ecrit les evenements JSONL apres validation de la transaction;
- regenere les instantanes JSON par remplacement atomique;
- detecte et signale toute divergence entre SQLite et les fichiers d'audit.

#### QualificationEngine

- maintient la phase active et les criteres du prochain passage;
- lance les campagnes automatiques hors du cycle de trading;
- signe chaque verdict avec la version du code, de la strategie et des donnees;
- refuse toute promotion sur rapport incomplet ou perime;
- attend une approbation Discord lorsque tous les controles passent.

#### CostLedger

- enregistre frais Kraken, slippage, couts Railway et couts IA;
- amortit les couts fixes sur la periode analysee;
- calcule le PnL net, le seuil de rentabilite et le scenario stresse;
- distingue le cout du worker et celui du bot;
- produit une estimation conservatrice lorsqu'une valeur reelle manque.

#### ProviderRouter

- lit le catalogue dynamique et le cache de modeles Hermes;
- conserve uniquement les modeles gratuits disponibles avec les
  authentifications actives;
- prefere le fournisseur Hermes/Nous;
- applique un cooldown par couple fournisseur-modele;
- laisse Hermes gerer ses fallbacks natifs;
- appelle OpenAI uniquement apres echec des candidats gratuits;
- recommence chaque nouvelle reflexion par Hermes;
- n'applique aucun changement si tous les fournisseurs echouent.

#### ControlAPI

- ecoute uniquement sur le reseau prive Railway;
- n'expose qu'une liste fermee de commandes;
- authentifie, horodate et deduplique chaque requete;
- transmet toute commande au meme moteur de controle que les operations
  locales;
- ne retourne jamais de secret.

### 4.2 Bot Discord

Le bot est deploye comme second service Railway minimal.

Il:

- ne possede aucune cle Kraken;
- utilise uniquement le token Discord et un secret de communication privee;
- interroge le worker par le reseau prive Railway;
- reste disponible pour signaler une panne du worker;
- applique une liste blanche de serveur, canal et utilisateur;
- utilise des interactions ephemeres pour les donnees sensibles;
- impose un TOTP aux actions qui augmentent le risque;
- journalise toutes les commandes sans journaliser le TOTP.

Les commandes sont:

- lecture: `/status`, `/positions`, `/orders`, `/risk`, `/strategy`,
  `/providers`, `/costs`, `/profitability`, `/qualification`;
- protection: `/pause`, `/kill`, `/reject`;
- risque accru: `/resume`, `/approve`, `/promote`, `/rollback-live`.

Les commandes de protection utilisent une confirmation courte. Les commandes
qui activent ou augmentent le risque utilisent:

1. une demande Discord ephemere;
2. un bouton de confirmation;
3. un code TOTP;
4. une expiration apres cinq minutes;
5. une cle d'idempotence a usage unique.

## 5. Modele de donnees

### 5.1 Tables SQLite

Les tables canoniques sont:

- `schema_migrations`;
- `candles_processed`;
- `decisions`;
- `order_intents`;
- `orders`;
- `order_fills`;
- `positions`;
- `risk_state`;
- `strategy_versions`;
- `provider_attempts`;
- `cost_events`;
- `qualification_runs`;
- `qualification_checks`;
- `approval_requests`;
- `control_commands`;
- `incidents`.

Les montants monetaires et quantites sont stockes en representation decimale,
jamais en flottants binaires dans les calculs financiers.

Chaque table metier utilise:

- un identifiant stable;
- des timestamps UTC;
- une version ou un compteur de revision;
- les liens vers les evenements sources;
- un statut explicite;
- une raison humaine lorsque le statut represente un refus ou une erreur.

### 5.2 Fichiers lisibles

Les journaux append-only sont:

- `decisions.jsonl`;
- `orders.jsonl`;
- `fills.jsonl`;
- `positions.jsonl`;
- `risk_events.jsonl`;
- `hypotheses.jsonl`;
- `provider_attempts.jsonl`;
- `cost_events.jsonl`;
- `qualification_history.jsonl`;
- `control_audit.jsonl`;
- `incidents.jsonl`.

Les instantanes sont:

- `heartbeat.json`;
- `system_status.json`;
- `positions.json`;
- `open_orders.json`;
- `risk_status.json`;
- `provider_status.json`;
- `costs.json`;
- `profitability.json`;
- `qualification_status.json`;
- `qualification_report.json`.

Les rapports humains sont:

- `qualification_report.md`;
- `daily_operations_report.md`;
- `profitability_report.md`.

Tous les fichiers d'etat sont ecrits dans un fichier temporaire du meme volume,
flushes, puis remplaces atomiquement.

## 6. Flux d'une decision et d'un ordre

### 6.1 Demarrage

1. Valider toutes les variables de configuration.
2. Verifier le schema et l'integrite SQLite.
3. Charger le kill switch et la derniere phase.
4. Interroger Kraken pour les soldes, ordres ouverts et ordres recents.
5. Reconcilier les ordres et positions.
6. Verifier la presence des stops pour toute position ouverte.
7. Regenerer les instantanes humains.
8. Autoriser les nouvelles entrees seulement si tous les controles obligatoires
   sont verts.

### 6.2 Cycle normal

1. Collecter les donnees partagees une seule fois.
2. Collecter les donnees des symboles avec une limite de concurrence.
3. Ignorer toute bougie non close ou deja traitee.
4. Evaluer d'abord les positions ouvertes et leurs sorties.
5. Evaluer ensuite les nouvelles entrees sur les symboles libres.
6. Calculer la taille par le RiskEngine.
7. Persister la decision et l'intention d'ordre.
8. Soumettre l'ordre avec son `cl_ord_id`.
9. Reconcilier jusqu'a un etat terminal ou un timeout controle.
10. Placer le stop sur la quantite reellement executee.
11. Mettre a jour la position et les couts.
12. Produire les notifications et instantanes.
13. Evaluer le challenger sans ordre reel.

### 6.3 Calcul de taille

Les parametres initiaux du micro-live sont:

- risque maximum par trade: `0.25 %` du capital;
- exposition maximum par symbole: `5 %`;
- exposition totale maximum: `10 %`;
- perte journaliere maximum: `0.5 %`;
- drawdown global maximum: `3 %`;
- une position par symbole;
- plafond absolu `HERMES_TRADING_MAX_ORDER_USDT`;
- montant final au moins egal au minimum Kraken, sinon aucun ordre.

Le calcul est:

```text
risk_budget = available_equity * risk_per_trade_pct
risk_sized_notional = risk_budget / stop_loss_pct

final_notional = min(
  risk_sized_notional,
  max_order_usdt,
  remaining_symbol_exposure,
  remaining_total_exposure,
  free_quote_balance
)
```

`position_size_r` devient un multiplicateur borne applique au budget de risque.
Il ne peut jamais augmenter les plafonds absolus.

## 7. Reconciliation Kraken

La reconciliation REST s'execute:

- au demarrage;
- au debut de chaque cycle;
- apres chaque soumission;
- plus frequemment lorsqu'un ordre est ouvert ou partiel;
- avant toute reprise suivant une erreur ambigue.

Pour chaque ordre, elle compare:

- `cl_ord_id`;
- identifiant Kraken;
- symbole et cote;
- quantite demandee, executee et restante;
- prix moyen;
- frais;
- statut local et distant;
- position attendue et solde reel.

En cas d'incoherence:

- aucune nouvelle entree n'est autorisee;
- l'incident est persiste;
- une alerte Discord est envoyee;
- le worker tente uniquement des operations de reconciliation ou de reduction
  du risque;
- une intervention humaine est demandee si l'etat ne peut etre determine.

Si le stop ne peut pas etre place apres une entree executee, le worker:

1. tente une cloture immediate au marche;
2. active le kill switch;
3. enregistre un incident critique;
4. interdit toute reprise automatique;
5. demande une confirmation Discord avec TOTP avant reprise.

## 8. Strategies champion et challenger

`strategy.yaml` reste la strategie champion.
`candidate_strategy.yaml` contient le challenger.

En training, Hermes peut continuer a appliquer automatiquement ses changements
selon les regles de reflection.

En validation-only et live:

- Hermes ecrit uniquement une nouvelle version challenger;
- le challenger est evalue en simulation;
- les frais, spread et slippage sont appliques;
- les resultats sont separes du champion;
- la promotion est bloquee tant que la qualification n'est pas complete;
- la promotion exige Discord et TOTP;
- la strategie precedente est archivee et reste restaurable.

Le rollback automatique est autorise uniquement pour diminuer le risque. Une
nouvelle strategie plus agressive ne peut jamais etre activee automatiquement.

## 9. Routage Hermes et OpenAI

### 9.1 Ordre de selection

Pour chaque reflexion:

1. rafraichir le catalogue Hermes si le cache a expire;
2. lire les fournisseurs actuellement authentifies;
3. filtrer les modeles gratuits compatibles;
4. exclure les modeles en cooldown;
5. classer par fournisseur prefere, disponibilite recente et latence;
6. essayer les candidats gratuits via Hermes;
7. utiliser les fallbacks natifs Hermes;
8. utiliser OpenAI seulement si tous les candidats gratuits ont echoue;
9. conserver la strategie si OpenAI echoue aussi.

Un `429` respecte `Retry-After` lorsqu'il est fourni. Le cooldown concerne
uniquement le couple en echec et ne bloque pas les autres fournisseurs.

Le cycle suivant repart toujours de l'etape 1. Le succes OpenAI n'est jamais
memorise comme nouvelle preference.

### 9.2 Audit et cout

Chaque tentative enregistre:

- fournisseur et modele;
- caractere gratuit ou payant;
- heure de debut et duree;
- succes, classe d'erreur et cooldown;
- tokens entrants et sortants;
- cout estime;
- raison du fallback.

Hermes est appele avec son rapport d'usage lorsqu'il est disponible. Les appels
OpenAI utilisent les compteurs de tokens retournes par l'API et une table de
prix versionnee et datee.

## 10. Couts et rentabilite

### 10.1 Sources de cout

Le CostLedger utilise, dans l'ordre:

1. consommation reelle Railway via API avec un token limite au projet;
2. cout mensuel Railway configure et amorti a la minute;
3. estimation conservatrice CPU, RAM, volume et egress.

La part d'abonnement imputee au projet est configurable et vaut `100 %` par
defaut.

Les autres couts sont:

- frais de chaque execution Kraken;
- slippage entre prix de decision et prix moyen;
- cout de chaque appel IA payant;
- cout du worker;
- cout additionnel du bot.

### 10.2 Indicateurs

Les rapports affichent:

- PnL brut;
- frais Kraken;
- slippage;
- PnL apres frais de trading;
- cout Railway worker;
- cout Railway bot;
- cout Hermes;
- cout OpenAI;
- PnL net;
- rendement net;
- capital de seuil de rentabilite;
- ratio de couverture des couts;
- scenario stresse.

Le scenario stresse reduit le PnL de `20 %` et augmente les couts de `25 %`.

### 10.3 Verrou economique

Le micro-live n'est pas bloque par sa rentabilite absolue, car son capital est
volontairement minimal.

Le live etendu exige:

- PnL net projete a 30 jours strictement positif;
- couverture des couts Railway et IA d'au moins `1.5`;
- scenario stresse positif;
- couts obligatoires disponibles;
- drawdown et regularite conformes.

Une derogation humaine peut autoriser un test limite mais ne peut modifier les
plafonds de risque.

## 11. Qualification et promotions

La machine d'etats est:

```text
training
-> validation_ready
-> kraken_validate_only
-> micro_live_btc
-> live_btc
-> live_eth
-> live_xrp
-> live_shib
```

### 11.1 Training vers validation-ready

Exigences:

- tous les tests automatiques passent;
- au moins 14 jours de donnees apres correction du simulateur;
- au moins 50 positions independantes cloturees;
- aucune duplication de bougie;
- frais et slippage inclus;
- limites de risque exercees;
- replay sans fuite de donnees futures.

### 11.2 Validation-only vers micro-live BTC

Exigences:

- au moins 48 heures sans erreur de precision;
- aucune intention dupliquee;
- aucun ordre validate-only incoherent;
- reconciliation des soldes et permissions;
- tests de redemarrage et reponse ambigue passes;
- stop Kraken valide;
- approbation Discord avec TOTP.

### 11.3 Micro-live BTC vers live BTC

Exigences:

- au moins 7 jours;
- au moins 10 positions completes;
- tous les stops presents;
- aucune divergence non resolue;
- limites de risque respectees;
- rapports de cout complets;
- approbation Discord avec TOTP.

### 11.4 Ajout d'un symbole

Chaque symbole est ajoute seul. Les exigences du micro-live BTC sont appliquees
au nouveau symbole. SHIB ajoute des limites de spread et slippage plus strictes.

Le passage au live etendu applique aussi le verrou economique.

### 11.5 Campagnes automatiques

Les campagnes couvrent:

- tests unitaires;
- tests d'integration avec faux Kraken;
- replay historique;
- remplissages partiels;
- rejets et timeouts;
- redemarrage a chaque frontiere d'ordre;
- idempotence `cl_ord_id`;
- reconciliation de soldes et positions;
- donnees de marche perimees;
- panne reseau;
- echec du stop;
- corruption controlee SQLite et JSON;
- limites de risque et kill switch;
- panne Hermes et rate limit;
- rotation de modeles gratuits;
- fallback OpenAI et retour Hermes;
- authentification Discord, TOTP, expiration et rejeu;
- calcul des couts et scenario stresse;
- stabilite sur la duree.

Chaque execution est liee a:

- version du code;
- version de strategie;
- version du schema SQLite;
- plage de donnees;
- configuration non secrete;
- hash des resultats.

Un resultat devient perime si l'un de ces elements change.

## 12. Discord et approbations

Le bot publie:

- etat du worker;
- positions et ordres ouverts;
- limites de risque et kill switch;
- derniere strategie champion et challenger;
- fournisseurs IA et cooldowns;
- couts et rentabilite;
- progression de qualification;
- incidents et recommandations.

Une demande de promotion contient:

- phase actuelle et phase demandee;
- resultats des controles obligatoires;
- risques residuels;
- cout actuel et projete;
- rentabilite normale et stressee;
- version du code et de la strategie;
- date d'expiration;
- boutons approuver et refuser.

Le worker verifie a nouveau les conditions au moment de l'approbation. Une
approbation ne peut pas utiliser un rapport devenu perime entre l'affichage et
la confirmation.

## 13. Gestion des incidents

### Donnees de marche invalides

- aucun ordre;
- decision `no_trade`;
- incident warning si repetition;
- alerte uniquement apres seuil pour eviter le bruit.

### Kraken indisponible ou ambigu

- gel des nouvelles entrees;
- maintien des stops distants;
- reconciliation avec backoff;
- alerte critical si une position n'est pas verifiable.

### SQLite indisponible

- aucune nouvelle entree;
- mode lecture seule;
- generation d'un diagnostic depuis les fichiers lisibles;
- restauration uniquement apres verification d'integrite.

### Bot indisponible

- trading et protections inchanges;
- aucune promotion;
- alerte depuis le worker si le webhook de secours reste disponible.

### Worker indisponible

- le bot signale l'absence de heartbeat;
- Railway tente le redemarrage;
- les stops Kraken restent actifs;
- le worker reconcilie avant toute reprise.

### Cout inconnu

- trading limite existant non interrompu;
- qualification economique invalide;
- promotion bloquee;
- estimation conservatrice affichee.

### Budget Railway atteint

- les limites Railway doivent emettre une alerte avant le hard limit;
- le bot et le worker utilisent des plafonds CPU et RAM;
- le reseau prive evite l'egress inter-service;
- le hard limit Railway ne remplace jamais le kill switch financier.

## 14. Performance

Le chemin critique contient uniquement:

- lecture de la derniere bougie;
- verification de deduplication;
- calcul de signal;
- controles de risque en memoire;
- courte transaction SQLite;
- appel Kraken uniquement si necessaire.

Les optimisations obligatoires sont:

- une connexion exchange reutilisee par cycle;
- contexte macro, news et on-chain partage;
- concurrence limitee des donnees par symbole;
- aucune evaluation sur une bougie deja traitee;
- polling rapide uniquement pour ordre transitoire;
- reflexion IA et qualification hors cycle;
- rapports Markdown generes en arriere-plan;
- rotation et retention des JSONL.

## 15. Deploiement et securite operationnelle

- copier `uv.lock` dans l'image et utiliser un build verrouille;
- executer le conteneur avec un utilisateur non-root;
- ajouter un healthcheck Railway;
- limiter CPU et RAM du worker et du bot;
- utiliser le reseau prive pour worker-bot;
- interdire les environnements de preview permanents;
- configurer alertes et limites de cout Railway;
- utiliser une cle Kraken dediee sans permission de retrait;
- verifier automatiquement les permissions de la cle;
- conserver les secrets uniquement dans les variables Railway;
- ne jamais afficher un secret dans les rapports ou erreurs;
- sauvegarder les donnees SQLite et JSONL;
- tester la restauration;
- faire tourner les migrations avant le demarrage du trading;
- empecher deux replicas du worker d'executer simultanement des ordres.

## 16. Documentation et exploitation humaine

La documentation doit expliquer:

- etat normal et etats de securite;
- lecture de chaque fichier JSON et JSONL;
- interpretation des positions et ordres;
- calcul du risque et de la rentabilite;
- commandes Discord;
- procedures pause, kill, reprise et rollback;
- reconciliation manuelle Kraken;
- restauration SQLite depuis sauvegarde;
- fonctionnement Hermes/OpenAI;
- gestion des couts Railway;
- promotion entre phases.

Le projet fournit des commandes en lecture seule pour:

- verifier l'integrite SQLite;
- exporter toutes les tables en JSON;
- comparer SQLite et les JSONL;
- regenerer les instantanes;
- produire un rapport de diagnostic;
- verifier la readiness sans soumettre d'ordre.

Ces commandes ne sont pas necessaires pour lire les fichiers de secours.

## 17. Decisions retenues

1. SQLite WAL avec JSONL et instantanes humains.
2. Kraken Spot long-only sans levier.
3. Stop heberge chez Kraken.
4. Taille fondee sur le risque avec plafonds absolus.
5. Hermes natif et modeles gratuits auto-decouverts.
6. OpenAI en dernier recours sans persistance de preference.
7. Champion reel et challenger simule.
8. Promotion manuelle, rollback automatique uniquement defensif.
9. Reconciliation REST adaptative.
10. Promotion BTC, ETH, XRP puis SHIB.
11. Qualification automatique avec confirmation Discord.
12. Bot Railway separe sans cle Kraken.
13. Liste blanche Discord et TOTP pour augmenter le risque.
14. Rentabilite nette incluant Railway et IA.
15. Verrou economique avant live etendu.

## 18. Hors perimetre initial

- shorts;
- Futures;
- levier;
- trading haute frequence;
- WebSocket Kraken;
- plusieurs positions par symbole;
- pyramiding;
- promotion automatique d'une strategie IA;
- modification des limites de risque par Hermes;
- acces direct du bot aux secrets ou a SQLite;
- redemarrage Railway commande depuis Discord avec un token de compte large.

