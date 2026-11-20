"""Fingerprint of the executed recette sources (LAT-141, delivery 1).

Sources are copied into ``.test-tmp-recette-fingerprint-*`` trees (and a
throw-away git repository there): the real tree is never modified. No network,
no model, no secret; the service runs with an injected session double.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from hermes_trading import pilot_paper
from hermes_trading.paper_publication import _release_hashes
from scripts import recette_fingerprint as fingerprint
from scripts import recette_service as service
from scripts import recette_status_report as status_report

AT = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
SECRET = "SECRET_VALUE_never_fingerprinted"
CLEAN_REPORT = {"stop_reason": "max_cycles_reached", "completed_cycles": 1, "samples": 1,
                "entries": 0, "exits": 0, "public_proof": "not_eligible_injected_collector"}
HAS_GIT = shutil.which("git") is not None


def copy_sources(target: Path) -> Path:
    """Copy exactly the image inputs (``.dockerignore``) into ``target``."""
    target.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", ".test-tmp*")
    for name in ("hermes_trading", "scripts"):
        shutil.copytree(ROOT / name, target / name, ignore=ignore)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, target / name)
    return target


def git(repo: Path, *args: str) -> str:
    environ = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=environ).stdout.strip()


class FingerprintFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-fingerprint-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def summary(self, source: Path) -> dict:
        return fingerprint.summarize(fingerprint.tree_hashes(source))


class FingerprintStabilityTests(FingerprintFixture):
    def test_same_sources_give_the_same_fingerprint_wherever_they_live(self) -> None:
        first = copy_sources(self.base / "a")
        second = copy_sources(self.base / "nested" / "b")
        one, again, other = self.summary(first), self.summary(first), self.summary(second)
        self.assertEqual(one, again)
        self.assertEqual(one, other)
        self.assertEqual(one["status"], "identified")
        self.assertEqual(one["short"], one["fingerprint"][:12])
        self.assertEqual({name: value["files"] > 0 for name, value in one["components"].items()},
                         {"hermes_trading": True, "scripts": True, "dependencies": True})
        self.assertEqual(one["components"]["dependencies"]["files"], 2)

    def test_executed_sources_are_the_checked_out_sources(self) -> None:
        executed = fingerprint.runtime_fingerprint()
        self.assertEqual(executed, self.summary(copy_sources(self.base / "copy")))
        self.assertTrue(fingerprint.is_valid_summary(executed))

    def test_package_component_is_the_digest_of_the_pinned_release_map(self) -> None:
        release = _release_hashes()
        expected = hashlib.sha256(
            json.dumps(release, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        component = fingerprint.runtime_fingerprint()["components"]["hermes_trading"]
        self.assertEqual(component, {"files": len(release), "sha256": expected})


class FingerprintSensitivityTests(FingerprintFixture):
    def setUp(self) -> None:
        super().setUp()
        self.source = copy_sources(self.base / "src")
        self.before = self.summary(self.source)

    def assertChanged(self, component: str) -> None:
        after = self.summary(self.source)
        self.assertNotEqual(after["fingerprint"], self.before["fingerprint"])
        for name in fingerprint.COMPONENTS:
            same = after["components"][name] == self.before["components"][name]
            self.assertEqual(same, name != component, name)

    def assertUnchanged(self) -> None:
        self.assertEqual(self.summary(self.source), self.before)

    def test_modified_package_module_changes_the_fingerprint(self) -> None:
        path = self.source / "hermes_trading" / "risk.py"
        path.write_bytes(path.read_bytes() + b"\n# modified\n")
        self.assertChanged("hermes_trading")

    def test_modified_or_added_or_removed_script_changes_the_fingerprint(self) -> None:
        path = self.source / "scripts" / "recette_service.py"
        original = path.read_bytes()
        path.write_bytes(original.replace(b"TICK_SECONDS = 900.0", b"TICK_SECONDS = 899.0"))
        self.assertChanged("scripts")
        path.write_bytes(original)
        self.assertUnchanged()
        (self.source / "scripts" / "new_helper.py").write_text("X = 1\n")
        self.assertChanged("scripts")
        (self.source / "scripts" / "new_helper.py").unlink()
        (self.source / "scripts" / "recette_notifications.py").unlink()
        self.assertChanged("scripts")

    def test_modified_lock_file_changes_the_dependency_component(self) -> None:
        path = self.source / "uv.lock"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertChanged("dependencies")

    def test_files_outside_the_executed_sources_do_not_change_it(self) -> None:
        (self.source / "scripts" / "lot1_release_plan.json").write_text("{}")
        (self.source / "scripts" / "__pycache__").mkdir(exist_ok=True)
        (self.source / "scripts" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\0")
        (self.source / "scripts" / ".test-tmp-scratch").mkdir()
        (self.source / "scripts" / ".test-tmp-scratch" / "data.py").write_text("X = 2\n")
        (self.source / "docs").mkdir()
        (self.source / "docs" / "RECETTE.md").write_text("changed")
        (self.source / "tests").mkdir()
        (self.source / "tests" / "test_x.py").write_text("X = 3\n")
        self.assertUnchanged()

    def test_a_linked_source_file_is_refused(self) -> None:
        target = self.source / "scripts" / "recette_service.py"
        link = self.source / "scripts" / "linked.py"
        link.symlink_to(target)
        with self.assertRaises(fingerprint.FingerprintError):
            self.summary(self.source)

    def test_missing_component_is_refused(self) -> None:
        (self.source / "uv.lock").unlink()
        with self.assertRaises(fingerprint.FingerprintError):
            self.summary(self.source)


@unittest.skipUnless(HAS_GIT, "git is not installed")
class FingerprintGitTests(FingerprintFixture):
    """A commit is reconciled with a report without any checkout."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = copy_sources(self.base / "repo")
        (self.repo / "README.md").write_text("not a source\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fixture")
        self.commit = git(self.repo, "rev-parse", "HEAD")

    def test_commit_objects_and_checkout_give_the_same_fingerprint(self) -> None:
        from_git = fingerprint.summarize(fingerprint.git_hashes(self.repo, self.commit))
        self.assertEqual(from_git, self.summary(self.repo))
        self.assertEqual(fingerprint.git_hashes(self.repo, self.commit),
                         fingerprint.tree_hashes(self.repo))

    def test_uncommitted_change_is_visible_against_the_commit(self) -> None:
        path = self.repo / "scripts" / "recette_status_report.py"
        path.write_bytes(path.read_bytes() + b"\n")
        from_git = fingerprint.summarize(fingerprint.git_hashes(self.repo, self.commit))
        self.assertNotEqual(from_git["fingerprint"], self.summary(self.repo)["fingerprint"])
        git(self.repo, "commit", "-q", "-am", "change")
        self.assertEqual(fingerprint.summarize(fingerprint.git_hashes(self.repo, "HEAD")),
                         self.summary(self.repo))

    def test_cli_reconciles_a_commit_with_an_expected_fingerprint(self) -> None:
        expected = self.summary(self.repo)["fingerprint"]
        out = io.StringIO()
        code = fingerprint.main(["--repo", str(self.repo), "--git-rev", self.commit,
                                 "--expect", expected], stdout=out)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["matches"])
        out = io.StringIO()
        code = fingerprint.main(["--repo", str(self.repo), "--git-rev", self.commit,
                                 "--expect", "0" * 64], stdout=out)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["matches"])

    def test_option_like_or_unknown_revisions_are_refused(self) -> None:
        for rev in ("--output=x", "-p", "HEAD;true", "HEAD rm", "", "nonexistent"):
            with self.subTest(rev=rev):
                with self.assertRaises(fingerprint.FingerprintError):
                    fingerprint.git_hashes(self.repo, rev)


