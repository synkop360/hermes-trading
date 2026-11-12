"""Run only synthetic legacy/native reporter tests; deny raw HTTP transports."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import httpx

started = time.monotonic()
with patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("real HTTP forbidden")), \
     patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("real HTTP forbidden")):
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "tests.test_notifications", "tests.test_paper_notifications"])
    with Path(__file__).with_name("reporter-regression.txt").open("w", encoding="utf-8") as output:
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
summary = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
           "skipped": len(result.skipped), "successful": result.wasSuccessful(),
           "seconds": round(time.monotonic() - started, 3), "raw_http_transports_denied": True}
Path(__file__).with_name("reporter-regression.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary))
raise SystemExit(not result.wasSuccessful())
