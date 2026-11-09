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
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


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


def _process_tree(command, *, cwd, env, source):
    """Own every descendant before allowing the native receiver to execute."""
    if os.name != 'nt':
        inherited=os.environ.get('HERMES_SUPERVISOR_PROCESS_TREE')=='session'
        if inherited and (os.getsid(0)!=os.getpgrp() or os.getsid(0)<=1):
            raise OSError('inherited process containment differs')
        environment=dict(env,HERMES_SUPERVISOR_PROCESS_TREE='session')
        child=subprocess.Popen(command,cwd=cwd,env=environment,stdin=source,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=not inherited)
        def stop(hard=True):
            try:
                if inherited:
                    if child.poll() is None:child.kill() if hard else child.terminate()
                else:os.killpg(child.pid,signal.SIGKILL if hard else signal.SIGTERM)
            except ProcessLookupError:pass
        # Nested native receivers stay in the outer watcher's process group.
        # Its owner kills that complete group on every termination path.
        return child,stop,lambda:stop(True)
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',wintypes.DWORD),
            ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('process_limit',wintypes.DWORD),
            ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',ctypes.c_uint64*6),('process_memory',ctypes.c_size_t),
            ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    job=kernel.CreateJobObjectW(None,None)
    if not job:raise OSError('process containment unavailable')
    child=None
    try:
        limits=Extended();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            raise OSError('process containment unavailable')
        child=subprocess.Popen(command,cwd=cwd,env=env,stdin=source,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW|0x4)  # CREATE_SUSPENDED
        if not kernel.AssignProcessToJobObject(job,int(child._handle)):
            raise OSError('process containment unavailable')
        # Assign while suspended: even an immediately spawned descendant must
        # join the job. Resume only after ownership succeeds.
        resume=ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes=[wintypes.HANDLE];resume.restype=wintypes.LONG
        if resume(int(child._handle))<0:raise OSError('contained process unavailable')
    except BaseException:
        if child is not None:
            child.kill();child.wait()
            child.stdout.close();child.stderr.close()
        kernel.CloseHandle(job)
        raise
    def stop(hard=True):kernel.TerminateJobObject(job,1)
    return child,stop,lambda:kernel.CloseHandle(job)


def _run_bounded(command, *, input=None, cwd, env, timeout, max_bytes, stop_event=None, **options):
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
        child,stop_tree,close_tree=_process_tree(command,cwd=cwd,env=env,source=source)
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
                            stop_tree()
                        elif keep:
                            output.extend(chunk)
            except (OSError, ValueError):
                failed.set()
                stop_tree()
            finally:
                stream.close()
        readers = [threading.Thread(target=drain, args=(child.stdout, True), daemon=True),
                   threading.Thread(target=drain, args=(child.stderr, False), daemon=True)]
        for reader in readers:
            reader.start()
        uncertain = False
        deadline=time.monotonic()+timeout
        try:
            while child.poll() is None:
                if (stop_event is not None and stop_event.is_set()) or time.monotonic()>=deadline:
                    uncertain=True
                    stop_tree(False)
                    try:child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        stop_tree();child.wait()
                    break
                try:child.wait(timeout=min(.25,max(.001,deadline-time.monotonic())))
                except subprocess.TimeoutExpired:pass
        finally:
            # Closing containment also removes descendants that outlived a
            # normally exiting parent; they cannot hold pipes or mutate later.
            close_tree()
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

    def __init__(self, project, root, primary, *, env=None, command_run=None, timeout_seconds=None):
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
        self.timeout_seconds = timeout_seconds
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
            self.env.pop(name, None)

    def _call(self, module, arguments, request=None):
        payload = None if request is None else json.dumps(request, allow_nan=False, separators=(',', ':'))
        if payload is not None and len(payload.encode('utf-8')) > self.MAX_BYTES:
            raise ValueError('request exceeds transport bound')
        command = [sys.executable, '-m', 'hermes_trading.' + module, *arguments]
        timeout = self.timeout_seconds() if self.timeout_seconds is not None else 30
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError('bounded receiver timeout required')
        failure = None
        try:
            result = self.command_run(command, cwd=self.project, env=self.env.copy(),
                input=payload, capture_output=True, text=True, encoding='utf-8',
                errors='strict', timeout=timeout,
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