class FingerprintPrivacyTests(FingerprintFixture):
    def test_output_carries_digests_and_counts_only(self) -> None:
        os.environ["HERMES_TEST_FINGERPRINT_SECRET"] = SECRET
        self.addCleanup(os.environ.pop, "HERMES_TEST_FINGERPRINT_SECRET", None)
        out = io.StringIO()
        self.assertEqual(fingerprint.main([], stdout=out), 0)
        text = out.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn(os.sep + "hermes_trading", text)
        data = json.loads(text)
        self.assertEqual(set(data), {"schema_version", "status", "algorithm", "fingerprint",
                                     "short", "components"})
        for component in data["components"].values():
            self.assertRegex(component["sha256"], r"^[0-9a-f]{64}$")

    def test_file_listing_uses_relative_paths_only(self) -> None:
        out = io.StringIO()
        self.assertEqual(fingerprint.main(["--files"], stdout=out), 0)
        files = json.loads(out.getvalue())["files"]
        for component in files.values():
            for path, digest in component.items():
                self.assertFalse(path.startswith("/"), path)
                self.assertNotIn("..", path)
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn("recette_service.py", files["scripts"])
        self.assertIn("paper_publication.py", files["hermes_trading"])

    def test_tampered_or_free_form_summaries_are_never_republished(self) -> None:
        good = fingerprint.runtime_fingerprint()
        tampered = json.loads(json.dumps(good))
        tampered["components"]["scripts"]["sha256"] = "f" * 64
        extra = dict(good, path=str(ROOT))
        free_form = dict(good, fingerprint=SECRET, short=SECRET[:12])
        for value in (tampered, extra, free_form, {"status": "identified"}, "abc", None):
            with self.subTest(value=str(value)[:40]):
                self.assertFalse(fingerprint.is_valid_summary(value))
                self.assertEqual(status_report._runtime(value)["status"], "unavailable")
        self.assertEqual(status_report._runtime(good), good)
        self.assertFalse(fingerprint.is_valid_summary(dict(fingerprint.unavailable("x"), reason=SECRET + " /a")))


