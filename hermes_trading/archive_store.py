"""Bounded immutable evidence object store. No network or retirement API."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import zlib

CHUNK_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 128 * CHUNK_BYTES
MAX_MANIFEST_BYTES = 4 * CHUNK_BYTES
MAX_FILES = 4096


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('invalid content digest')
    return value


def _root(path):
    if '..' in Path(path).parts:
        raise ValueError('parent traversal is outside archive contract')
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symlink or reparse path is outside archive contract')
    return path


def _read(path, limit):
    path = _root(path)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('evidence must be a bounded regular file')
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('evidence grew beyond bound')
        return raw


def _name(value):
    if not isinstance(value, str) or len(value) > 200 or not value:
        raise ValueError('invalid evidence path')
    parts = value.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(i) for i in range(1, 10)),
                *('LPT' + str(i) for i in range(1, 10))}
    if any(not re.fullmatch('[A-Za-z0-9_.-]+', p) or p in ('.', '..') or p.endswith('.')
           or p.split('.')[0].upper() in reserved for p in parts):
        raise ValueError('unsafe or nonportable evidence path')
    if PurePosixPath(value).is_absolute():
        raise ValueError('absolute evidence path')
    return value


def _inflate(raw):
    if len(raw) > CHUNK_BYTES + 131072:
        raise ValueError('compressed object exceeds bound')
    try:
        decoder = zlib.decompressobj()
        data = decoder.decompress(raw, CHUNK_BYTES + 1)
        if len(data) > CHUNK_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('oversized, incomplete or trailing object')
        return data
    except zlib.error as error:
        raise ValueError('corrupt compressed object') from error


def _object(archive, digest):
    path = _root(archive / 'objects' / _digest(digest))
    try:
        raw = _inflate(_read(path, CHUNK_BYTES + 131072))
    except OSError as error:
        raise ValueError('missing or unreadable evidence object') from error
    if _sha(raw) != digest:
        raise ValueError('evidence object digest differs')
    return raw


def _publish(path, raw):
    path = _root(path)
    missing = []; parent = path.parent
    while not parent.exists():
        missing.append(parent); parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _sync_directory(directory); _sync_directory(directory.parent)
    if path.exists():
        if _read(path, len(raw)) != raw:
            raise ValueError('immutable evidence differs')
        sync_file(path); _sync_directory(path.parent)
        return
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if _read(temporary, len(raw)) != raw:
        raise ValueError('temporary evidence verification failed')
    os.replace(temporary, path)
    _sync_directory(path.parent)


def directory_fsync_supported():
    """Capability only; Windows metadata durability is explicitly unverified."""
    return os.name == 'posix'


def _sync_directory(path):
    if not directory_fsync_supported():
        return False
    descriptor = os.open(_root(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def sync_file(path):
    flags = (os.O_RDONLY if os.name == 'posix' else os.O_RDWR)
    flags |= getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(_root(path), flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('archive sync target is not a regular file')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest(archive, digest):
    path = _root(archive / 'manifests' / (_digest(digest) + '.json'))
    try:
        raw = _read(path, MAX_MANIFEST_BYTES)
    except OSError as error:
        raise ValueError('missing or malformed manifest') from error
    return manifest_bytes(raw, digest, object_reader=lambda obj: _object(archive, obj))


def manifest_bytes(raw, digest, *, object_reader=None, check=lambda: None):
    """Parse bounded metadata; verify file content only with an object reader.

    Metadata-only parsing is transfer preflight, never a verified-copy receipt.
    """
    _digest(digest); check()
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES or _sha(raw) != digest:
        raise ValueError('manifest digest/size differs')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('malformed archive manifest') from error
    if not isinstance(value, dict) or set(value) != {'schema_version', 'purpose', 'experiment',
            'first_batch', 'last_batch', 'parent', 'files'}:
        raise ValueError('manifest fields differ')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['purpose'] != 'paired_evidence_snapshot_v1':
        raise ValueError('unsupported manifest')
    if not isinstance(value['experiment'], str) or not value['experiment'].strip() or len(value['experiment']) > 200:
        raise ValueError('invalid experiment')
    if any(type(value[k]) is not int for k in ('first_batch', 'last_batch')) or not 1 <= value['first_batch'] <= value['last_batch']:
        raise ValueError('invalid batch range')
    if value['parent'] is not None:
        _digest(value['parent'])
    files = value['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('invalid file inventory')
    seen = set(); total = 0
    for row in files:
        check()
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256', 'objects'}:
            raise ValueError('invalid file record')
        name = _name(row['path'])
        if name.casefold() in seen:
            raise ValueError('duplicate or case-colliding evidence path')
        seen.add(name.casefold())
        if type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_TOTAL_BYTES:
            raise ValueError('invalid decoded file size')
        total += row['bytes']
        if total > MAX_TOTAL_BYTES:
            raise ValueError('snapshot exceeds decoded budget')
        objects = row['objects']
        _digest(row['sha256'])
        if not isinstance(objects, list) or len(objects) != (row['bytes'] + CHUNK_BYTES - 1) // CHUNK_BYTES:
            raise ValueError('invalid object count')
        hasher = hashlib.sha256(); size = 0
        for index, obj in enumerate(objects):
            check(); _digest(obj)
            if object_reader is None:
                continue
            chunk = object_reader(obj)
            expected = min(CHUNK_BYTES, row['bytes'] - index * CHUNK_BYTES)
            if len(chunk) != expected:
                raise ValueError('object length differs')
            hasher.update(chunk); size += len(chunk)
        if object_reader is not None and (size != row['bytes'] or hasher.hexdigest() != row['sha256']):
            raise ValueError('reconstructed file differs')
    return value


def validate(archive, digest):
    """Validate complete predecessor evidence, not just the latest manifest."""
    _digest(digest)
    archive = _root(archive); chain = []; seen = set()
    while digest is not None:
        if digest in seen or len(chain) >= 1024:
            raise ValueError('cyclic or oversized archive chain')
        seen.add(digest)
        value = _manifest(archive, digest)
        chain.append(value); digest = value['parent']
    chain.reverse()
    check_chain(chain)
    return chain


def check_chain(chain):
    """Check coverage of already parsed manifests, without asserting content."""
    if not isinstance(chain, list) or not 1 <= len(chain) <= 1024:
        raise ValueError('archive chain exceeds bound')
    if chain[0]['first_batch'] != 1:
        raise ValueError('initial batch range must start at one')
    for previous, current in zip(chain, chain[1:]):
        if current['experiment'] != previous['experiment'] or current['first_batch'] != previous['last_batch'] + 1:
            raise ValueError('experiment or consecutive batch coverage differs')


def seal(source, archive, inventory, *, experiment, first_batch, last_batch, parent=None, fault=None):
    """Seal an explicit quiescent export. Caller certifies its completed-cohort cut; no pruning."""
    source = _root(source); archive = _root(archive)
    if source == archive or source in archive.parents or archive in source.parents:
        raise ValueError('source and archive must be separate directories')
    if not isinstance(inventory, (list, tuple)) or not 1 <= len(inventory) <= MAX_FILES:
        raise ValueError('invalid inventory')
    names = [_name(name) for name in inventory]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('duplicate or case-colliding inventory')
    if any(p.stat().st_size for p in source.rglob('*-wal') if p.is_file()):
        raise ValueError('uncheckpointed WAL: source is not a sealed offline fixture')
    if type(first_batch) is not int or type(last_batch) is not int or not 1 <= first_batch <= last_batch:
        raise ValueError('invalid batch range')
    if not isinstance(experiment, str) or not experiment.strip() or len(experiment) > 200:
        raise ValueError('invalid experiment')
    if parent is None:
        if first_batch != 1:
            raise ValueError('initial batch range must start at one')
    else:
        previous = validate(archive, parent)[-1]
        if previous['experiment'] != experiment or first_batch != previous['last_batch'] + 1:
            raise ValueError('experiment or consecutive batch coverage differs')
    files = []; total = 0
    for name in sorted(names):
        path = _root(source / name)
        if not path.is_file():
            raise ValueError('missing source file')
        size = path.stat().st_size; total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError('snapshot exceeds decoded budget')
        digest = hashlib.sha256(); objects = []; read = 0
        with path.open('rb') as stream:
            while chunk := stream.read(CHUNK_BYTES):
                read += len(chunk)
                if read > size:
                    raise ValueError('source changed during seal')
                obj = _sha(chunk); digest.update(chunk); objects.append(obj)
                _publish(archive / 'objects' / obj, zlib.compress(chunk, 9))
                if _object(archive, obj) != chunk:
                    raise ValueError('stored evidence differs')
                if fault: fault('after_object')
        if read != size or path.stat().st_size != size:
            raise ValueError('source changed during seal')
        files.append({'path': name, 'bytes': size, 'sha256': digest.hexdigest(), 'objects': objects})
    value = {'schema_version': 1, 'purpose': 'paired_evidence_snapshot_v1', 'experiment': experiment,
             'first_batch': first_batch, 'last_batch': last_batch, 'parent': parent, 'files': files}
    raw = _json(value)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('manifest exceeds bound')
    if fault: fault('before_manifest')
    result = _sha(raw)
    _publish(archive / 'manifests' / (result + '.json'), raw)
    validate(archive, result)
    return result


def restore(archive, digest, target, *, fault=None):
    archive = _root(archive); target = _root(target)
    if target.exists() or archive == target or archive in target.parents or target in archive.parents:
        raise ValueError('restore requires a separate new target')
    chain = validate(archive, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.staging')
    staging.mkdir()
    for row in chain[-1]['files']:
        path = staging / row['path']; path.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256(); size = 0
        with path.open('xb') as stream:
            for obj in row['objects']:
                raw = _object(archive, obj); stream.write(raw); hasher.update(raw); size += len(raw)
            stream.flush(); os.fsync(stream.fileno())
        if size != row['bytes'] or hasher.hexdigest() != row['sha256']:
            raise ValueError('restored file differs')
    if fault: fault('before_restore_publish')
    if target.exists():
        raise ValueError('restore target appeared during staging')
    staging.rename(target)
    return {'manifest_sha256': digest, 'chain_length': len(chain), 'files': len(chain[-1]['files']),
            'restored_bytes': sum(row['bytes'] for row in chain[-1]['files']),
            'remote_retirement_authorized': False, 'independent_backup_verified': False}
