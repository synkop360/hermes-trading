"""Run the pinned full suite from a disposable writable Linux tmpfs copy.

Only research evidence is written on the host. The frozen package and inventory
are mounted read-only; no image is pulled and the container has no network.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import time
import uuid


RUN = Path(__file__).resolve().parent
COMMIT = "c7f816d1e6dcc162ebc4ee6c7100c9d092702f15"
IMAGE = "hermes-paired-cost-reserve:20261015"
MAX_SECONDS = 1800


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_exact(root, pins):
    """Reject missing, extra, unsafe, linked or changed frozen source files."""
    for name in pins:
        part = PurePosixPath(name)
        if (part.is_absolute() or ".." in part.parts or "\\" in name
                or ":" in name or part.as_posix() != name):
            raise ValueError("unsafe_inventory_path")
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("linked_package_entry")
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = sha(path)
    if actual != pins:
        raise ValueError("frozen_package_inventory_differs")
    return len(actual)


INNER = r'''
import hashlib, importlib, json, os, signal, sys, unittest
from pathlib import Path, PurePosixPath

signal.alarm(1800)
source = Path('/source')
raw = Path('/source-pins.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == os.environ['EXPECTED_PINS_SHA256']
pins = json.loads(raw)
assert len(pins) == 675
actual = set()
for path in source.rglob('*'):
    assert not path.is_symlink(), 'source_symlink'
    if path.is_file():
        actual.add(path.relative_to(source).as_posix())
assert actual == set(pins), 'source_file_set_differs'
workspace = Path('/tmp/workspace')
workspace.mkdir()
for name, digest in pins.items():
    part = PurePosixPath(name)
    assert not part.is_absolute() and '..' not in part.parts
    assert '\\' not in name and ':' not in name and part.as_posix() == name
    original, target = source / name, workspace / name
    data = original.read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
assert {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in workspace.rglob('*') if p.is_file()} == pins
os.chdir(workspace)
sys.path[:0] = [str(workspace / 'tests'), str(workspace)]
importlib.invalidate_caches()  # PYTHONPATH named the workspace before its copy existed.
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash
assert _hash(_release_hashes()) == os.environ['EXPECTED_RUNTIME_HASH']
print('NATIVE_COPY ' + json.dumps(dict(files=len(pins), inventory_sha256=hashlib.sha256(raw).hexdigest(),
    runtime_hash=_hash(_release_hashes()), workspace=str(workspace), uid=os.getuid())), flush=True)
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=2).run(suite)
summary = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
    skipped=[dict(test=str(test), reason=reason) for test, reason in result.skipped],
    failure_tests=[test.id() for test, _ in result.failures],
    error_tests=[test.id() for test, _ in result.errors],
    expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses),
    successful=result.wasSuccessful(), runtime_hash=_hash(_release_hashes()))
print('NATIVE_RESULT ' + json.dumps(summary, sort_keys=True), flush=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def command(args, *, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, check=True)


def main():
    metadata = json.loads((RUN / "package.json").read_text())
    assert metadata["commit"] == COMMIT and metadata["files"] == 675
    package = Path(metadata["package"]).resolve()
    inventory = package.with_name(package.name + ".raw-sha256.json")
    pin_hash = sha(inventory)
    assert pin_hash == metadata["inventory_sha256"]
    pins = json.loads(inventory.read_bytes())
    assert len(pins) == 675
    verify_exact(package, pins)
    image_id = command(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]).stdout.strip()
    assert image_id.startswith("sha256:") and len(image_id) == 71

    started_at = datetime.now(timezone.utc)
    label = started_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = RUN / ("linux-full-native-" + label)
    evidence.mkdir()
    cidfile, log = evidence / "container.cid", evidence / "suite.log"
    receipt_path = evidence / "receipt.json"
    args = ["docker", "create", "--pull", "never", "--name", "hermes-full-" + label.lower(),
            "--cidfile", str(cidfile), "--network", "none", "--read-only", "--user", "65534:65534",
            "--env", "PYTHONPATH=/tmp/workspace:/tmp/workspace/tests",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
            "--env", "EXPECTED_PINS_SHA256=" + pin_hash,
            "--env", "EXPECTED_RUNTIME_HASH=" + metadata["runtime_hash"],
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=1073741824,mode=1777",
            "--mount", f"type=bind,source={package},target=/source,readonly",
            "--mount", f"type=bind,source={inventory},target=/source-pins.json,readonly",
            "--workdir", "/tmp", "--entrypoint", "/app/.venv/bin/python",
            image_id, "-B", "-u", "-X", "utf8", "-c", INNER]
    receipt = dict(status="starting", candidate=COMMIT, package=str(package), files=675,
                   source_inventory_sha256=pin_hash, image=image_id, network="none", uid=65534,
                   root_read_only=True, source_read_only=True, workspace="/tmp/workspace",
                   max_seconds=MAX_SECONDS, started_at=started_at.isoformat(), log=str(log),
                   cidfile=str(cidfile), scope="full exact-package unittest discovery")
    child = None
    cid = None
    try:
        command(args)
        cid = cidfile.read_text().strip()
        assert len(cid) == 64 and all(c in "0123456789abcdef" for c in cid)
        receipt["container_id"] = cid
        inspected = json.loads(command(["docker", "inspect", cid]).stdout)[0]
        assert inspected["Image"] == image_id
        assert inspected["HostConfig"]["NetworkMode"] == "none"
        assert inspected["HostConfig"]["ReadonlyRootfs"] is True
        assert inspected["Config"]["User"] == "65534:65534"
        assert all(not mount["RW"] for mount in inspected["Mounts"] if mount["Type"] == "bind")
        receipt["status"] = "running"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(dict(status="running", receipt=str(receipt_path), log=str(log),
                              cidfile=str(cidfile), container_id=cid)), flush=True)
        running_at = time.monotonic()
        with log.open("wb", buffering=0) as stream:
            child = subprocess.Popen(["docker", "start", "--attach", cid], stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT)
            next_progress = running_at + 30
            while child.poll() is None:
                now = time.monotonic()
                if now - running_at >= MAX_SECONDS:
                    receipt["status"] = "timed_out"
                    break
                if now >= next_progress:
                    print(json.dumps(dict(status="running", elapsed_seconds=round(now-running_at, 1),
                                          log_bytes=log.stat().st_size, container_id=cid)), flush=True)
                    next_progress = now + 30
                time.sleep(min(1, MAX_SECONDS - (now-running_at)))
        if receipt["status"] != "timed_out":
            receipt["attach_exit_code"] = child.returncode
            state = json.loads(command(["docker", "inspect", "--format", "{{json .State}}", cid]).stdout)
            receipt["container_exit_code"] = state["ExitCode"]
            receipt["container_state"] = state
            receipt["status"] = "finished"
    except (Exception, KeyboardInterrupt) as error:
        receipt["status"] = "harness_error"
        receipt["error_type"] = type(error).__name__
        receipt["error"] = str(error)
    finally:
        if cid is None and cidfile.exists():
            cid = cidfile.read_text().strip()
        if cid:
            try:
                command(["docker", "rm", "--force", cid], timeout=20)
            except Exception as error:
                receipt["cleanup_error"] = type(error).__name__ + ": " + str(error)
            try:
                absent = command(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                                  "--filter", "id=" + cid], timeout=15).stdout.strip() == ""
                receipt["container_confirmed_absent"] = absent
            except Exception as error:
                receipt["absence_check_error"] = type(error).__name__
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        try:
            receipt["source_files_verified_after"] = verify_exact(package, pins)
            receipt["source_inventory_sha256_after"] = sha(inventory)
            assert receipt["source_inventory_sha256_after"] == pin_hash
            receipt["frozen_sources_unchanged"] = True
        except Exception as error:
            receipt["frozen_sources_unchanged"] = False
            receipt["source_check_error"] = type(error).__name__ + ": " + str(error)
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
        receipt["copy_results"] = [json.loads(line.removeprefix("NATIVE_COPY "))
                                   for line in lines if line.startswith("NATIVE_COPY ")]
        receipt["results"] = [json.loads(line.removeprefix("NATIVE_RESULT "))
                              for line in lines if line.startswith("NATIVE_RESULT ")]
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        success = (receipt["status"] == "finished" and receipt.get("container_exit_code") == 0
                   and receipt.get("container_confirmed_absent") is True
                   and receipt["frozen_sources_unchanged"] and len(receipt["results"]) == 1
                   and receipt["results"][0]["successful"])
        receipt["successful"] = success
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