class ServiceContractTests(unittest.TestCase):
    """The served report identifies the runtime, the bounds and the volume."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-fingerprint-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper", "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.now = AT

    def clock(self):
        return self.now

    async def runner(self, root, *, environ, bounds, on_cycle=None):
        (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
        return dict(CLEAN_REPORT)

    async def no_sleep(self, seconds):
        return None

    def serve(self, environ=None, **kwargs):
        out = io.StringIO()
        options = dict(environ=self.environ if environ is None else environ, stream=out,
                       sleep=self.no_sleep, report_clock=self.clock, session_runner=self.runner)
        options.update(kwargs)
        asyncio.run(service.serve(self.root, **options))
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        report = status_report.read_recette_report(self.root / status_report.REPORT_NAME)
        return records, report

    def test_report_and_start_line_carry_the_executed_fingerprint(self) -> None:
        records, report = self.serve(max_cycles=1, max_runtime_seconds=60, interval_seconds=60,
                                     reflect_every=1)
        expected = fingerprint.runtime_fingerprint()
        self.assertEqual(report["runtime"], expected)
        self.assertEqual(records[0]["runtime_fingerprint"], expected["fingerprint"])
        self.assertEqual(records[0]["runtime_status"], "identified")
        self.assertEqual(report["worker_recovery"]["status"], "not_applicable")
        self.assertEqual(report["history_retention"]["status"], "not_applicable")
        self.assertNotIn(str(self.base), json.dumps(report))

    def test_bounds_provenance_distinguishes_argument_environment_and_default(self) -> None:
        environ = dict(self.environ, HERMES_RECETTE_INTERVAL_SECONDS="120")
        _, report = self.serve(environ=environ, max_cycles=3)
        self.assertEqual(report["bounds"], {
            "max_cycles": {"value": 3, "source": "argument"},
            "max_runtime_seconds": {"value": service.DEFAULT_MAX_RUNTIME_SECONDS, "source": "default"},
            "interval_seconds": {"value": 120.0, "source": "environment"},
            "reflect_every": {"value": service.DEFAULT_REFLECT_EVERY, "source": "default"},
        })
        _, provenance = service.resolve_bounds_with_provenance(
            dict(self.environ, HERMES_RECETTE_MAX_CYCLES="  "))
        self.assertEqual(provenance["max_cycles"]["source"], "default")

    def test_volume_identity_proves_a_restart_on_the_same_volume(self) -> None:
        _, before = self.serve(max_cycles=1, interval_seconds=60)
        (self.root / service.PILOT_DIRNAME / pilot_paper.STOP_FILE).unlink()
        self.now = AT + timedelta(hours=2)
        _, after = self.serve(max_cycles=1, interval_seconds=60)
        self.assertEqual(before["volume"], {"status": "initialized", "marker_created_at": AT.isoformat(),
                                            "service_started_at": AT.isoformat()})
        self.assertEqual(after["volume"], {"status": "recognized", "marker_created_at": AT.isoformat(),
                                           "service_started_at": self.now.isoformat()})
        self.assertEqual(before["runtime"], after["runtime"])


if __name__ == "__main__":
    unittest.main()
