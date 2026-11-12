"""Same-host transport for the existing paper supervisor receivers.

This adapter grants no launch permission and initializes no state. Activation
must retain the watcher's launch, release, lifecycle and publication checks.
Receiver output is private: callers must never write it to application logs.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


class TransportError(RuntimeError):
    """Code-only failure; receiver output must not escape through exceptions."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate response key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite response')


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite response')
    return result


def _run_bounded(command, *, input, cwd, env, timeout, max_bytes, **options):
    """Drain both streams concurrently and kill at a combined byte ceiling."""
    output = bytearray()
    total = 0
    exceeded = threading.Event()
    failed = threading.Event()
    lock = threading.Lock()
    # File-backed stdin avoids blocking the deadline while a child refuses
    # to read. The temporary file closes on every path and is never logged.
    with tempfile.TemporaryFile(dir=cwd) as source:
        if input is not None:
            source.write(input.encode('utf-8'))
        source.seek(0)
        child = subprocess.Popen(command, cwd=cwd, env=env, stdin=source,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        def drain(stream, keep):
            nonlocal total
            try:
                while True:
                    chunk = stream.read1(8192)
                    if not chunk:
                        break
                    with lock:
                        total += len(chunk)
                        if total > max_bytes:
                            exceeded.set()
                            child.kill()
                        elif keep:
                            output.extend(chunk)
            except (OSError, ValueError):
                failed.set()
                child.kill()
            finally:
                stream.close()
        readers = [threading.Thread(target=drain, args=(child.stdout, True), daemon=True),
                   threading.Thread(target=drain, args=(child.stderr, False), daemon=True)]
        for reader in readers:
            reader.start()
        uncertain = False
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            uncertain = True
            child.kill()
            child.wait()
        finally:
            for reader in readers:
                reader.join(timeout=1)
    if uncertain:
        raise TransportError('receiver_outcome_uncertain')
    if exceeded.is_set():
        raise TransportError('receiver_response_exceeds_bound')
    if failed.is_set() or any(reader.is_alive() for reader in readers):
        raise TransportError('receiver_response_unavailable')
    return subprocess.CompletedProcess(command, child.returncode, output.decode('utf-8'), '')


class LocalPaperTransport:
    MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, project, root, primary, *, env=None, command_run=None):
        self.env = dict(os.environ if env is None else env)
        if (self.env.get('HERMES_TRADING_MODE') != 'paper' or
                self.env.get('HERMES_TRADING_ALLOW_REAL_ORDERS') != 'false'):
            raise ValueError('explicit paper-only environment required')
        if primary not in {'adaptive_50', 'adaptive_100', 'adaptive_150', 'adaptive_200'}:
            raise ValueError('explicit adaptive primary required')
        self.project, self.root = Path(project).resolve(strict=True), Path(root).resolve(strict=True)
        self.state = (self.root / primary).resolve(strict=True)
        if (not self.project.is_dir() or not self.root.is_dir() or not self.state.is_dir() or
                self.state.parent != self.root):
            raise ValueError('existing separate primary directory required')
        self.command_run = command_run or _run_bounded
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
            self.env.pop(name, None)

    def _call(self, module, arguments, request=None):
        payload = None if request is None else json.dumps(request, allow_nan=False, separators=(',', ':'))
        if payload is not None and len(payload.encode('utf-8')) > self.MAX_BYTES:
            raise ValueError('request exceeds transport bound')
        command = [sys.executable, '-m', 'hermes_trading.' + module, *arguments]
        failure = None
        try:
            result = self.command_run(command, cwd=self.project, env=self.env.copy(),
                input=payload, capture_output=True, text=True, encoding='utf-8',
                errors='strict', timeout=30,
                max_bytes=self.MAX_BYTES,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        except subprocess.TimeoutExpired:
            failure = 'receiver_outcome_uncertain'
        except (OSError, UnicodeError):
            failure = 'receiver_unavailable'
        # Raise outside the handler: even exception context must not contain
        # private stdout, stderr, requests or credentials.
        if failure is not None:
            raise TransportError(failure)
        if result.returncode != 0:
            raise TransportError('receiver_rejected')
        if not isinstance(result.stdout, str) or len(result.stdout.encode('utf-8')) > self.MAX_BYTES:
            raise TransportError('receiver_response_exceeds_bound')
        response = None
        try:
            response = json.loads(result.stdout, object_pairs_hook=_pairs,
                parse_constant=_constant, parse_float=_float)
        except (ValueError, RecursionError):
            pass
        if not isinstance(response, dict):
            raise TransportError('receiver_response_invalid')
        return response

    def snapshot(self):
        return self._call('supervisor_sync', ['--state-dir', str(self.state)])

    def publish(self, request):
        return self._call('paper_publication', ['--state-dir', str(self.state)], request)

    def usage(self, request):
        return self._call('supervisor_usage_feed', ['--state-dir', str(self.state)], request)

    def access(self, request):
        return self._call('model_access_refresh', ['install', '--state-dir', str(self.state)], request)

    def health(self, request):
        raw = json.dumps(request, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > 2048:
            raise ValueError('health request exceeds argument bound')
        return self._call('paper_supervisor_health', ['--root', str(self.root),
            '--request-base64', base64.b64encode(raw).decode('ascii')])
