# Hermes Routing Strategy And Costs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire de Hermes et de ses modeles gratuits la voie normale de reflexion, isoler les strategies challenger du live et calculer une rentabilite nette incluant Railway, Kraken et l'IA.

**Architecture:** ProviderRouter appelle d'abord Hermes sans override, puis utilise son catalogue dynamique pour les autres candidats gratuits; OpenAI reste le dernier recours et n'est jamais memorise comme preference. StrategyRegistry separe champion et challenger. CostLedger collecte des evenements Decimal et ProfitabilityService produit les indicateurs et fichiers humains.

**Tech Stack:** Python 3.11, subprocess, HTTPX, YAML, SQLite WAL, `Decimal`, `unittest`.

## Global Constraints

- Hermes est toujours tente avant OpenAI a chaque nouvelle reflexion.
- Aucun modele OpenAI n'est defini par defaut dans le watchdog.
- Seuls les modeles explicitement identifies gratuits sont auto-selectionnes.
- Un cout inconnu n'est jamais zero.
- En live et validation-only, Hermes ecrit uniquement le challenger.
- Hermes ne modifie jamais les limites de risque ou les criteres de promotion.
- Les reflexions et rapports de cout restent hors du cycle de trading.

---

## File Structure

- Create `hermes_trading/providers.py`: catalogue, classement et cooldowns.
- Create `hermes_trading/model_runner.py`: Hermes oneshot et OpenAI fallback.
- Create `hermes_trading/strategy_registry.py`: champion/challenger/promotion.
- Create `hermes_trading/costs.py`: ledger de couts et sources Railway.
- Create `hermes_trading/profitability.py`: indicateurs et scenario stresse.
- Modify `hermes_trading/reflect.py`: utiliser le routeur et le challenger.
- Modify `scripts/hermes_watch.py`: supprimer les defaults OpenAI.
- Modify `hermes_trading/score.py`: scorer des positions completes nettes.
- Create `tests/fixtures/hermes_provider_models_cache.json`.
- Create `tests/test_providers.py`, `tests/test_model_runner.py`,
  `tests/test_strategy_registry.py`, `tests/test_costs.py`,
  `tests/test_profitability.py`.

### Task 1: Discover And Rank Free Hermes Models

**Files:**
- Create: `hermes_trading/providers.py`
- Create: `tests/fixtures/hermes_provider_models_cache.json`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `$HERMES_HOME/provider_models_cache.json`, provider attempt rows.
- Produces: `ModelCandidate`, `ProviderCooldown`, `HermesCatalog.load()`,
  `ProviderRouter.candidates()`, `record_success()`, `record_failure()`.

- [ ] **Step 1: Write failing catalog and cooldown tests**

```python
class ProviderRouterTests(unittest.TestCase):
    def test_catalog_keeps_only_explicitly_free_models(self):
        catalog = HermesCatalog.load(FIXTURE_CACHE)
        candidates = ProviderRouter(self.repo, catalog).candidates(now=NOW)
        self.assertEqual(
            [(item.provider, item.model) for item in candidates],
            [("nous", "tencent/hy3:free"), ("openrouter", "vendor/backup:free")],
        )

    def test_rate_limited_model_does_not_block_other_provider(self):
        router = ProviderRouter(self.repo, fixture_catalog())
        router.record_failure(
            ModelCandidate("nous", "a:free", True, "cache"),
            error_class="rate_limit",
            retry_after_seconds=600,
            now=NOW,
        )
        self.assertEqual(
            [item.provider for item in router.candidates(now=NOW)],
            ["openrouter"],
        )

    def test_openai_is_never_returned_as_free_candidate(self):
        self.assertNotIn("openai", [item.provider for item in ProviderRouter(...).candidates(now=NOW)])
```

- [ ] **Step 2: Run provider tests and verify failure**

Run: `uv run python -m unittest tests.test_providers -v`

Expected: provider module is missing.

- [ ] **Step 3: Implement tolerant Hermes cache parsing**

The parser accepts cache entries containing a list of model strings or model
objects. A model is free only when one of these conditions is explicit:

```python
model_id.endswith(":free")
or metadata.get("free") is True
or (
    Decimal(str(metadata["input_cost"])) == 0
    and Decimal(str(metadata["output_cost"])) == 0
)
```

