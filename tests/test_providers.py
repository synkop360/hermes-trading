from datetime import UTC, datetime
from pathlib import Path
import unittest

from hermes_trading.providers import HermesCatalog, ModelCandidate, ProviderAttempt, ProviderRouter


NOW = datetime(2026, 8, 1, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "hermes_provider_models_cache.json"


class Store:
    def __init__(self) -> None:
        self.attempts: list[ProviderAttempt] = []

    def provider_attempts(self):
        return self.attempts

    def record_provider_attempt(self, attempt):
        self.attempts.append(attempt)


class ProviderRouterTests(unittest.TestCase):
    def test_catalog_keeps_only_explicitly_free_models(self):
        candidates = ProviderRouter(Store(), HermesCatalog.load(FIXTURE)).candidates(now=NOW)
        self.assertEqual([(item.provider, item.model) for item in candidates], [("nous", "tencent/hy3:free"), ("openrouter", "vendor/backup:free")])

    def test_rate_limited_model_does_not_block_other_provider(self):
        store = Store()
        router = ProviderRouter(store, HermesCatalog.load(FIXTURE))
        router.record_failure(ModelCandidate("nous", "tencent/hy3:free", True, "cache"), error_class="rate_limit", retry_after_seconds=600, now=NOW)
        self.assertEqual([item.provider for item in router.candidates(now=NOW)], ["openrouter"])

    def test_openai_is_never_returned_as_free_candidate(self):
        candidates = ProviderRouter(Store(), HermesCatalog.load(FIXTURE)).candidates(now=NOW)
        self.assertNotIn("openai", [item.provider for item in candidates])


if __name__ == "__main__":
    unittest.main()
