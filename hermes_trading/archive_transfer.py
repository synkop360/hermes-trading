"""Authenticated bounded archive-read protocol and resumable transfer.

No socket listener, Railway command, installed key or retirement endpoint is
provided. An exchange adapter must enforce the supplied response/time limits;
the client also verifies every returned byte against the pinned content root.
"""
import base64
from datetime import datetime
import json
import re
import time
import uuid

from hermes_trading import archive_store as store
from hermes_trading.control_auth import sign_command, verify_command
from hermes_trading.reflection_supervision import _hash, _json, _now

PURPOSE = 'paired_archive_read_v1'
MAX_REQUEST_BYTES = 8192


def _secret(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 4096:
        raise ValueError('archive authentication key is unavailable')


def _request(body, experiment):
    if not isinstance(body, str) or len(body.encode()) > MAX_REQUEST_BYTES:
        raise ValueError('archive read request exceeds bound')
    try:
        value = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise ValueError('archive read request malformed') from error
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'purpose', 'experiment_id', 'kind', 'sha256'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['purpose'] != PURPOSE or value['experiment_id'] != experiment or
            not isinstance(value['kind'], str) or value['kind'] not in {'manifest', 'object'} or _json(value) != body):
        raise ValueError('archive read scope differs')
    store._digest(value['sha256'])
    return value


def _raw_limit(kind):
    return store.MAX_MANIFEST_BYTES if kind == 'manifest' else store.CHUNK_BYTES + 131072


class ReadServer:
    def __init__(self, archive, experiment, secret, nonce_store, *, enabled=False):
        self.archive = archive; self.experiment = experiment; self.secret = secret
        self.nonce_store = nonce_store; self.enabled = enabled

    def handle(self, envelope, *, now):
        if self.enabled is not True:
            raise ValueError('archive read server is disabled')
        _secret(self.secret)
        if (not isinstance(envelope, dict) or set(envelope) != {'body', 'nonce', 'timestamp', 'signature'} or
                not isinstance(envelope['nonce'], str) or re.fullmatch('[0-9a-f]{32}', envelope['nonce']) is None):
            raise ValueError('archive read envelope differs')
        request = _request(envelope['body'], self.experiment)
        store._digest(envelope['signature'])
        try:
            at = datetime.fromisoformat(envelope['timestamp'])
            if at.tzinfo is None or not 0 <= (_now(now) - at).total_seconds() < 300:
                raise ValueError('archive read validity differs')
        except (TypeError, AttributeError) as error:
            raise ValueError('archive read validity differs') from error
        if not verify_command(self.secret, envelope['nonce'], at, envelope['body'], envelope['signature'], self.nonce_store, now=now):
            raise ValueError('archive read authentication failed')
        archive = store._root(self.archive); digest = request['sha256']; kind = request['kind']
        path = archive / ('manifests' if kind == 'manifest' else 'objects') / (digest + '.json' if kind == 'manifest' else digest)
        raw = store._read(path, _raw_limit(kind))
        if kind == 'manifest':
            parsed = store.manifest_bytes(raw, digest)
            if parsed['experiment'] != self.experiment:
                raise ValueError('archive manifest experiment differs')
        elif store._sha(store._inflate(raw)) != digest:
            raise ValueError('archive object digest differs')
        return {'schema_version': 1, 'kind': kind, 'sha256': digest, 'request_sha256': _hash(envelope),
                'data_base64': base64.b64encode(raw).decode('ascii')}