Unknown price metadata means `free=False`. Never inspect or expose credential
fingerprints. If the cache is absent or incompatible, return an empty catalog;
the default Hermes call in Task 2 remains available.

- [ ] **Step 4: Implement stable ranking**

Rank by:

1. Nous provider;
2. no active cooldown;
3. most recent success;
4. lower recent latency;
5. model/provider lexical order for deterministic tests.

Persist cooldown and health in `provider_attempts`; project
`provider_status.json`.

- [ ] **Step 5: Run and commit**

Run: `uv run python -m unittest tests.test_providers -v`

```bash
git add hermes_trading/providers.py tests/fixtures/hermes_provider_models_cache.json tests/test_providers.py
git commit -m "feat: discover free hermes model candidates"
```

### Task 2: Route Hermes First And OpenAI Last

**Files:**
- Create: `hermes_trading/model_runner.py`
- Modify: `hermes_trading/reflect.py`
- Modify: `scripts/hermes_watch.py`
- Test: `tests/test_model_runner.py`
- Modify: `tests/test_score_reflect.py`

**Interfaces:**
- Consumes: `ProviderRouter`, reflection prompt, environment.
- Produces: `ModelRunResult`, `ReflectionModelRunner.run(prompt)`,
  `HermesInvocation.run()`, `OpenAIFallback.run()`.

- [ ] **Step 1: Write failing routing-order tests**

```python
class ReflectionModelRunnerTests(unittest.TestCase):
    def test_default_hermes_is_first_and_has_no_model_override(self):
        runner = fixture_runner(default_hermes_result=valid_json())
        result = runner.run("prompt")
        self.assertEqual(runner.commands[0][:2], ["hermes", "-z"])
        self.assertNotIn("--provider", runner.commands[0])
        self.assertNotIn("-m", runner.commands[0])
        self.assertEqual(result.provider, "hermes_auto")

    def test_openai_runs_only_after_all_free_candidates(self):
        runner = fixture_runner(
            default_hermes_error=RuntimeError("unavailable"),
            free_candidates=[("nous", "a:free"), ("openrouter", "b:free")],
            candidate_errors=[RuntimeError("429"), RuntimeError("overload")],
            openai_result=valid_json(),
        )
        result = runner.run("prompt")
        self.assertEqual(runner.providers_called, ["hermes_auto", "nous", "openrouter", "openai_api"])
        self.assertEqual(result.provider, "openai_api")

    def test_next_reflection_starts_with_hermes_after_openai_success(self):
        runner = fixture_runner_with_previous_openai_success()
        runner.run("new prompt")
        self.assertEqual(runner.providers_called[0], "hermes_auto")
```

- [ ] **Step 2: Run routing tests and verify failure**

Run: `uv run python -m unittest tests.test_model_runner -v`

Expected: model runner is missing.

- [ ] **Step 3: Implement Hermes oneshot with usage report**

Run:

```python
command = ["hermes", "-z", prompt, "--usage-file", str(usage_path)]
```

For explicit free candidates, append `--provider` and `-m`. Use a subprocess
timeout configured by `HERMES_REFLECTION_TIMEOUT_SECONDS`. Parse only the last
valid JSON object from stdout. Read token/cost metadata from the usage file and
delete the temporary file in `finally`.

- [ ] **Step 4: Implement OpenAI fallback and failure classification**

OpenAI runs only when a key exists and all Hermes attempts failed. Record input
and output tokens from the Responses API. Classify `429`, connection, timeout,
overload, invalid-model and invalid-output separately. Respect `Retry-After`.

- [ ] **Step 5: Replace reflection selection and remove duplication**

Replace `select_reflection_models()` usage with `ReflectionModelRunner`.
Remove the duplicate `_normalize_strategy_proposal()` implementation, retaining
the version that uppercases and validates asset names. If all attempts fail,
write `hermes_asset_failed` and return no change.

In `scripts/hermes_watch.py`, delete:

```python
os.environ.setdefault(
    "HERMES_REFLECTION_MODELS",
    "openai:gpt-5.6-luna,openai:gpt-5.6-terra",
)
```

- [ ] **Step 6: Run routing and reflection suites**

