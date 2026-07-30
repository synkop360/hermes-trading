# Hermes Trading Live Hardening Roadmap

La specification de reference est:

`docs/superpowers/specs/2026-07-30-live-trading-hardening-design.md`

L'implementation est divisee en cinq plans. Ils doivent etre executes dans
l'ordre, car chaque plan publie des interfaces consommees par le suivant.

| Ordre | Plan | Resultat testable |
|---|---|---|
| 1 | `2026-07-30-state-and-simulation-foundation.md` | SQLite WAL, fichiers humains, bougies dedupliquees et vraies positions simulees |
| 2 | `2026-07-30-kraken-risk-and-reconciliation.md` | Taille par risque, ordres idempotents, stops Kraken et reconciliation |
| 3 | `2026-07-30-hermes-routing-strategy-and-costs.md` | Hermes gratuit auto-decouvert, OpenAI fallback, champion/challenger et PnL net |
| 4 | `2026-07-30-qualification-and-discord-control.md` | Qualification automatique, API privee, bot Discord, TOTP et promotions |
| 5 | `2026-07-30-deployment-validation-and-operations.md` | Tests de panne, image durcie, sauvegarde, documentation et promotion Railway |

## Gates entre plans

Un plan est termine uniquement lorsque:

1. tous ses tests cibles passent;
2. la suite complete existante passe;
3. les nouveaux fichiers humains sont lisibles avec un editeur de texte;
4. `graphify update .` a ete execute;
5. le commit du plan ne contient aucun fichier d'etat, secret ou changement
   etranger au lot.

Le mode Railway reste `training` pendant les plans 1 a 4. Le plan 5 autorise
uniquement `validation_ready`, puis `kraken_validate_only`; le micro-live exige
ensuite la qualification et la confirmation Discord prevues par le systeme.