class ReadClient:
    def __init__(self, experiment, secret, exchange, *, now=None):
        self.experiment = experiment; self.secret = secret; self.exchange = exchange
        self.now = now or _now; self.total_wire_bytes = 0

    def get(self, kind, digest, *, timeout_seconds, max_wire_bytes):
        _secret(self.secret)
        body = _json({'schema_version': 1, 'purpose': PURPOSE, 'experiment_id': self.experiment,
                      'kind': kind, 'sha256': digest})
        _request(body, self.experiment)
        if not 0 < timeout_seconds <= 120 or type(max_wire_bytes) is not int or max_wire_bytes <= 0:
            raise ValueError('archive exchange bounds differ')
        at = _now(self.now()); nonce = uuid.uuid4().hex
        envelope = {'body': body, 'nonce': nonce, 'timestamp': at.isoformat(),
                    'signature': sign_command(self.secret, nonce, at, body)}
        request_bytes = len(_json(envelope).encode())
        max_response_bytes = max_wire_bytes - request_bytes
        if max_response_bytes <= 0:
            raise ValueError('archive request exceeds wire budget')
        # Count serialized request and response JSON; this is not a TCP/SSH quota.
        self.total_wire_bytes += request_bytes
        response = self.exchange(envelope, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes)
        if (not isinstance(response, dict) or set(response) != {'schema_version', 'kind', 'sha256', 'request_sha256', 'data_base64'} or
                type(response['schema_version']) is not int or response['schema_version'] != 1 or
                response['kind'] != kind or response['sha256'] != digest or response['request_sha256'] != _hash(envelope)):
            raise ValueError('archive response binding differs')
        encoded = response['data_base64']; limit = _raw_limit(kind)
        if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
            raise ValueError('archive response exceeds bound')
        size = len(_json(response).encode())
        self.total_wire_bytes += size
        if size > max_response_bytes:
            raise ValueError('archive response exceeds wire budget')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError('archive response encoding differs') from error
        if len(raw) > limit:
            raise ValueError('archive response exceeds decoded bound')
        if kind == 'manifest':
            parsed = store.manifest_bytes(raw, digest)
            if parsed['experiment'] != self.experiment:
                raise ValueError('archive manifest experiment differs')
        elif store._sha(store._inflate(raw)) != digest:
            raise ValueError('archive object digest differs')
        return raw


def transfer(client, destination, digest, *, enabled=False, max_wire_bytes,
             timeout_seconds, clock=time.monotonic, fault=None):
    """Copy and fully verify an exact pinned chain, resuming valid local chunks.

    Immutable manifests publish last. A successful copy is neither an
    independent-backup certificate nor permission to retire source evidence.
    """
    if enabled is not True:
        raise ValueError('archive transfer is disabled')
    store._digest(digest)
    if type(max_wire_bytes) is not int or not 0 < max_wire_bytes <= 1024**4:
        raise ValueError('archive wire budget differs')
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120:
        raise ValueError('archive transfer deadline differs')
    destination = store._root(destination); started = clock(); deadline = started + timeout_seconds
    initial_bytes = client.total_wire_bytes
    def check():
        if clock() >= deadline:
            raise TimeoutError('archive transfer deadline exceeded')
    def remote(kind, value):
        check(); remaining = max_wire_bytes - (client.total_wire_bytes - initial_bytes)
        if remaining <= 0:
            raise ValueError('archive wire budget exhausted')
        raw = client.get(kind, value, timeout_seconds=deadline-clock(),
            max_wire_bytes=min(remaining, 2 * _raw_limit(kind) + MAX_REQUEST_BYTES))
        check()
        return raw
    chain = []; seen = set(); current = digest
    while current is not None:
        check()
        if current in seen or len(chain) >= 1024:
            raise ValueError('archive predecessor chain exceeds bound or cycles')
        seen.add(current)
        path = destination / 'manifests' / (current + '.json')
        raw = store._read(path, store.MAX_MANIFEST_BYTES) if path.exists() else remote('manifest', current)
        value = store.manifest_bytes(raw, current, check=check)
        if value['experiment'] != client.experiment:
            raise ValueError('archive transfer experiment differs')
        chain.append((current, raw, value)); current = value['parent']
    chain.reverse(); store.check_chain([value for _, _, value in chain])
    new_objects = 0; verified = set()
    for _, _, value in chain:
        for row in value['files']:
            for obj in row['objects']:
                check(); path = destination / 'objects' / obj
                if obj in verified:
                    continue
                if path.exists():
                    store._object(destination, obj)
                    store.sync_file(path); store._sync_directory(path.parent)
                    verified.add(obj)
                    continue
                raw = remote('object', obj)
                store._publish(path, raw); store._object(destination, obj); new_objects += 1
                verified.add(obj)
                if fault:
                    fault('after_object')
    for value_digest, raw, _ in chain:
        store.manifest_bytes(raw, value_digest, object_reader=lambda obj: store._object(destination, obj), check=check)
    for value_digest, raw, _ in chain:
        check()
        if fault:
            fault('before_manifest')
        store._publish(destination / 'manifests' / (value_digest + '.json'), raw)
    return {'manifest_sha256': digest, 'chain_length': len(chain), 'new_objects': new_objects,
            'wire_bytes': client.total_wire_bytes - initial_bytes, 'elapsed_seconds': clock()-started,
            'local_directory_fsync_supported': store.directory_fsync_supported(),
            'retirement_authorized': False, 'independent_backup_verified': False}