Run: `uv run python -m unittest tests.test_model_runner tests.test_score_reflect -v`

Expected: Hermes-first, fallback and existing one-variable tests pass.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/model_runner.py hermes_trading/reflect.py scripts/hermes_watch.py tests/test_model_runner.py tests/test_score_reflect.py
git commit -m "feat: keep openai as reflection fallback"
```

### Task 3: Separate Champion And Challenger Strategies

**Files:**
- Create: `hermes_trading/strategy_registry.py`
- Modify: `hermes_trading/reflect.py`
- Modify: `hermes_trading/loop.py`
- Test: `tests/test_strategy_registry.py`

**Interfaces:**
- Consumes: repository strategy versions and YAML files.
- Produces: `StrategyRegistry.load_champion()`, `load_challenger()`,
  `propose_challenger()`, `promote_challenger(approval)`,
  `defensive_rollback(reason)`, `StrategyVersion`.

- [ ] **Step 1: Write failing isolation and promotion tests**

```python
class StrategyRegistryTests(unittest.TestCase):
    def test_live_reflection_writes_candidate_only(self):
        registry = fixture_registry(mode="live")
        registry.propose_challenger(candidate_strategy(version="70"))
        self.assertEqual(registry.load_champion()["version"], "69")
        self.assertEqual(registry.load_challenger()["version"], "70")

    def test_promotion_requires_fresh_approved_qualification(self):
        with self.assertRaises(PromotionBlocked):
            self.registry.promote_challenger(approval=stale_approval())

    def test_defensive_rollback_cannot_raise_any_risk_limit(self):
        with self.assertRaises(UnsafeRollback):
            self.registry.defensive_rollback(reason="test", target=more_aggressive_strategy())
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_strategy_registry -v`

Expected: registry module is missing.

- [ ] **Step 3: Implement registry and atomic YAML updates**

Archive every champion in `state/history/`. `candidate_strategy.yaml` contains
metadata:

```yaml
candidate:
  based_on_version: "69"
  proposed_at: "2026-07-30T20:00:00+00:00"
  provider: "nous"
  model: "tencent/hy3:free"
strategy:
  version: "70"
```

Promotion verifies approval hash, qualification freshness and exact candidate
hash, then atomically replaces `strategy.yaml`.

- [ ] **Step 4: Route reflection output by trading mode**

Training may update champion as today. `validation-only` and `live` call
`propose_challenger()`. The loop evaluates champion for real orders and
challenger with the simulation PositionEngine using a separate portfolio ID.

- [ ] **Step 5: Run and commit**

Run: `uv run python -m unittest tests.test_strategy_registry tests.test_score_reflect -v`

```bash
git add hermes_trading/strategy_registry.py hermes_trading/reflect.py hermes_trading/loop.py tests/test_strategy_registry.py
git commit -m "feat: isolate champion and challenger strategies"
```

### Task 4: Record Railway, Kraken And Model Costs

**Files:**
- Create: `hermes_trading/costs.py`
- Modify: `hermes_trading/model_runner.py`
- Modify: `hermes_trading/execution.py`
- Test: `tests/test_costs.py`

**Interfaces:**
- Consumes: fills, model usage reports, Railway API or fallback settings.
- Produces: `CostEvent`, `CostLedger.record()`, `costs_between()`,
  `RailwayCostSource.fetch()`, `ConfiguredMonthlyCostSource.fetch()`.

- [ ] **Step 1: Write failing cost-source tests**

```python
class CostLedgerTests(unittest.TestCase):
    def test_unknown_railway_cost_uses_configured_monthly_fallback(self):
        source = RailwayCostSource(api=FailingApi(), fallback_monthly=Decimal("8"))
        cost = source.cost_for_window(days=Decimal("15"), month_days=Decimal("30"))
        self.assertEqual(cost.amount, Decimal("4"))
        self.assertEqual(cost.quality, "estimated")

    def test_missing_real_and_fallback_cost_is_incomplete_not_zero(self):
        cost = RailwayCostSource(api=FailingApi(), fallback_monthly=None).cost_for_window(...)
        self.assertIsNone(cost.amount)
        self.assertEqual(cost.quality, "missing")

    def test_openai_usage_records_versioned_price(self):
        event = openai_cost_event(input_tokens=1000, output_tokens=200, pricing=PRICE_TABLE)
        self.assertEqual(event.pricing_version, "2026-07-30")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_costs -v`

Expected: costs module is missing.

- [ ] **Step 3: Implement the append-only cost ledger**

Store categories `kraken_fee`, `slippage`, `railway_worker`, `railway_bot`,
`hermes`, `openai`. Every event includes currency, amount or `None`, quality
`actual|estimated|missing`, source, period and metadata. Project `costs.json`
and `cost_events.jsonl`.

- [ ] **Step 4: Implement Railway source priority**

Use a project-scoped API token when configured. Query at most hourly and cache
the result. If the GraphQL field is unavailable or unauthorized, use:

```text
HERMES_RAILWAY_MONTHLY_COST_USD
HERMES_RAILWAY_SUBSCRIPTION_ALLOCATION_PCT=100
HERMES_RAILWAY_BOT_COST_SHARE_PCT
```

Never require an account-wide token. Private worker-bot traffic is not counted
as public egress.

- [ ] **Step 5: Wire Kraken and model events**

Record actual fill fees and decision-to-fill slippage. ModelRunner records
Hermes usage at zero only when the candidate is confirmed free; otherwise the
amount is `None`, which blocks economic qualification.

- [ ] **Step 6: Run and commit**

Run: `uv run python -m unittest tests.test_costs tests.test_model_runner tests.test_execution_service -v`

```bash
git add hermes_trading/costs.py hermes_trading/model_runner.py hermes_trading/execution.py tests/test_costs.py
git commit -m "feat: account for trading and operating costs"
```

### Task 5: Calculate Net Profitability And Stress Scenario

**Files:**
- Create: `hermes_trading/profitability.py`
- Modify: `hermes_trading/score.py`
- Modify: `hermes_trading/notifications.py`
- Test: `tests/test_profitability.py`

**Interfaces:**
- Consumes: closed positions and CostLedger window totals.
- Produces: `ProfitabilityReport`, `ProfitabilityService.calculate()`,
  `economic_gate()`, `render_profitability_markdown()`.

- [ ] **Step 1: Write failing net and stress tests**

```python
class ProfitabilityTests(unittest.TestCase):
    def test_net_pnl_subtracts_all_cost_categories(self):
        report = calculate_report(
            gross_pnl="20",
            kraken_fees="2",
            slippage="1",
            railway="5",
            ai="1",
        )
        self.assertEqual(report.net_pnl, Decimal("11"))

    def test_missing_cost_blocks_extended_live(self):
        report = calculate_report(gross_pnl="20", railway=None)
        gate = economic_gate(report)
        self.assertFalse(gate.passed)
        self.assertIn("railway cost missing", gate.reasons)

    def test_stress_reduces_pnl_and_increases_costs(self):
        report = calculate_report(gross_pnl="100", total_costs="60")
        self.assertEqual(report.stressed_pnl, Decimal("80") - Decimal("75"))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_profitability -v`

Expected: profitability module is missing.

- [ ] **Step 3: Implement report and economic gate**

Calculate gross PnL, trading costs, operating costs, net PnL, net return,
30-day projection, break-even capital, operating coverage and stress result.
The extended-live gate requires:

```python
projected_net_30d > 0
and operating_coverage >= Decimal("1.5")
and stressed_net_pnl > 0
and not missing_required_costs
```

Micro-live reports the same metrics but does not apply this gate.

- [ ] **Step 4: Replace percentage-only scoring**

Score completed positions using actual realized Decimal PnL after fees. Keep a
compatibility path for old training JSONL during migration, marked
`data_quality="legacy_estimate"`.

- [ ] **Step 5: Produce human and Discord summaries**

Write `profitability.json` and `profitability_report.md`. Add a compact Discord
summary with gross, trading costs, hosting, AI, net, break-even capital and
stress verdict.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run python -m unittest discover -s tests`

Expected: all tests pass.

Run: `graphify update .`

```bash
git add hermes_trading/profitability.py hermes_trading/score.py hermes_trading/notifications.py tests/test_profitability.py graphify-out
git commit -m "feat: report profitability after all costs"
```
